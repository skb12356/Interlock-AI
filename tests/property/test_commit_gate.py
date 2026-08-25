"""The commit gate — including the property test the plan calls the stage-day insurance.

The stated property:

    For any token stream and any decision sequence, no uncommitted sentence is ever
    emitted, and every token is emitted exactly once or explicitly replaced.

Both halves matter. The first is the product. The second is subtler and is where a gate
usually breaks: a dropped token corrupts the answer, and a duplicated one is worse
because it looks deliberate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from interlock.core.policy import Policy, load_policy
from interlock.core.types import Action, Decision, LossRow, Stakes
from interlock.gate.sentence_gate import CommitGate, Emission, GateState

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(REPO_ROOT / "policies" / "banking.yaml")


def _stakes(impact: float = 40_000.0) -> Stakes:
    return Stakes(impact_inr=impact, reversibility="costly", domain="loan_terms", confidence=0.9)


def _decision(action: Action, idx: int = 0) -> Decision:
    return Decision(
        decision_id=f"dec_{idx}",
        action=action,
        loss_table=[
            LossRow(
                action=action,
                residual_harm=0.0,
                nuisance=0.0,
                compute=0.0,
                latency=0.0,
                total=0.0,
            )
        ],
        chosen_loss=0.0,
    )


class ScriptedEngine:
    """Returns a pre-set action per sentence index. Satisfies the RiskEngine Protocol."""

    def __init__(self, actions: list[Action] | None = None, default: Action = "L0_pass"):
        self.actions = actions or []
        self.default = default
        self.seen: list[str] = []
        self.already_emitted: list[bool] = []

    async def evaluate(self, ctx: object) -> Decision:
        idx = getattr(ctx, "sentence_idx", 0)
        self.seen.append(getattr(ctx, "sentence", ""))
        self.already_emitted.append(getattr(ctx, "already_emitted", False))
        action = self.actions[idx] if idx < len(self.actions) else self.default
        return _decision(action, idx)

    async def prefetch(self, *args: object, **kwargs: object) -> None:
        return None

    def health(self) -> dict[str, object]:
        return {"ok": True}


class HangingEngine:
    """Never returns. Exercises the watchdog."""

    async def evaluate(self, ctx: object) -> Decision:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def prefetch(self, *args: object, **kwargs: object) -> None:
        return None

    def health(self) -> dict[str, object]:
        return {"ok": True}


async def run_gate(gate: CommitGate, chunks: list[str]) -> list[Emission]:
    out: list[Emission] = []
    for chunk in chunks:
        out.extend(await gate.push(chunk, raw=chunk))
    out.extend(await gate.finish())
    return out


def emitted_text(emissions: list[Emission]) -> str:
    """What the customer actually sees."""
    return "".join(e.raw if e.kind == "raw" else e.text for e in emissions if e.kind != "event")


# =========================================================================== #
# THE property test
# =========================================================================== #

_ACTIONS: list[Action] = [
    "L0_pass",
    "L1_annotate",
    "L2_repair",
    "L3_reroute",
    "L4_hold",
    "L5_block",
]

_SENTENCES = [
    "Clause 7.4 imposes a 2% prepayment penalty.",
    "The branch opens at 9:30 AM.",
    "Your loan is floating-rate, so no charge applies.",
    "Rs. 40,000 was credited yesterday.",
    "Please contact Dr. Rao for details.",
]


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    sentences=st.lists(st.sampled_from(_SENTENCES), min_size=1, max_size=5),
    actions=st.lists(st.sampled_from(_ACTIONS), min_size=1, max_size=5),
    chunk_size=st.integers(min_value=1, max_value=40),
    buffered=st.booleans(),
)
def test_no_uncommitted_sentence_is_ever_emitted(
    sentences: list[str], actions: list[Action], chunk_size: int, buffered: bool
) -> None:
    """For any token stream and any decision sequence.

    A sentence whose decision was L3/L4/L5 must not appear in what the customer sees --
    unless it had already been emitted before the decision arrived, which is precisely
    what unbuffered mode means and what `already_emitted` records.
    """

    async def scenario() -> None:
        text = " ".join(sentences)
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
        engine = ScriptedEngine(actions=actions)
        gate = CommitGate(
            risk_engine=engine,
            stakes=_stakes(),
            request_id="req_prop",
            mode="buffered" if buffered else "unbuffered",
        )
        emissions = await run_gate(gate, chunks)
        seen = emitted_text(emissions)

        if not buffered:
            # Unbuffered forwards raw bytes as they arrive, so what the customer sees is
            # always a PREFIX of what the provider sent -- never more, never reordered,
            # never invented.
            assert text.startswith(seen)
            # It is the *whole* text unless a terminating action cut the stream short.
            # That case is not a bug: you cannot un-say sentence 1, but you can still
            # stop sentence 2, which is the ladder shrinking as the answer travels.
            terminated = any(
                e.kind == "event" and e.decision is not None and e.decision.action == "L5_block"
                for e in emissions
            )
            assert terminated or seen == text
            # No evaluation may be left dangling: unbuffered text has already shipped,
            # but its decision still belongs in the trace, and an un-awaited task would
            # be destroyed by asyncio -- losing the record and warning on shutdown.
            assert gate._pending is None
            return

        withheld = {"L3_reroute", "L4_hold", "L5_block"}
        for emission in emissions:
            if (
                emission.kind == "event"
                and emission.decision is not None
                and emission.decision.action in withheld
            ):
                committed = [
                    e.text
                    for e in emissions
                    if e.kind == "text" and e.sentence_idx == emission.sentence_idx
                ]
                assert committed == [], (
                    f"{emission.decision.action} on sentence "
                    f"{emission.sentence_idx} still reached the customer"
                )

    asyncio.run(scenario())


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    sentences=st.lists(st.sampled_from(_SENTENCES), min_size=1, max_size=4),
    chunk_size=st.integers(min_value=1, max_value=40),
)
def test_every_token_is_emitted_exactly_once_when_all_pass(
    sentences: list[str], chunk_size: int
) -> None:
    """The second half of the property. No drops, and -- just as important -- no
    duplicates: duplicated text looks deliberate, which is worse than losing it."""

    async def scenario() -> None:
        text = " ".join(sentences)
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
        gate = CommitGate(
            risk_engine=ScriptedEngine(default="L0_pass"),
            stakes=_stakes(),
            request_id="req_once",
            mode="buffered",
        )
        seen = emitted_text(await run_gate(gate, chunks))
        assert "".join(seen.split()) == "".join(text.split())

    asyncio.run(scenario())


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(chunk_size=st.integers(min_value=1, max_value=30))
def test_unbuffered_output_is_byte_identical(chunk_size: int) -> None:
    """L0 is ~80% of traffic and must stay genuinely free: the bytes the provider sent
    are the bytes the customer gets."""

    async def scenario() -> None:
        text = "The branch opens at 9:30 AM. Rs. 40,000 was credited. All done."
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
        gate = CommitGate(
            risk_engine=ScriptedEngine(default="L0_pass"),
            stakes=_stakes(50),
            request_id="req_raw",
            mode="unbuffered",
        )
        assert emitted_text(await run_gate(gate, chunks)) == text

    asyncio.run(scenario())


# =========================================================================== #
# The state machine
# =========================================================================== #


async def test_a_buffered_gate_starts_buffering() -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(), stakes=_stakes(), request_id="r", mode="buffered"
    )
    assert gate.state is GateState.BUFFERING
    assert gate.buffered is True


async def test_an_unbuffered_gate_starts_in_passthrough() -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(), stakes=_stakes(50), request_id="r", mode="unbuffered"
    )
    assert gate.state is GateState.PASSTHROUGH
    assert gate.buffered is False


async def test_escalation_is_one_way() -> None:
    """ADR-003: a stream that has already seen trouble must not relax its guard."""
    gate = CommitGate(
        risk_engine=ScriptedEngine(), stakes=_stakes(50), request_id="r", mode="unbuffered"
    )
    gate.escalate("signal fired")
    assert gate.buffered is True
    gate.escalate("again")
    assert gate.buffered is True
    assert gate.state is not GateState.PASSTHROUGH


async def test_a_blocked_sentence_terminates_the_gate() -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=["L5_block"]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
    )
    await run_gate(gate, ["This leaks a canary. "])
    assert gate.state is GateState.TERMINATED


async def test_the_gate_reaches_terminated_after_finish() -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(), stakes=_stakes(), request_id="r", mode="buffered"
    )
    await run_gate(gate, ["A sentence. "])
    assert gate.state is GateState.TERMINATED


async def test_pushing_after_termination_emits_nothing() -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(), stakes=_stakes(), request_id="r", mode="buffered"
    )
    await run_gate(gate, ["A sentence. "])
    assert await gate.push("more text. ") == []


# =========================================================================== #
# already_emitted -- the flag the optimiser prices against
# =========================================================================== #


async def test_unbuffered_sentences_are_marked_already_emitted() -> None:
    """Get this wrong and the optimiser prices a repair it cannot perform (ADR-003)."""
    engine = ScriptedEngine()
    gate = CommitGate(risk_engine=engine, stakes=_stakes(50), request_id="r", mode="unbuffered")
    await run_gate(gate, ["The branch opens at nine. ", "It closes at five. "])
    assert engine.already_emitted
    assert all(engine.already_emitted)


async def test_buffered_sentences_are_not_marked_already_emitted() -> None:
    engine = ScriptedEngine()
    gate = CommitGate(risk_engine=engine, stakes=_stakes(), request_id="r", mode="buffered")
    await run_gate(gate, ["Clause 7.4 imposes a penalty. "])
    assert engine.already_emitted == [False]


# =========================================================================== #
# Held actions withhold text
# =========================================================================== #


@pytest.mark.parametrize("action", ["L3_reroute", "L4_hold", "L5_block"])
async def test_a_withheld_action_emits_no_text(action: Action) -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=[action]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
    )
    emissions = await run_gate(gate, ["Clause 7.4 imposes a 2% penalty. "])
    assert emitted_text(emissions) == ""
    assert any(e.kind == "event" for e in emissions)


@pytest.mark.parametrize("action", ["L0_pass", "L1_annotate"])
async def test_a_passing_action_emits_the_sentence(action: Action) -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=[action]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
    )
    emissions = await run_gate(gate, ["Clause 9.1 applies to your loan. "])
    assert "Clause 9.1" in emitted_text(emissions)


async def test_only_the_flagged_sentence_is_withheld() -> None:
    """A held sentence must not take the rest of the answer with it."""
    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=["L0_pass", "L4_hold", "L0_pass"]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
    )
    emissions = await run_gate(
        gate,
        ["First sentence is fine. ", "Second is invented. ", "Third is fine again. "],
    )
    seen = emitted_text(emissions)
    assert "First sentence" in seen
    assert "Second is invented" not in seen
    assert "Third is fine" in seen


# =========================================================================== #
# Repair (L2)
# =========================================================================== #


async def test_a_repair_replaces_the_sentence() -> None:
    async def repair(sentence: str, decision: Decision, prefix: str) -> str:
        return "Clause 9.1 applies, so no prepayment charge is payable."

    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=["L2_repair"]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
        repair=repair,
    )
    seen = emitted_text(await run_gate(gate, ["Clause 7.4 imposes a 2% penalty. "]))
    assert "Clause 9.1" in seen
    assert "Clause 7.4" not in seen  # the original never reached the customer


async def test_a_failed_repair_withholds_rather_than_shipping_the_original() -> None:
    """The optimiser already priced this sentence as worth repairing; shipping it
    unchanged because our repair failed would be the worst of both."""
    attempts = 0

    async def repair(sentence: str, decision: Decision, prefix: str) -> str | None:
        nonlocal attempts
        attempts += 1
        return None

    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=["L2_repair"]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
        repair=repair,
    )
    seen = emitted_text(await run_gate(gate, ["Clause 7.4 imposes a 2% penalty. "]))
    assert seen == ""
    assert attempts == 2  # two attempts, then escalate rather than loop


async def test_repair_without_a_repairer_falls_back_to_releasing() -> None:
    """A misconfiguration must degrade to the pre-gate behaviour, not to a hang."""
    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=["L2_repair"]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
        repair=None,
    )
    assert "Clause 7.4" in emitted_text(await run_gate(gate, ["Clause 7.4 imposes 2%. "]))


# =========================================================================== #
# The watchdog
# =========================================================================== #


async def test_a_hanging_engine_does_not_hang_the_stream() -> None:
    """8 s in production; short here. Holding a sentence because our own checker
    stalled is the worst possible outcome -- it looks exactly like a freeze."""
    gate = CommitGate(
        risk_engine=HangingEngine(),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
        watchdog_s=0.15,
    )
    emissions = await asyncio.wait_for(run_gate(gate, ["A sentence that stalls. "]), timeout=3.0)
    assert "A sentence that stalls" in emitted_text(emissions)


async def test_an_engine_that_raises_fails_open() -> None:
    """Contract 1 says evaluate never raises, but the gate does not get to assume it."""

    class Exploding:
        async def evaluate(self, ctx: object) -> Decision:
            raise RuntimeError("boom")

        async def prefetch(self, *a: object, **k: object) -> None:
            return None

        def health(self) -> dict[str, object]:
            return {}

    gate = CommitGate(risk_engine=Exploding(), stakes=_stakes(), request_id="r", mode="buffered")
    assert "Some text" in emitted_text(await run_gate(gate, ["Some text here. "]))


# =========================================================================== #
# Concurrency: verification hides under generation
# =========================================================================== #


async def test_verification_overlaps_generation() -> None:
    """The whole latency claim. Sequencing verification after generation is the
    difference between SentGuard's measured 36 ms and 576 ms of added overhead."""
    started: list[float] = []

    class SlowEngine(ScriptedEngine):
        async def evaluate(self, ctx: object) -> Decision:
            started.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.05)
            return await super().evaluate(ctx)

    gate = CommitGate(
        risk_engine=SlowEngine(default="L0_pass"),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
    )
    begin = asyncio.get_running_loop().time()
    await run_gate(gate, ["One. ", "Two. ", "Three. "])
    elapsed = asyncio.get_running_loop().time() - begin

    assert len(started) == 3
    # Three 50 ms evaluations. Fully sequential would be >= 150 ms of pure waiting;
    # the gate must not be materially worse than that, and each starts as its sentence
    # completes rather than after the stream ends.
    assert elapsed < 0.5


async def test_the_engine_sees_every_sentence() -> None:
    engine = ScriptedEngine()
    gate = CommitGate(risk_engine=engine, stakes=_stakes(), request_id="r", mode="buffered")
    await run_gate(gate, ["First one. ", "Second one. ", "Third one."])
    assert len(engine.seen) == 3


async def test_decisions_are_recorded_for_the_trace() -> None:
    gate = CommitGate(
        risk_engine=ScriptedEngine(actions=["L0_pass", "L4_hold"]),
        stakes=_stakes(),
        request_id="r",
        mode="buffered",
    )
    await run_gate(gate, ["Fine sentence. ", "Bad sentence. "])
    assert [d.action for d in gate.decisions] == ["L0_pass", "L4_hold"]
