"""Governor tests.

Invariant 4 is one assertion with two halves, and the halves fail in opposite
directions. Low stakes must fail OPEN — blocking a branch-timings question because a GPU
fell over is how a guardrail gets switched off. High stakes must fail CLOSED — shipping a
₹40,000 loan answer unchecked for the same reason is how it gets someone hurt. A
governor that gets the sign backwards passes every latency test in this file and is
worse than having none.
"""

from __future__ import annotations

import pytest

from interlock.core.types import Stakes
from interlock.gateway.governor import (
    CAPABILITIES,
    DEFAULT_LADDER,
    Breaker,
    BreakerState,
    Governor,
    GovernorState,
)


def _stakes(impact: float) -> Stakes:
    return Stakes(
        impact_inr=impact,
        reversibility="costly" if impact >= 1000 else "reversible",
        domain="prepayment" if impact >= 1000 else "branch_info",
        confidence=0.9,
    )


def _load(governor: Governor, overhead_ms: float, n: int = 40) -> None:
    for _ in range(n):
        governor.observe(overhead_ms)


# --------------------------------------------------------------------------- #
# Invariant 4
# --------------------------------------------------------------------------- #


def test_bypass_fails_open_on_low_stakes() -> None:
    """A branch-timings answer must not be blocked because a GPU fell over."""
    governor = Governor()
    governor.force(GovernorState.BYPASS)
    action, reason = governor.bypass_action(_stakes(50))
    assert action == "L0_pass"
    assert "failing open" in reason


def test_bypass_fails_closed_on_high_stakes() -> None:
    """A Rs.40,000 loan answer must not ship unchecked for the same reason."""
    governor = Governor()
    governor.force(GovernorState.BYPASS)
    action, reason = governor.bypass_action(_stakes(40_000))
    assert action == "L4_hold"
    assert "failing closed" in reason


def test_the_split_is_at_the_declared_threshold() -> None:
    """One estimate, one threshold: 'high stakes' must mean the same thing here as it
    does to the buffering decision and the router."""
    governor = Governor(hold_above_impact_inr=1_000)
    assert governor.bypass_action(_stakes(999))[0] == "L0_pass"
    assert governor.bypass_action(_stakes(1_000))[0] == "L4_hold"


# --------------------------------------------------------------------------- #
# The degradation order
# --------------------------------------------------------------------------- #


def test_background_analysis_is_given_up_first() -> None:
    """Lane C exists so it can be dropped: it is off the critical path by construction,
    so thinning it costs accuracy tomorrow and latency never."""
    governor = Governor()
    assert governor.allows("background")
    _load(governor, 70.0)
    assert governor.state is GovernorState.THIN
    assert not governor.allows("background")
    assert governor.allows("verifier"), "live-check depth must survive the first cut"


def test_the_order_is_strictly_nested() -> None:
    """Each state gives up a superset of what the one before it gave up. A degradation
    order that re-enables something on the way down is not an order."""
    previous = CAPABILITIES[GovernorState.NORMAL]
    for state in list(GovernorState)[1:]:
        current = CAPABILITIES[state]
        assert current < previous, f"{state.name} is not a strict subset of its predecessor"
        previous = current


def test_bypass_gives_up_everything() -> None:
    assert CAPABILITIES[GovernorState.BYPASS] == frozenset()


@pytest.mark.parametrize(("overhead", "expected"), [
    (10.0, GovernorState.NORMAL),
    (70.0, GovernorState.THIN),
    (100.0, GovernorState.SHALLOW),
    (150.0, GovernorState.PROBE_ONLY),
    (300.0, GovernorState.BYPASS),
])
def test_each_rung_engages_at_its_threshold(overhead: float, expected: GovernorState) -> None:
    governor = Governor()
    _load(governor, overhead)
    assert governor.state is expected


def test_the_ladder_sits_under_the_latency_budget() -> None:
    """The governor must react BEFORE the 120 ms budget is blown, not after -- which is
    the difference between degrading and having degraded."""
    first_state, first_threshold = DEFAULT_LADDER[0]
    assert first_state is GovernorState.THIN
    assert first_threshold < 120.0


# --------------------------------------------------------------------------- #
# Not over-reacting
# --------------------------------------------------------------------------- #


def test_a_few_slow_requests_do_not_degrade_anything() -> None:
    """Escalating on three observations would let one cold start degrade a deployment."""
    governor = Governor()
    for _ in range(5):
        governor.observe(5_000.0)
    assert governor.state is GovernorState.NORMAL
    assert governor.p95() > 0


def test_recovery_is_slower_than_escalation() -> None:
    """A system that de-escalates the instant latency dips oscillates under exactly the
    load that made it degrade."""
    governor = Governor()
    _load(governor, 300.0)
    assert governor.state is GovernorState.BYPASS

    # Just inside the threshold is not enough.
    _load(governor, 190.0, n=200)
    assert governor.state is GovernorState.BYPASS

    # Well inside it recovers -- one step.
    _load(governor, 100.0, n=200)
    assert governor.state is GovernorState.PROBE_ONLY


def test_recovery_walks_down_one_step_at_a_time() -> None:
    """No single TRANSITION skips a rung on the way down.

    Asserted against the transition log rather than by sampling the state between
    batches: recovery is one step per observation, so any batch large enough to refill
    the window walks the whole ladder and a sampled view would see BYPASS -> NORMAL and
    call it a jump. The invariant is about what the governor logged, not about when the
    test happened to look.
    """
    governor = Governor()
    _load(governor, 300.0)
    assert governor.state is GovernorState.BYPASS

    _load(governor, 1.0, n=200)
    assert governor.state is GovernorState.NORMAL

    order = [s.label for s in GovernorState]
    downward = [
        (t["from"], t["to"])
        for t in governor.snapshot()["recent_transitions"]
        if order.index(t["to"]) < order.index(t["from"])
    ]
    assert downward, "nothing was logged on the way down"
    for src, dst in downward:
        assert order.index(src) - order.index(dst) == 1, f"{src} -> {dst} skipped a rung"


# --------------------------------------------------------------------------- #
# The circuit breaker
# --------------------------------------------------------------------------- #


def test_five_failures_open_the_breaker() -> None:
    breaker = Breaker()
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()


def test_a_success_resets_the_failure_count() -> None:
    """Intermittent failures spread over time are not an outage."""
    breaker = Breaker()
    for _ in range(4):
        breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_a_half_open_breaker_admits_exactly_one_probe() -> None:
    """The failure being guarded against is a thundering herd onto a service that has
    only just come back."""
    breaker = Breaker(open_for_s=0.0)
    for _ in range(5):
        breaker.record_failure()
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow()
    assert not breaker.allow()
    assert not breaker.allow()


def test_a_successful_probe_closes_the_breaker() -> None:
    breaker = Breaker(open_for_s=0.0)
    for _ in range(5):
        breaker.record_failure()
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow()


def test_an_open_breaker_degrades_regardless_of_latency() -> None:
    """An observer that is failing is not slow, it is absent, and no amount of waiting
    will produce a signal from it."""
    governor = Governor()
    _load(governor, 1.0)
    assert governor.state is GovernorState.NORMAL
    for _ in range(5):
        governor.breaker.record_failure()
    governor.observe(1.0)
    assert governor.state is GovernorState.SHALLOW
    assert not governor.allows("verifier")


def test_an_open_breaker_does_not_bypass_the_engine_entirely() -> None:
    """SHALLOW, not BYPASS. The deterministic detectors do not need the observer, and
    dropping them because a GPU died would give up the canary rule for no reason."""
    governor = Governor()
    for _ in range(5):
        governor.breaker.record_failure()
    governor.observe(1.0)
    assert governor.state is GovernorState.SHALLOW
    assert governor.allows("deterministic")
    assert not governor.bypassed


# --------------------------------------------------------------------------- #
# The admin view
# --------------------------------------------------------------------------- #


def test_the_snapshot_says_what_was_given_up() -> None:
    """The console explains decisions already made (invariant 2). 'state: shallow' alone
    tells an operator nothing about what stopped happening."""
    governor = Governor()
    _load(governor, 100.0)
    snapshot = governor.snapshot()
    assert snapshot["state"] == "shallow"
    assert "background" in snapshot["given_up"]
    assert "verifier" in snapshot["given_up"]
    assert "deterministic" in snapshot["capabilities"]
    assert snapshot["recent_transitions"]
    assert snapshot["recent_transitions"][-1]["to"] == "shallow"


def test_transitions_record_their_reason() -> None:
    governor = Governor()
    _load(governor, 300.0)
    reasons = [t["reason"] for t in governor.snapshot()["recent_transitions"]]
    assert any("p95" in reason for reason in reasons)


def test_the_transition_log_is_bounded() -> None:
    """A long-lived process must not accumulate an unbounded audit trail in memory."""
    governor = Governor()
    for _ in range(200):
        governor.force(GovernorState.THIN)
        governor.force(GovernorState.NORMAL)
    assert len(governor.snapshot()["recent_transitions"]) <= 10
