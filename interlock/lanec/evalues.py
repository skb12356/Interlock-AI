"""Anytime-valid testing: watch a rate continuously without cheating.

Fairness monitoring has a statistics problem that is easy to miss and fatal once missed.
The natural thing to do is run a significance test every hour and alert when p < 0.05.
Do that for a week and you have run 168 tests; the chance that at least one fires by luck
alone is not 5% but about 99.98%. The monitor becomes a random alarm, gets ignored, and
then gets switched off — which is the same failure mode as an over-blocking guardrail,
arriving through arithmetic instead of policy.

CLAUDE.md §8 is explicit about the fix: **anytime-valid tests, never repeated ordinary
significance tests.** This is that.

**The construction.** For observations ``X_t ∈ [0, 1]`` and a null ``E[X_t] ≤ mu0``::

    e_t = Π_{s≤t} (1 + lambda_s · (X_s − mu0))

Under the null each factor has conditional expectation at most 1, so ``e_t`` is a
non-negative supermartingale starting at 1. Ville's inequality then gives, for the whole
infinite sequence at once::

    P( ∃t : e_t ≥ 1/alpha )  ≤  alpha

That is the entire point. You may look at ``e_t`` after every single observation, stop
whenever you like, and the false-alarm probability over the *whole run* is still bounded
by alpha. No correction, no fixed sample size, no peeking penalty.

**Two things make it valid, and both are easy to break.**

*lambda must be predictable.* Each ``lambda_s`` may depend only on ``X_1 … X_{s−1}``.
Choosing it using ``X_s`` — for instance by fitting to the whole series at once — makes
the expectation argument collapse and the guarantee evaporate silently. Nothing errors;
the numbers just stop meaning what they say.

*lambda must keep every factor positive.* With ``X ∈ [0,1]`` the worst case is ``X = 0``,
so ``lambda < 1/mu0`` is required. A single non-positive factor would zero the martingale
permanently and destroy an alarm that had already been earned.

The always-valid p-value is ``p_t = 1 / max_{s≤t} e_s``, using the **running maximum**
rather than the current value: evidence that arrived and then partially receded still
happened, and a p-value that recovered as the martingale drifted back down would be
exactly the peeking artefact this construction exists to remove.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["EValueMonitor", "EValueState", "always_valid_p"]

#: Alert threshold, as a false-alarm rate over the *entire run* rather than per test.
DEFAULT_ALPHA = 0.05

#: Fraction of the theoretical maximum bet we are willing to place. The bound requires
#: lambda < 1/mu0; sitting at the boundary makes a single unlucky observation drive a
#: factor to ~0 and kill the martingale for good. Half is the usual conservative choice
#: and costs only a little power.
LAMBDA_SAFETY = 0.5

#: Below this many observations lambda stays at zero, so ``e_t`` stays at exactly 1.
#: Betting on an estimate from three data points is how a monitor alarms in its first
#: minute of life -- valid, since the bound still holds, but useless.
WARMUP = 10


def always_valid_p(running_max_e: float) -> float:
    """``p = 1 / max e``, clipped to [0, 1]."""
    if running_max_e <= 1.0:
        return 1.0
    return min(1.0, 1.0 / running_max_e)


@dataclass(frozen=True, slots=True)
class EValueState:
    """The monitor after one observation. Everything a chart needs."""

    t: int
    x: float
    lambda_used: float
    e_value: float
    running_max_e: float
    p_value: float
    alerted: bool

    def as_row(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "x": self.x,
            "lambda": round(self.lambda_used, 6),
            "e_value": self.e_value,
            "running_max_e": self.running_max_e,
            "p_value": self.p_value,
            "alerted": self.alerted,
        }


@dataclass
class EValueMonitor:
    """A betting martingale against ``H0: E[X] <= mu0``.

    Feed it one observation at a time. It never needs to know how many are coming, and
    it may be read after every one.
    """

    #: The tolerable rate under the null. For fairness twins this is the disparity rate
    #: the operator is willing to accept -- **not** zero, because two answers to two
    #: differently-worded questions will differ occasionally for innocent reasons, and a
    #: null of exactly zero would alert on the first one.
    mu0: float = 0.05
    alpha: float = DEFAULT_ALPHA
    warmup: int = WARMUP
    safety: float = LAMBDA_SAFETY

    e_value: float = field(default=1.0, init=False)
    running_max_e: float = field(default=1.0, init=False)
    history: list[EValueState] = field(default_factory=list, init=False)
    _sum: float = field(default=0.0, init=False)
    _sum_sq: float = field(default=0.0, init=False)
    _n: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not 0.0 < self.mu0 < 1.0:
            raise ValueError(f"mu0 must be in (0, 1), got {self.mu0}")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1), got {self.alpha}")

    # ------------------------------------------------------------------ #

    @property
    def alerted(self) -> bool:
        """True once the run has ever crossed the threshold. Latched deliberately —
        evidence that arrived does not un-arrive because later data was quieter."""
        return self.running_max_e >= 1.0 / self.alpha

    @property
    def p_value(self) -> float:
        return always_valid_p(self.running_max_e)

    @property
    def threshold(self) -> float:
        return 1.0 / self.alpha

    def _next_lambda(self) -> float:
        """The bet for the NEXT observation, from everything seen so far.

        Predictable by construction: this is called before the new observation is
        folded in, so it cannot see it. An approximate Kelly / GRAPPA plug-in — bet in
        proportion to the observed excess over the null, damped by its variability.
        """
        if self._n < self.warmup:
            return 0.0
        mean = self._sum / self._n
        variance = max(1e-12, self._sum_sq / self._n - mean**2)
        excess = mean - self.mu0
        if excess <= 0.0:
            # No evidence against the null so far. Bet nothing: a negative lambda would
            # be betting FOR the null, which this test makes no claim about.
            return 0.0
        raw = excess / (variance + excess**2)
        ceiling = self.safety / self.mu0
        return float(min(raw, ceiling))

    def update(self, x: float) -> EValueState:
        """Fold in one observation and return the new state."""
        if not 0.0 <= x <= 1.0:
            raise ValueError(f"observations must be in [0, 1], got {x}")

        lam = self._next_lambda()
        factor = 1.0 + lam * (x - self.mu0)
        # Guaranteed positive by the ceiling on lambda, but asserted rather than assumed:
        # a single non-positive factor would zero the martingale permanently and silently
        # destroy an alarm that had already been earned.
        if factor <= 0.0:  # pragma: no cover - unreachable while the ceiling holds
            raise AssertionError(f"non-positive martingale factor {factor} at lambda={lam}")

        self.e_value *= factor
        self.running_max_e = max(self.running_max_e, self.e_value)

        self._sum += x
        self._sum_sq += x * x
        self._n += 1

        state = EValueState(
            t=self._n,
            x=x,
            lambda_used=lam,
            e_value=self.e_value,
            running_max_e=self.running_max_e,
            p_value=self.p_value,
            alerted=self.alerted,
        )
        self.history.append(state)
        return state

    def extend(self, observations: Sequence[float]) -> EValueState | None:
        last: EValueState | None = None
        for x in observations:
            last = self.update(x)
        return last

    # ------------------------------------------------------------------ #

    def report(self) -> dict[str, Any]:
        observed = self._sum / self._n if self._n else 0.0
        notes: list[str] = []
        if self._n < self.warmup:
            notes.append(
                f"only {self._n} observations -- below the {self.warmup}-sample warm-up, "
                f"so no bets have been placed and e is still exactly 1"
            )
        if self.alerted:
            notes.append(
                f"ALERTED: e reached {self.running_max_e:.1f} against a threshold of "
                f"{self.threshold:.0f}. Under the null this happens at most "
                f"{self.alpha:.0%} of the time ACROSS THE WHOLE RUN, not per look."
            )
        elif self._n >= self.warmup and observed <= self.mu0:
            notes.append(
                f"observed rate {observed:.3f} is at or below the null {self.mu0:.3f}; "
                f"no evidence of disparity, and none is being claimed"
            )
        return {
            "n": self._n,
            "mu0": self.mu0,
            "alpha": self.alpha,
            "observed_rate": round(observed, 4),
            "e_value": self.e_value,
            "running_max_e": self.running_max_e,
            "alert_threshold": self.threshold,
            "alerted": self.alerted,
            "always_valid_p": self.p_value,
            "notes": notes,
        }

    def chart_series(self) -> dict[str, list[float]]:
        """The one chart the plan asks for: e over time, with the alert line."""
        return {
            "t": [state.t for state in self.history],
            "e_value": [state.e_value for state in self.history],
            "running_max_e": [state.running_max_e for state in self.history],
            "p_value": [state.p_value for state in self.history],
            "alert_line": [self.threshold] * len(self.history),
        }
