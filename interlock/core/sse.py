"""Contract 3 — the SSE wire format (Implementation03 §4).

FROZEN. We stay OpenAI-compatible on the ``data:`` channel so any SDK works untouched.
Interlock metadata rides on **named** SSE events:

    event: interlock.stakes
    data: {"impact_inr":40000,"reversibility":"costly","domain":"loan_terms","mode":"buffered"}

    data: {"id":"chatcmpl-...","choices":[{"delta":{"content":"Under your agreement, "}}]}

    event: interlock.decision
    data: {"decision_id":"dec_...","sentence_idx":2,"action":"L2_repair",...}

    data: [DONE]

``counterfactual`` on the decision event is what makes the demo land: the console renders
"what would have shipped" in red beside what actually did.

**A compatibility caveat we do not paper over.** The frozen contract asserts that standard
clients ignore named events. That is true of the EventSource spec and of raw SSE readers,
but it is *not* universally true of SDK stream decoders — some cast every ``data:`` payload
to a chunk type regardless of the event name. So the emission is gated by
``StreamOptions`` (default: on, per the contract) and any client may opt out with the
``X-Interlock-Events: off`` request header. This is an additive escape hatch, not a change
to the wire format. ``tests/contract/test_openai_compat.py`` verifies the claim against a
real SDK rather than trusting it.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from interlock.core.types import Action, GateMode, Reversibility

__all__ = [
    "DONE",
    "EVENT_DECISION",
    "EVENT_HOLD",
    "EVENT_SIGNAL",
    "EVENT_STAKES",
    "INTERLOCK_EVENTS",
    "DecisionEvent",
    "HoldEvent",
    "HoldKind",
    "SignalEvent",
    "StakesEvent",
    "format_data",
    "format_done",
    "format_event",
]

EVENT_STAKES = "interlock.stakes"
EVENT_SIGNAL = "interlock.signal"
EVENT_DECISION = "interlock.decision"
EVENT_HOLD = "interlock.hold"

#: Every named event we are allowed to emit. A name not in this tuple is a contract break.
INTERLOCK_EVENTS: tuple[str, ...] = (
    EVENT_STAKES,
    EVENT_SIGNAL,
    EVENT_DECISION,
    EVENT_HOLD,
)

#: The OpenAI stream terminator, byte-for-byte.
DONE = "data: [DONE]\n\n"

HoldKind = Literal["response", "tool_call"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StakesEvent(_Event):
    """Emitted once, before the upstream call, so the console can show the estimate
    that drove *both* the routing decision and the scrutiny budget."""

    impact_inr: float
    reversibility: Reversibility
    domain: str
    mode: GateMode
    stakes_id: str = ""  # ties router and risk engine to one estimate (Contribution 1)
    route_reason: str | None = None  # 'stakes_high' | 'router_mf' | 'cache_hit' | 'pinned'
    model_served: str | None = None


class SignalEvent(_Event):
    """One calibrated signal for one sentence. ``prob``, never ``raw`` — the console
    must not display an uncalibrated score as though it meant something."""

    sentence_idx: int
    name: str
    prob: float | None = None


class DecisionEvent(_Event):
    """The action taken for one sentence, and what would have shipped without it."""

    decision_id: str
    sentence_idx: int
    action: Action
    chosen_loss: float
    runner_up: Action | None = None
    margin: float = 0.0
    counterfactual: str | None = None  # the text that would have shipped
    hard_rule: str | None = None  # set when a deterministic rule fired
    degraded: bool = False


class HoldEvent(_Event):
    """A durable pending state was created. Survives a restart (F6/F7)."""

    hold_id: str
    kind: HoldKind
    reason: str
    tool: str | None = None
    sentence_idx: int | None = None


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


def format_data(payload: str | bytes | dict[str, Any]) -> str:
    """Frame an unnamed ``data:`` line — the OpenAI-compatible channel.

    Passthrough chunks are forwarded as the upstream serialised them; re-encoding JSON
    would be a needless way to break byte-compatibility with a client's parser.
    """
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if not isinstance(payload, str):
        payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"data: {payload}\n\n"


def format_event(name: str, payload: BaseModel | dict[str, Any]) -> str:
    """Frame a named Interlock event.

    Raises on an unknown name: an event the console does not know how to render is a
    contract break, and it should fail here rather than silently on stage.
    """
    if name not in INTERLOCK_EVENTS:
        raise ValueError(
            f"unknown Interlock SSE event: {name!r}; expected one of {INTERLOCK_EVENTS}"
        )
    body = (
        payload.model_dump_json()
        if isinstance(payload, BaseModel)
        else json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )
    return f"event: {name}\ndata: {body}\n\n"


def format_done() -> str:
    return DONE


class StreamOptions(BaseModel):
    """Per-request switches for what rides on the stream.

    ``emit_interlock_events`` defaults to True (the frozen contract). A client that
    cannot tolerate named events sets ``X-Interlock-Events: off``; it then receives a
    pure OpenAI stream, and the console reads the same decisions over its websocket
    instead. Nothing about the enforcement behaviour changes either way.
    """

    model_config = ConfigDict(extra="forbid")

    emit_interlock_events: bool = True
    events: tuple[str, ...] = Field(default=INTERLOCK_EVENTS)

    def allows(self, name: str) -> bool:
        return self.emit_interlock_events and name in self.events
