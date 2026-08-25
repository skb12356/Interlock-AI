"""Contract 3 — the SSE wire format (Implementation03 §4).

The gate must never drop or mangle a token, and a standard OpenAI client must be able
to read the stream while ignoring everything Interlock adds to it. These tests pin the
framing; `test_openai_compat.py` (D1-A1) proves the compatibility claim against a real SDK.
"""

from __future__ import annotations

import json

import pytest

from interlock.core.sse import (
    DONE,
    EVENT_DECISION,
    EVENT_HOLD,
    EVENT_SIGNAL,
    EVENT_STAKES,
    INTERLOCK_EVENTS,
    DecisionEvent,
    HoldEvent,
    SignalEvent,
    StakesEvent,
    StreamOptions,
    format_data,
    format_done,
    format_event,
)

# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


def test_data_frame_is_openai_shaped() -> None:
    chunk = '{"id":"chatcmpl-1","choices":[{"delta":{"content":"Under your agreement, "}}]}'
    assert format_data(chunk) == f"data: {chunk}\n\n"


def test_passthrough_chunks_are_not_re_encoded() -> None:
    """Re-serialising a provider's JSON is a needless way to break a client's parser.
    Whitespace and key order must survive exactly as the upstream sent them."""
    upstream = '{"id": "chatcmpl-1",  "choices": [ {"delta": {"content": "hi"}} ]}'
    assert format_data(upstream) == f"data: {upstream}\n\n"


def test_data_frame_accepts_bytes_from_the_upstream_reader() -> None:
    assert format_data(b'{"a":1}') == 'data: {"a":1}\n\n'


def test_done_terminator_is_byte_exact() -> None:
    assert format_done() == "data: [DONE]\n\n"
    assert DONE == "data: [DONE]\n\n"


def test_named_event_framing() -> None:
    frame = format_event(
        EVENT_SIGNAL, SignalEvent(sentence_idx=2, name="minicheck_support", prob=0.87)
    )
    head, body, blank_a, blank_b = frame.split("\n")
    assert head == f"event: {EVENT_SIGNAL}"
    assert body.startswith("data: ")
    assert (blank_a, blank_b) == ("", "")  # the blank line that dispatches the event
    assert json.loads(body[len("data: ") :])["prob"] == 0.87


def test_every_frame_ends_with_a_blank_line() -> None:
    """SSE dispatches on a blank line. A missing one means the client buffers forever --
    which on stage looks exactly like the demo freezing."""
    frames = [
        format_data('{"a":1}'),
        format_event(
            EVENT_STAKES,
            StakesEvent(impact_inr=1, reversibility="costly", domain="d", mode="buffered"),
        ),
        format_done(),
    ]
    assert all(frame.endswith("\n\n") for frame in frames)


def test_unknown_event_name_is_refused() -> None:
    """An event the console cannot render is a contract break; fail here, not on stage."""
    with pytest.raises(ValueError, match="unknown Interlock SSE event"):
        format_event("interlock.vibes", {"x": 1})


def test_the_four_event_names_are_frozen() -> None:
    assert INTERLOCK_EVENTS == (
        "interlock.stakes",
        "interlock.signal",
        "interlock.decision",
        "interlock.hold",
    )


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


def test_stakes_event_matches_the_documented_example() -> None:
    event = StakesEvent(
        impact_inr=40000, reversibility="costly", domain="loan_terms", mode="buffered"
    )
    payload = json.loads(event.model_dump_json())
    assert payload["impact_inr"] == 40000
    assert payload["mode"] == "buffered"


def test_stakes_event_carries_the_shared_estimate_id() -> None:
    """Contribution 1 must be provable from one trace: the router and the risk engine
    consumed the *same* estimate."""
    event = StakesEvent(
        impact_inr=40000,
        reversibility="costly",
        domain="loan_terms",
        mode="buffered",
        stakes_id="stk_1",
        route_reason="stakes_high",
        model_served="qwen3:8b",
    )
    assert event.stakes_id == "stk_1"
    assert event.route_reason == "stakes_high"


def test_decision_event_carries_the_counterfactual() -> None:
    """'What would have shipped' is the line the demo lands on."""
    event = DecisionEvent(
        decision_id="dec_1",
        sentence_idx=2,
        action="L2_repair",
        chosen_loss=2491.0,
        runner_up="L3_reroute",
        counterfactual="Clause 7.4 imposes a 2% prepayment penalty.",
    )
    assert json.loads(event.model_dump_json())["counterfactual"].startswith("Clause 7.4")


def test_signal_event_exposes_only_the_calibrated_probability() -> None:
    """The console must never render an uncalibrated score as if it meant something."""
    assert "raw" not in SignalEvent.model_fields
    assert "prob" in SignalEvent.model_fields


def test_hold_event_matches_the_documented_example() -> None:
    event = HoldEvent(
        hold_id="hld_1",
        kind="tool_call",
        tool="send_email",
        reason="irreversible x untrusted_provenance",
    )
    frame = format_event(EVENT_HOLD, event)
    assert frame.startswith("event: interlock.hold\n")
    assert json.loads(frame.split("data: ", 1)[1])["tool"] == "send_email"


# --------------------------------------------------------------------------- #
# The opt-out escape hatch (additive; the wire format itself is unchanged)
# --------------------------------------------------------------------------- #


def test_events_are_emitted_by_default() -> None:
    options = StreamOptions()
    assert all(options.allows(name) for name in INTERLOCK_EVENTS)


def test_a_client_can_opt_out_of_named_events() -> None:
    """Some SDK stream decoders cast every `data:` payload to a chunk type regardless of
    the event name. Such a client sends `X-Interlock-Events: off` and gets a pure OpenAI
    stream; the console reads the same decisions over its websocket instead."""
    options = StreamOptions(emit_interlock_events=False)
    assert not any(options.allows(name) for name in INTERLOCK_EVENTS)


def test_opting_out_does_not_change_enforcement() -> None:
    """The escape hatch governs what the *client* sees, never what the gate does."""
    assert "emit_interlock_events" in StreamOptions.model_fields
    assert set(StreamOptions.model_fields) == {"emit_interlock_events", "events"}


def test_a_client_can_subscribe_to_a_subset() -> None:
    options = StreamOptions(events=(EVENT_DECISION,))
    assert options.allows(EVENT_DECISION)
    assert not options.allows(EVENT_SIGNAL)
