"""Which context influenced this tool call?

The question the tool interlock actually turns on. A content filter asks *what does the
call say*; this asks *where did the instruction come from*. Those give different answers
on the case that matters: a perfectly ordinary-looking ``send_email`` whose recipient
was dictated by a hidden line of white text in an uploaded PDF.

The taint lattice is ordered by how much an attacker controls it::

    system < user < retrieved_verified < retrieved_untrusted < tool_external

and joining is a max. Labels are attached at ingestion (``retrieval/corpus.py``) and
never re-derived here -- a label guessed at read time makes the join a guess.

**Two tiers, per ADR-007.** The honest attribution is tier 1: an argument value that
appears in a fragment is evidence *that* fragment influenced *that* argument. But a
model can paraphrase, translate or arithmetic-shift an instruction until no argument
matches any passage, and an attacker who knows the matcher will do exactly that. So
when tier 1 finds nothing, tier 2 does not conclude "clean" -- it takes the worst label
present in the turn's context at all. Tier 2 over-taints on purpose. The cost of that
is priced: it lands on the intervention ladder like everything else, so an over-taint
on a *reversible* tool still passes rather than freezing.

The one thing this module must never do is return a *lower* taint than the truth.
Everything else is a tuning question.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from interlock.core.types import PROVENANCE_ORDER, Fragment, Provenance, max_provenance

__all__ = [
    "MIN_SINGLE_TOKEN_CHARS",
    "MIN_TOKENS_FOR_OVERLAP",
    "TIER1_OVERLAP_THRESHOLD",
    "TaintVerdict",
    "ToolCall",
    "argument_strings",
    "conversation_taint",
    "influencing_taint",
    "tool_calls_from_delta",
]

#: How much of an argument's token content must appear in a fragment for tier 1 to call
#: it a match. High on purpose: this tier exists to be *precise*, and a loose threshold
#: turns every call into a match on the largest passage in context, which would make the
#: tier-1/tier-2 distinction meaningless.
TIER1_OVERLAP_THRESHOLD = 0.9

#: Below this many tokens, only the contiguous-phrase arm can match. Token overlap over
#: one or two tokens is noise -- ``{"currency": "INR"}`` would "match" every document
#: mentioning rupees, and every call would come back tainted for no reason.
MIN_TOKENS_FOR_OVERLAP = 3

#: A one-token argument must be at least this long to be traceable at all. "5", "IN"
#: and "en" occur somewhere in almost any passage; treating that as influence is how
#: the entire corpus becomes untrusted and the interlock stops discriminating.
MIN_SINGLE_TOKEN_CHARS = 4

_TOKEN = re.compile(r"[a-z0-9]+(?:[.@][a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    """Lowercase tokens, keeping emails and decimals whole -- both are tool arguments."""
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One requested tool invocation, normalised out of whatever the provider sent."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""

    @property
    def digest_source(self) -> str:
        """Stable text form, for the loop breaker's repeat detection (D3-A5)."""
        items = sorted((str(k), str(v)) for k, v in self.arguments.items())
        return self.name + "|" + "|".join(f"{k}={v}" for k, v in items)


@dataclass(frozen=True, slots=True)
class TaintVerdict:
    """The provenance attributed to a call, and enough to explain it to a reviewer."""

    taint: Provenance
    #: 1 = an argument was traced to a specific fragment. 2 = conservative fallback.
    tier: int
    #: doc_ids of the fragments that produced this verdict.
    matched_doc_ids: tuple[str, ...] = ()
    #: Argument names that matched, tier 1 only.
    matched_arguments: tuple[str, ...] = ()
    rationale: str = ""

    @property
    def untrusted(self) -> bool:
        return PROVENANCE_ORDER.index(self.taint) >= PROVENANCE_ORDER.index("retrieved_untrusted")


def argument_strings(value: Any, *, _depth: int = 0) -> list[str]:
    """Flatten an argument value to the strings worth matching against.

    Numbers are included as strings: an amount dictated by a poisoned document is the
    single most important argument to trace, and it arrives as an int.
    """
    if _depth > 6:  # a self-referential arguments blob should not hang the request
        return []
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str | int | float):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, dict):
        return [s for v in value.values() for s in argument_strings(v, _depth=_depth + 1)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in argument_strings(v, _depth=_depth + 1)]
    return []


def _matches(argument: str, fragment_text: str) -> bool:
    """Does this argument value appear in this passage?

    Matching is on **token boundaries**, never raw substring. A raw ``in`` test looks
    obviously right and is not: ``{"currency": "en"}`` matches "prepaym*en*t", so a
    two-letter locale code traces to every document in the corpus and tier 1 stops
    meaning anything. Both arms below compare token sequences for that reason.
    """
    argument = argument.strip()
    if not argument:
        return False

    argument_tokens = _tokens(argument)
    if not argument_tokens:
        return False
    fragment_tokens = _tokens(fragment_text)

    # A single short token is not evidence of anything. "5" or "IN" will occur
    # somewhere in almost any passage, and calling that influence is how the whole
    # corpus becomes untrusted.
    if len(argument_tokens) == 1 and len(argument_tokens[0]) < MIN_SINGLE_TOKEN_CHARS:
        return False

    # Arm 1: the argument appears as a contiguous phrase. This is what catches an email
    # address, an account number or a quoted sentence lifted from the document.
    width = len(argument_tokens)
    for start in range(len(fragment_tokens) - width + 1):
        if fragment_tokens[start : start + width] == argument_tokens:
            return True

    # Arm 2: most of a longer argument's tokens are present, in any order. This is the
    # paraphrase-resistant arm, and it needs enough tokens to be meaningful.
    if width < MIN_TOKENS_FOR_OVERLAP:
        return False
    present = set(fragment_tokens)
    hits = sum(1 for token in argument_tokens if token in present)
    return hits / width >= TIER1_OVERLAP_THRESHOLD


def influencing_taint(
    call: ToolCall,
    fragments: Sequence[Fragment],
    *,
    conversation_taint: Provenance = "system",
) -> TaintVerdict:
    """Attribute a provenance label to ``call``.

    ``conversation_taint`` carries forward what earlier turns already established.
    Taint does not expire at a turn boundary: a poisoned document read on turn two is
    still what motivated the tool call the model makes on turn four, and a lattice that
    forgets is a lattice an attacker only has to wait out.
    """
    if not fragments:
        return TaintVerdict(
            taint=conversation_taint,
            tier=2,
            rationale="no retrieved context this turn; carried the conversation's taint",
        )

    # -- tier 1: trace each argument to a specific passage --------------------
    matched_fragments: list[Fragment] = []
    matched_arguments: list[str] = []
    for name, value in call.arguments.items():
        for argument in argument_strings(value):
            hits = [f for f in fragments if _matches(argument, f.text)]
            if hits:
                matched_fragments.extend(hits)
                matched_arguments.append(name)
                break

    if matched_fragments:
        taint = max_provenance([f.provenance for f in matched_fragments] + [conversation_taint])
        doc_ids = tuple(dict.fromkeys(f.doc_id or "?" for f in matched_fragments))
        arguments = tuple(dict.fromkeys(matched_arguments))
        return TaintVerdict(
            taint=taint,
            tier=1,
            matched_doc_ids=doc_ids,
            matched_arguments=arguments,
            rationale=(
                f"argument(s) {', '.join(arguments)} trace to {', '.join(doc_ids)} "
                f"(provenance {taint})"
            ),
        )

    # -- tier 2: nothing traced, so assume the worst thing in the room --------
    #
    # This is the branch that catches a paraphrased instruction, and the branch an
    # attacker who defeats tier 1 lands in. It must not read as "clean".
    untrusted = [f for f in fragments if str(f.provenance).endswith("untrusted")]
    if untrusted:
        taint = max_provenance([f.provenance for f in untrusted] + [conversation_taint])
        doc_ids = tuple(dict.fromkeys(f.doc_id or "?" for f in untrusted))
        return TaintVerdict(
            taint=taint,
            tier=2,
            matched_doc_ids=doc_ids,
            rationale=(
                f"no argument traced to a passage, but {len(untrusted)} untrusted "
                f"fragment(s) were in context ({', '.join(doc_ids)}); assumed influential"
            ),
        )

    taint = max_provenance([f.provenance for f in fragments] + [conversation_taint])
    return TaintVerdict(
        taint=taint,
        tier=2,
        rationale=f"no untrusted context this turn; highest label present was {taint}",
    )


def tool_calls_from_delta(delta: dict[str, Any] | None) -> list[ToolCall]:
    """Read tool calls out of an OpenAI-shaped message or streaming delta.

    Tolerant: a malformed ``arguments`` blob yields a call with no arguments rather than
    raising. A tool call we cannot parse must still be *seen* -- dropping it would let a
    malformed call through the interlock entirely, which is the wrong way to fail.
    """
    if not delta:
        return []
    raw = delta.get("tool_calls")
    if not isinstance(raw, list):
        return []

    calls: list[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        parsed: dict[str, Any] = {}
        if isinstance(arguments, dict):
            parsed = arguments
        elif isinstance(arguments, str) and arguments.strip():
            try:
                loaded = json.loads(arguments)
                parsed = loaded if isinstance(loaded, dict) else {"_": loaded}
            except json.JSONDecodeError:
                parsed = {"_unparsed": arguments}
        calls.append(ToolCall(name=name, arguments=parsed, call_id=str(entry.get("id") or "")))
    return calls


def conversation_taint(
    fragments: Iterable[Fragment], *, prior: Provenance = "system"
) -> Provenance:
    """Fold this turn's fragments into the running conversation taint."""
    return max_provenance([f.provenance for f in fragments] + [prior])
