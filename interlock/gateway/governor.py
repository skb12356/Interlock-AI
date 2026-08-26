"""The load governor: what Interlock gives up, in what order, when it is overloaded.

Invariant 4, and the reason it is an invariant rather than a feature: a guardrail with no
defined degradation order does not degrade, it collapses. Under load it either blocks
everything (and gets switched off in week two) or passes everything (and was never a
control). Both failures look like "the system slowed down" from outside.

So the order is declared, not discovered:

    NORMAL      full depth
    THIN        background analysis stops -- shadow replay, fairness twins, the
                deep-judge sample. Nobody waiting on a response notices.
    SHALLOW     live-check depth drops: the claim verifier is skipped, probes only.
    PROBE_ONLY  only the cheapest deterministic signals run.
    BYPASS      the risk engine is not consulted at all.

**Background first, always.** Lane C exists precisely so it can be dropped: it is off the
critical path by construction, so thinning it costs accuracy tomorrow and latency never.
Cutting live-check depth before background work would trade the thing customers feel for
the thing they do not.

**And at BYPASS the split happens.** This is the half that is easy to get backwards:

    low stakes  -> PASS   (fail open)
    high stakes -> HOLD   (fail closed)

A branch-timings question must not be blocked because a GPU fell over. A ₹40,000 loan
answer must not ship unchecked for the same reason. The whole point of one stakes
estimate is that the system knows which is which even when it knows nothing else.

The circuit breaker is separate from the state machine and feeds it: five failures in ten
seconds opens it, thirty seconds later it half-opens and one success closes it. A
half-open breaker admits exactly one probe, because the failure it is protecting against
is a thundering herd on a service that has just come back.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from interlock.core.clock import wall_time
from interlock.core.types import Action, Stakes

__all__ = ["Breaker", "BreakerState", "Governor", "GovernorState"]


class GovernorState(IntEnum):
    """Ordered by how much has been given up. Comparable on purpose."""

    NORMAL = 0
    THIN = 1
    SHALLOW = 2
    PROBE_ONLY = 3
    BYPASS = 4

    @property
    def label(self) -> str:
        return self.name.lower()


class BreakerState(IntEnum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


#: What each state gives up. Read by Lane B and Lane C rather than hardcoded there, so
#: the degradation order lives in one place and can be reasoned about as a whole.
CAPABILITIES: dict[GovernorState, frozenset[str]] = {
    GovernorState.NORMAL: frozenset({"background", "verifier", "probes", "deterministic"}),
    GovernorState.THIN: frozenset({"verifier", "probes", "deterministic"}),
    GovernorState.SHALLOW: frozenset({"probes", "deterministic"}),
    GovernorState.PROBE_ONLY: frozenset({"deterministic"}),
    GovernorState.BYPASS: frozenset(),
}

#: p95 thresholds, in ms, at which each state engages. Interlock's own overhead budget is
#: 120 ms; these sit under it so the governor reacts before the budget is blown rather
#: than after, which is the difference between degrading and having degraded.
DEFAULT_LADDER: tuple[tuple[GovernorState, float], ...] = (
    (GovernorState.THIN, 60.0),
    (GovernorState.SHALLOW, 90.0),
    (GovernorState.PROBE_ONLY, 120.0),
    (GovernorState.BYPASS, 200.0),
)

#: Below this many samples the p95 is not a p95. Escalating on three observations would
#: make one slow cold-start request degrade the whole deployment.
MIN_SAMPLES = 20

#: Recovery is deliberately slower than escalation: a system that de-escalates the
#: instant latency dips will oscillate between states under exactly the load that made
#: it degrade. One step down at a time, and only well inside the threshold.
RECOVERY_MARGIN = 0.75


@dataclass
class Breaker:
    """A circuit breaker over one dependency (the observer, in practice)."""

    failure_threshold: int = 5
    window_s: float = 10.0
    open_for_s: float = 30.0
    _failures: deque[float] = field(default_factory=deque, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _half_open_probe: bool = field(default=False, init=False)

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if wall_time() - self._opened_at >= self.open_for_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def record_failure(self) -> None:
        now = wall_time()
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_s:
            self._failures.popleft()
        if len(self._failures) >= self.failure_threshold:
            self._opened_at = now
            self._failures.clear()
            self._half_open_probe = False

    def record_success(self) -> None:
        # A success only closes the breaker from HALF_OPEN. Successes while OPEN are
        # not possible (nothing is admitted) and successes while CLOSED are the norm.
        if self.state is BreakerState.HALF_OPEN:
            self._opened_at = None
            self._half_open_probe = False
        self._failures.clear()

    def allow(self) -> bool:
        """May a call go through right now?"""
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.OPEN:
            return False
        # HALF_OPEN admits exactly one probe. The failure being guarded against is a
        # thundering herd onto a service that has only just come back.
        if self._half_open_probe:
            return False
        self._half_open_probe = True
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.name.lower(),
            "recent_failures": len(self._failures),
            "opened_at": self._opened_at,
        }


@dataclass
class Governor:
    """Tracks Interlock's own overhead and decides how much checking to attempt."""

    ladder: tuple[tuple[GovernorState, float], ...] = DEFAULT_LADDER
    window: int = 200
    min_samples: int = MIN_SAMPLES
    breaker: Breaker = field(default_factory=Breaker)
    #: Stakes at or above which BYPASS holds rather than passes. Defaults to the
    #: policy's buffering threshold so "high stakes" means one thing system-wide.
    hold_above_impact_inr: float = 1000.0
    _samples: deque[float] = field(default_factory=deque, init=False)
    _state: GovernorState = field(default=GovernorState.NORMAL, init=False)
    _transitions: list[tuple[float, str, str, str]] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------ #

    @property
    def state(self) -> GovernorState:
        return self._state

    def observe(self, overhead_ms: float) -> GovernorState:
        """Record one request's Interlock overhead and re-evaluate the state."""
        self._samples.append(overhead_ms)
        while len(self._samples) > self.window:
            self._samples.popleft()
        return self._reassess()

    def p95(self) -> float:
        if not self._samples:
            return 0.0
        ordered = sorted(self._samples)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return ordered[index]

    def _reassess(self) -> GovernorState:
        # The breaker outranks latency: an observer that is failing is not slow, it is
        # absent, and no amount of waiting will produce a signal from it.
        if self.breaker.state is BreakerState.OPEN:
            self._transition(GovernorState.SHALLOW, "observer circuit breaker is open")
            return self._state

        if len(self._samples) < self.min_samples:
            # Not enough evidence. Escalating on three observations would let one slow
            # cold start degrade a whole deployment.
            return self._state

        current = self.p95()
        target = GovernorState.NORMAL
        for state, threshold in self.ladder:
            if current >= threshold:
                target = state

        if target > self._state:
            self._transition(target, f"p95 {current:.0f} ms")
        elif target < self._state:
            # One step at a time, and only well inside the threshold, or the system
            # oscillates under exactly the load that made it degrade.
            recovery_to = GovernorState(self._state - 1)
            threshold = dict(self.ladder).get(self._state, 0.0)
            if current <= threshold * RECOVERY_MARGIN:
                self._transition(recovery_to, f"p95 {current:.0f} ms, recovering one step")
        return self._state

    def _transition(self, target: GovernorState, reason: str) -> None:
        if target == self._state:
            return
        self._transitions.append((wall_time(), self._state.label, target.label, reason))
        del self._transitions[:-50]
        self._state = target

    # -- what callers actually ask ------------------------------------- #

    def allows(self, capability: str) -> bool:
        """May this class of work run in the current state?"""
        return capability in CAPABILITIES[self._state]

    @property
    def bypassed(self) -> bool:
        return self._state is GovernorState.BYPASS

    def bypass_action(self, stakes: Stakes) -> tuple[Action, str]:
        """Invariant 4's split, and the half that is easy to get backwards.

        Low stakes fail **open**: a branch-timings answer must not be blocked because a
        GPU fell over. High stakes fail **closed**: a Rs.40,000 loan answer must not ship
        unchecked for the same reason. One stakes estimate is what lets the system tell
        them apart when it knows nothing else about the request.
        """
        if stakes.impact_inr >= self.hold_above_impact_inr:
            return "L4_hold", (
                f"governor is in bypass and this request is high-stakes "
                f"(Rs.{stakes.impact_inr:,.0f}); failing closed"
            )
        return "L0_pass", (
            f"governor is in bypass and this request is low-stakes "
            f"(Rs.{stakes.impact_inr:,.0f}); failing open"
        )

    def force(self, state: GovernorState, reason: str = "forced") -> None:
        """Set the state directly. For tests, drills and an operator override."""
        self._transition(state, reason)

    def snapshot(self) -> dict[str, Any]:
        """`/admin/governor`. Explains the state it is in, never asks what to do."""
        return {
            "state": self._state.label,
            "p95_ms": round(self.p95(), 2),
            "samples": len(self._samples),
            "min_samples": self.min_samples,
            "capabilities": sorted(CAPABILITIES[self._state]),
            "given_up": sorted(CAPABILITIES[GovernorState.NORMAL] - CAPABILITIES[self._state]),
            "hold_above_impact_inr": self.hold_above_impact_inr,
            "breaker": self.breaker.snapshot(),
            "ladder": [{"state": s.label, "p95_ms": t} for s, t in self.ladder],
            "recent_transitions": [
                {"at": at, "from": src, "to": dst, "reason": why}
                for at, src, dst, why in self._transitions[-10:]
            ],
        }
