"""Learn-then-Test: choosing a threshold you are allowed to make a promise about.

The policy says ``max_ungrounded_escape_rate: 0.01`` at ``confidence: 0.90``. That is a
claim about a bound, not a hope, and the difference between the two is this file.

The naive version -- sweep thresholds on held-out data, keep the smallest one whose
empirical escape rate is under 1%, quote 1% -- is wrong in a specific and expensive way.
Trying many thresholds and reporting the best one is multiple testing; the winner is
selected partly because it got lucky on that sample. The reported rate is optimistic by
an amount nobody can state, which is worse than having no bound at all, because it looks
like one.

Learn-then-Test fixes this properly:

1. Treat each candidate threshold as a **hypothesis**: "this threshold's true escape
   rate exceeds alpha."
2. Compute a valid **p-value** for each from held-out data, using a concentration
   inequality rather than a normal approximation -- the events being counted are rare,
   which is exactly where a normal approximation is least trustworthy.
3. Reject the family with a procedure that controls the error rate across all of them.
   **Fixed-sequence testing** is used here: order thresholds from most to least
   conservative and walk until the first non-rejection. Ordering carries information
   -- a stricter threshold cannot have a higher escape rate -- so this spends no
   correction budget at all, unlike Bonferroni over an unordered family.

What comes out is a threshold and a sentence you can defend: *at most 1% ungrounded
escapes, at 90% confidence, computed on n held-out items.* If no threshold survives, the
answer is that no threshold survives -- which is information, not a failure, and it must
never be papered over by widening alpha until something passes.

**Hoeffding-Bentkus.** The p-value is the minimum of two bounds: Hoeffding's, which is
tight when the rate is moderate, and Bentkus's binomial-tail bound, which is much
tighter in the small-rate regime this operates in. Taking the minimum of two valid
bounds is itself valid, and it is what the LTT literature uses for exactly this reason.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

__all__ = [
    "ConformalResult",
    "ThresholdCandidate",
    "bentkus_p_value",
    "binom_cdf",
    "hoeffding_bentkus_p_value",
    "hoeffding_p_value",
    "select_threshold",
]


def binom_cdf(successes: int, trials: int, probability: float) -> float:
    """``P(Bin(trials, probability) <= successes)``, computed exactly.

    Written out rather than pulled from scipy. It is six lines, the counts here are in
    the hundreds so exact summation is cheap, and a certified bound should not depend on
    which minor version of a transitive dependency happened to be installed.
    """
    if successes < 0:
        return 0.0
    if successes >= trials:
        return 1.0
    if probability <= 0.0:
        return 1.0
    if probability >= 1.0:
        return 0.0
    total = 0.0
    for k in range(successes + 1):
        total += math.comb(trials, k) * probability**k * (1.0 - probability) ** (trials - k)
    return min(1.0, total)


def hoeffding_p_value(observed_rate: float, alpha: float, n: int) -> float:
    """Hoeffding's bound on P(empirical rate <= observed | true rate >= alpha).

    Valid for any bounded loss. Loose when the rate is small, which is why it is only
    half of the pair.
    """
    if n <= 0:
        return 1.0
    if observed_rate >= alpha:
        return 1.0
    return float(math.exp(-2.0 * n * (alpha - observed_rate) ** 2))


def bentkus_p_value(observed_rate: float, alpha: float, n: int) -> float:
    """Bentkus' binomial-tail bound: ``e * P(Bin(n, alpha) <= ceil(n * rate))``.

    Much tighter than Hoeffding when ``alpha`` is small, which is the whole operating
    regime here -- a 1% escape target means the interesting thresholds all sit in the
    tail where Hoeffding has almost no power.
    """
    if n <= 0:
        return 1.0
    if observed_rate >= alpha:
        return 1.0
    successes = math.ceil(n * observed_rate)
    return float(min(1.0, math.e * binom_cdf(successes, n, alpha)))


def hoeffding_bentkus_p_value(observed_rate: float, alpha: float, n: int) -> float:
    """The minimum of the two. Valid because each is valid on its own."""
    return min(
        hoeffding_p_value(observed_rate, alpha, n),
        bentkus_p_value(observed_rate, alpha, n),
    )


@dataclass(frozen=True, slots=True)
class ThresholdCandidate:
    """One candidate, and everything needed to see why it was or was not chosen."""

    threshold: float
    #: Share of items above this threshold -- what fraction of traffic is intervened on.
    intervention_rate: float
    #: Share of DEFECTIVE items that slipped below the threshold. The quantity bounded.
    escape_rate: float
    n_eval: int
    p_value: float
    rejected: bool

    def as_row(self) -> dict[str, float | bool | int]:
        return {
            "threshold": self.threshold,
            "intervention_rate": self.intervention_rate,
            "escape_rate": self.escape_rate,
            "n_eval": self.n_eval,
            "p_value": self.p_value,
            "rejected": self.rejected,
        }


@dataclass
class ConformalResult:
    """The chosen threshold and the promise it licenses."""

    #: None means no threshold was certifiable. That is a valid, reportable outcome.
    threshold: float | None
    alpha: float
    delta: float
    n_eval: int
    #: Empirical escape rate at the chosen threshold.
    escape_rate: float | None = None
    intervention_rate: float | None = None
    candidates: list[dict[str, float | bool | int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def certified(self) -> bool:
        return self.threshold is not None

    def statement(self) -> str:
        """The sentence that may be said out loud, and no stronger one."""
        if not self.certified:
            return (
                f"No threshold could be certified at alpha={self.alpha}, "
                f"delta={self.delta} on {self.n_eval} held-out items. "
                "The guarantee cannot be made with this detector and this much data."
            )
        return (
            f"At most {self.alpha:.0%} ungrounded escapes, at "
            f"{1 - self.delta:.0%} confidence, at threshold {self.threshold:.4f}, "
            f"computed on {self.n_eval} held-out items."
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")


def select_threshold(
    probabilities: np.ndarray,
    labels: np.ndarray,
    *,
    alpha: float,
    delta: float,
    grid: np.ndarray | None = None,
) -> ConformalResult:
    """Pick the least-intervening threshold whose escape rate is certifiably <= alpha.

    ``probabilities`` must be **out-of-fold**. Selecting a threshold on the same data a
    calibrator was fitted to re-introduces exactly the optimism this procedure exists to
    remove, and nothing here can detect that it happened.
    """
    probabilities = np.asarray(probabilities, dtype=float)
    labels = np.asarray(labels, dtype=int)
    n_total = len(labels)
    defective = labels == 1
    n_defective = int(defective.sum())

    if n_defective == 0:
        return ConformalResult(
            threshold=None,
            alpha=alpha,
            delta=delta,
            n_eval=n_total,
            notes=["no defective items in the evaluation set; nothing to bound"],
        )

    if grid is None:
        # ASCENDING, and the direction is the whole correctness of the procedure.
        #
        # The threshold is the probability at or above which the system intervenes, so
        # threshold 0.0 intervenes on everything and lets nothing escape -- trivially
        # certifiable, operationally useless. Threshold 1.0 intervenes on nothing and
        # cannot be certified at all. Fixed-sequence testing must start where the
        # hypothesis is easiest to reject and walk toward where it is hardest.
        #
        # Getting this backwards does not raise, and does not look wrong: it reports
        # "no threshold could be certified" for every input, because the very first
        # candidate fails and the sequence stops there. Found exactly that way.
        grid = np.unique(np.round(np.linspace(0.0, 1.0, 201), 4))

    candidates: list[ThresholdCandidate] = []
    chosen: ThresholdCandidate | None = None
    for threshold in grid:
        # An "escape" is a defective item the system would NOT have acted on.
        escapes = int((defective & (probabilities < threshold)).sum())
        escape_rate = escapes / n_defective
        p_value = hoeffding_bentkus_p_value(escape_rate, alpha, n_defective)
        rejected = p_value <= delta
        candidate = ThresholdCandidate(
            threshold=float(threshold),
            intervention_rate=float((probabilities >= threshold).mean()),
            escape_rate=escape_rate,
            n_eval=n_defective,
            p_value=p_value,
            rejected=rejected,
        )
        candidates.append(candidate)
        if rejected:
            # Certifiable, and it intervenes less than every threshold before it. Keep
            # walking: the goal is the LEAST-intervening certifiable threshold.
            chosen = candidate
        else:
            # Fixed-sequence testing stops here. Continuing past a non-rejection and
            # picking a later winner is the multiple-testing mistake this whole
            # procedure exists to avoid.
            break

    notes: list[str] = []
    if chosen is None:
        notes.append(
            f"no threshold in the grid could be certified: the tightest achievable "
            f"p-value was {min(c.p_value for c in candidates):.4f} against delta={delta}"
        )
        if n_defective < 100:
            notes.append(
                f"only {n_defective} defective items -- a 1% bound needs a few hundred "
                f"before any threshold can clear it, whatever the detector's quality"
            )
    elif chosen.threshold <= 0.0:
        notes.append(
            "the certified threshold is 0.0, i.e. intervene on everything. Technically "
            "a valid bound and operationally useless; the detector is not separating."
        )
    elif chosen.intervention_rate > 0.5:
        notes.append(
            f"certified, but it intervenes on {chosen.intervention_rate:.0%} of traffic. "
            f"The bound holds; whether the product survives that rate is a separate "
            f"question, and the false-intervention metric is where it gets answered."
        )

    return ConformalResult(
        threshold=chosen.threshold if chosen else None,
        alpha=alpha,
        delta=delta,
        n_eval=n_defective,
        escape_rate=chosen.escape_rate if chosen else None,
        intervention_rate=chosen.intervention_rate if chosen else None,
        candidates=[c.as_row() for c in candidates],
        notes=notes,
    )
