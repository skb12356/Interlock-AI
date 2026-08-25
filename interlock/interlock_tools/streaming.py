"""Accumulating streamed tool calls so they can be judged before they escape.

A tool call does not arrive whole. OpenAI-shaped streams emit it as a run of deltas
keyed by ``index``, with the arguments dribbling out as JSON string fragments::

    {"tool_calls":[{"index":0,"id":"call_1","function":{"name":"send_email"}}]}
    {"tool_calls":[{"index":0,"function":{"arguments":"{\\"to\\":"}}]}
    {"tool_calls":[{"index":0,"function":{"arguments":"\\"a@b.example\\"}"}}]}

Which forces the design. There is no moment during the stream when the interlock could
evaluate a partial call -- ``{"to":`` is not an argument -- so **tool-call deltas are
withheld from the client entirely** until the stream ends and the calls assemble. This
is the commit gate's principle applied to a different payload: text streams one sentence
behind so a bad sentence can be repaired; tool calls stream one *call* behind so a bad
call can be frozen.

Withholding them costs nothing a customer notices. A client cannot execute half a tool
call either, so it was going to wait for the same terminator regardless; all that changes
is that Interlock gets to see the finished call first.

Content deltas in the same stream are **not** withheld. A response that both says
something and calls something keeps streaming its prose at full speed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from interlock.interlock_tools.provenance import ToolCall

__all__ = ["ToolCallAccumulator", "assemble_tool_calls"]


@dataclass
class _Partial:
    """One tool call under construction, identified by its stream index."""

    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    kind: str = "function"


@dataclass
class ToolCallAccumulator:
    """Absorbs tool-call deltas from a stream and assembles them at the end."""

    _partials: dict[int, _Partial] = field(default_factory=dict, init=False)
    #: True once any tool-call delta has been seen, even one that never completed.
    saw_any: bool = field(default=False, init=False)

    def absorb(self, chunk: dict[str, Any] | None) -> bool:
        """Take any tool-call deltas out of ``chunk``.

        Returns True if this chunk carried tool-call deltas and must therefore be
        **withheld** from the client. A chunk carrying both content and tool calls
        returns True as well: the content in such a chunk is part of the same
        assistant turn as the call, and releasing half of it while freezing the other
        half produces a message the client cannot interpret.
        """
        if not chunk:
            return False
        found = False
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            source = delta if isinstance(delta, dict) else choice.get("message")
            if not isinstance(source, dict):
                continue
            entries = source.get("tool_calls")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    self._absorb_one(entry)
                    found = True
        if found:
            self.saw_any = True
        return found

    def _absorb_one(self, entry: dict[str, Any]) -> None:
        # `index` is what ties fragments of the same call together. Providers that omit
        # it are emitting one call at a time, so index 0 is the honest default; using
        # something like len(partials) instead would start a new call per fragment and
        # produce a stream of one-character tool names.
        raw_index = entry.get("index")
        index = int(raw_index) if isinstance(raw_index, int) else 0
        partial = self._partials.setdefault(index, _Partial(index=index))

        if entry.get("id"):
            partial.call_id = str(entry["id"])
        if entry.get("type"):
            partial.kind = str(entry["type"])

        function = entry.get("function")
        if not isinstance(function, dict):
            return
        if function.get("name"):
            partial.name = str(function["name"])
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            partial.arguments += arguments
        elif isinstance(arguments, dict):
            # Some providers send the whole object once rather than streaming a string.
            partial.arguments = json.dumps(arguments)

    def assemble(self) -> list[ToolCall]:
        """The completed calls, in stream order."""
        return assemble_tool_calls(
            [
                {
                    "index": partial.index,
                    "id": partial.call_id,
                    "type": partial.kind,
                    "function": {"name": partial.name, "arguments": partial.arguments},
                }
                for partial in sorted(self._partials.values(), key=lambda p: p.index)
                if partial.name
            ]
        )

    def raw_entries(self) -> list[dict[str, Any]]:
        """The assembled deltas, in the wire shape, for replaying an approved call."""
        return [
            {
                "index": partial.index,
                "id": partial.call_id,
                "type": partial.kind,
                "function": {"name": partial.name, "arguments": partial.arguments},
            }
            for partial in sorted(self._partials.values(), key=lambda p: p.index)
            if partial.name
        ]

    @property
    def incomplete(self) -> list[int]:
        """Indices that received fragments but never a name.

        Reported rather than dropped: a truncated tool call means the stream died
        mid-call, and the client needs to know that rather than seeing nothing.
        """
        return [p.index for p in self._partials.values() if not p.name]


def assemble_tool_calls(entries: list[dict[str, Any]]) -> list[ToolCall]:
    """Turn wire-shaped entries into ``ToolCall`` objects, tolerating bad JSON."""
    calls: list[ToolCall] = []
    for entry in entries:
        function = entry.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        raw = function.get("arguments")
        arguments: dict[str, Any] = {}
        if isinstance(raw, dict):
            arguments = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                loaded = json.loads(raw)
                arguments = loaded if isinstance(loaded, dict) else {"_": loaded}
            except json.JSONDecodeError:
                # A call whose arguments did not parse is still a call. Treating it as
                # argument-free would let it past tier 1 with nothing to match on, so
                # the raw text is kept and matched as-is.
                arguments = {"_unparsed": raw}
        calls.append(ToolCall(name=name, arguments=arguments, call_id=str(entry.get("id") or "")))
    return calls
