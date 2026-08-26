"""Cost regret: what did we spend that we did not have to?

The router sends high-stakes traffic to the strong model. Some of that traffic did not
need it — the cheap model would have produced an answer that passed the same checks. The
money spent on those requests is **regret**, and it is the honest counterweight to the
saving Interlock claims: a router that never over-spends is a router that is not routing.

Measured by shadow replay. Sample a small share of strong-tier traffic, re-run it on the
cheaper model *offline*, verify the cheap answer through the same risk engine, and record
whether it would have passed. Where it would, the difference in price is regret.

Three things make this a real measurement rather than a plausible-looking number:

**It is a sample, so it gets an interval.** CLAUDE.md §9 is explicit — the ledger
reports with confidence intervals, never bare point estimates. A 5% sample of a few
hundred requests is a handful of observations, and the interval on that is wide enough
that quoting the point alone would be misleading about how much is known. The bootstrap
is used rather than a normal approximation because the per-request regret distribution is
strongly non-normal: most requests have zero regret and a few have all of it.

**It runs offline.** Shadow replay is Lane C by construction (invariant 3's cut order
names it as the first thing to thin). It must never touch the request path — the
customer is not waiting for a second opinion on an answer they already have.

**"Would have passed" is decided by the same risk engine.** Not by a similarity score
against the strong answer, which would measure agreement rather than adequacy. A cheap
answer that is differently worded and equally grounded is a success, and a similarity
metric would score it as a failure.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["RegretEstimate", "RegretLedger", "ShadowResult", "bootstrap_ci"]

#: Share of strong-tier traffic replayed on the cheap model. The plan says 5%. Low
#: enough to be nearly free, high enough that a day of traffic yields a usable sample.
DEFAULT_SAMPLE_RATE = 0.05

#: Resamples for the bootstrap. 2,000 is well past the point where the interval stops
#: moving, and it costs milliseconds on samples this size.
BOOTSTRAP_RESAMPLES = 2_000

#: Below this many shadow runs, an interval is reported but the estimate is flagged.
#: A regret figure from six observations is arithmetic, not evidence.
MIN_SAMPLE_FOR_CONFIDENCE = 30


def bootstrap_ci(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = 20260826,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean.

    Non-parametric on purpose. Per-request regret is mostly zeros with a few large
    values, so a normal-approximation interval would be symmetric around the mean and
    would happily extend below zero -- a confidence interval implying we might have
    *saved* money by over-spending.
    """
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))

    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    tail = (1.0 - confidence) / 2.0
    low = means[max(0, int(tail * resamples) - 1)]
    high = means[min(resamples - 1, int((1.0 - tail) * resamples))]
    return (low, high)


@dataclass(frozen=True, slots=True)
class ShadowResult:
    """One request replayed on the cheaper model."""

    request_id: str
    served_model: str
    cheaper_model: str
    #: What the request actually cost.
    served_inr: float
    #: What it would have cost on the cheaper tier.
    cheaper_inr: float
    #: The action the risk engine chose for the CHEAP answer. Anything not requiring
    #: intervention means the cheap model would have been good enough.
    cheaper_action: str
    #: True when the cheap answer would have been served without intervention.
    cheaper_sufficed: bool

    @property
    def regret_inr(self) -> float:
        """Money spent that need not have been. Zero when the upgrade was justified."""
        if not self.cheaper_sufficed:
            return 0.0
        return max(0.0, self.served_inr - self.cheaper_inr)


@dataclass
class RegretEstimate:
    """Population regret, with its interval and its caveats."""

    n_shadow: int
    n_sufficed: int
    #: Mean regret per sampled request, and the bootstrap interval on that mean.
    mean_regret_inr: float
    ci_low_inr: float
    ci_high_inr: float
    #: Scaled to the whole population the sample was drawn from.
    population_requests: int
    estimated_total_regret_inr: float
    estimated_total_ci: tuple[float, float]
    #: Share of the sample where the cheap model would have done.
    over_routing_rate: float
    confidence: float = 0.95
    notes: list[str] = field(default_factory=list)

    @property
    def reliable(self) -> bool:
        return self.n_shadow >= MIN_SAMPLE_FOR_CONFIDENCE

    def statement(self) -> str:
        """The sentence that may be said out loud, and no stronger one."""
        if self.n_shadow == 0:
            return "No shadow runs yet -- regret is unmeasured, not zero."
        body = (
            f"Rs.{self.estimated_total_regret_inr:,.2f} of over-routing across "
            f"{self.population_requests} requests "
            f"[{self.estimated_total_ci[0]:,.2f}, {self.estimated_total_ci[1]:,.2f}] "
            f"at {self.confidence:.0%} confidence, from {self.n_shadow} shadow runs"
        )
        if not self.reliable:
            body += f" -- BELOW {MIN_SAMPLE_FOR_CONFIDENCE} SAMPLES, treat as indicative only"
        return body

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["estimated_total_ci"] = list(self.estimated_total_ci)
        payload["reliable"] = self.reliable
        payload["statement"] = self.statement()
        return payload


@dataclass
class RegretLedger:
    """Decides what to shadow, and estimates population regret from what came back."""

    sample_rate: float = DEFAULT_SAMPLE_RATE
    confidence: float = 0.95
    seed: int = 20260826
    results: list[ShadowResult] = field(default_factory=list)
    #: Every strong-tier request seen, sampled or not. The denominator.
    strong_tier_requests: int = 0
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def should_shadow(self, tier: str) -> bool:
        """Sample only strong-tier traffic.

        Shadowing cheap-tier requests would measure whether an even cheaper model would
        do, which is a different and much less interesting question -- the money is in
        the tier that costs money.
        """
        if tier != "strong":
            return False
        self.strong_tier_requests += 1
        return self._rng.random() < self.sample_rate

    def record(self, result: ShadowResult) -> None:
        self.results.append(result)

    def estimate(self) -> RegretEstimate:
        notes: list[str] = []
        if not self.results:
            return RegretEstimate(
                n_shadow=0,
                n_sufficed=0,
                mean_regret_inr=0.0,
                ci_low_inr=0.0,
                ci_high_inr=0.0,
                population_requests=self.strong_tier_requests,
                estimated_total_regret_inr=0.0,
                estimated_total_ci=(0.0, 0.0),
                over_routing_rate=0.0,
                confidence=self.confidence,
                notes=["no shadow runs; regret is unmeasured, which is not the same as zero"],
            )

        regrets = [result.regret_inr for result in self.results]
        mean = sum(regrets) / len(regrets)
        low, high = bootstrap_ci(regrets, confidence=self.confidence, seed=self.seed)

        # Scale the per-request mean to the population it was sampled from. Both ends
        # of the interval scale too -- reporting a scaled point estimate beside an
        # unscaled interval is a units error that looks like precision.
        population = max(self.strong_tier_requests, len(self.results))
        sufficed = sum(1 for result in self.results if result.cheaper_sufficed)

        if len(self.results) < MIN_SAMPLE_FOR_CONFIDENCE:
            notes.append(
                f"only {len(self.results)} shadow runs -- below {MIN_SAMPLE_FOR_CONFIDENCE}, "
                f"the interval is wide and the point estimate should not be quoted alone"
            )
        if sufficed == len(self.results):
            notes.append(
                "the cheap model sufficed on EVERY sampled request -- either the router "
                "is over-routing badly, or the sample is too small to have seen a case "
                "that needed the upgrade"
            )

        return RegretEstimate(
            n_shadow=len(self.results),
            n_sufficed=sufficed,
            mean_regret_inr=mean,
            ci_low_inr=low,
            ci_high_inr=high,
            population_requests=population,
            estimated_total_regret_inr=mean * population,
            estimated_total_ci=(low * population, high * population),
            over_routing_rate=sufficed / len(self.results),
            confidence=self.confidence,
            notes=notes,
        )
