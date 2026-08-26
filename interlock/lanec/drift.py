"""The meta-monitor: when should we stop trusting our own detectors?

Invariant 8 asks for something unusual. Most monitoring answers *"is the system up?"*.
This answers *"are the numbers the system is producing still meaningful?"* — and it has
to be able to come back with **no**, about itself, loudly enough that somebody acts.

Three things drift, independently, and conflating them wastes a week of debugging:

**Calibration drift.** The isotonic map was fitted on a snapshot. If traffic shifts, the
map still returns a number between 0 and 1 and that number is quietly wrong. Detected by
re-scoring the human anchor set and watching ECE: no new labelling, no retraining, just
the same fixed items scored by today's calibrator.

**Agreement drift.** The fast lane and the deep judge used to agree at some rate. If
that rate falls, one of them changed and the fast lane is the more likely candidate.

**Input drift.** The traffic itself moved — new domains, new phrasing, a different stakes
mix. This one is *not* an alarm on its own. It is the explanation for the other two, and
reporting it as a failure would fire on every product launch and every marketing campaign.

Every alarm here is **anytime-valid** (``evalues.py``), for the same reason fairness is:
a meta-monitor is read continuously by definition, so a monitor built on repeated
significance tests would be alarming constantly on its own multiplicity and would be the
first thing an operator muted.

**What it does not do.** It never retunes a threshold by itself. It reports that the
anchor has moved and names which kind of drift it is; changing a policy remains a
reviewed change to a versioned file (CLAUDE.md §9). A monitor that silently re-fits the
thing it is monitoring has no way left to tell anyone it failed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from interlock.lanec.evalues import EValueMonitor

__all__ = ["DriftReport", "MetaMonitor", "TrustState"]

#: ECE on the anchor set above which the calibration is no longer fit to price with.
#: Twice the target the calibrator was accepted at (0.05) -- a monitor that alarms the
#: moment the metric moves at all would alarm on sampling noise.
ECE_ALARM = 0.10

#: The agreement rate with the deep judge that the fast lane is expected to hold.
#: Below this, disagreement is treated as evidence rather than as noise.
AGREEMENT_FLOOR = 0.80

#: Minimum anchor items before any calibration verdict is offered.
MIN_ANCHOR = 50


class TrustState:
    """How much of the system's own output is currently defensible."""

    TRUSTED = "trusted"
    #: Numbers still usable, but something has moved and somebody should look.
    WATCH = "watch"
    #: Do not quote the calibrated probabilities or anything derived from them.
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class DriftReport:
    """What moved, by how much, and what may still be said."""

    trust: str
    ece_now: float | None
    ece_at_fit: float | None
    agreement_rate: float | None
    n_anchor: int
    n_judged: int
    calibration_alerted: bool
    agreement_alerted: bool
    input_shift: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def safe_to_quote_probabilities(self) -> bool:
        return self.trust != TrustState.UNTRUSTED

    def statement(self) -> str:
        if self.trust == TrustState.TRUSTED:
            return (
                f"Detectors are behaving as calibrated (ECE {self.ece_now:.4f} on "
                f"{self.n_anchor} anchor items). Probabilities may be quoted."
            )
        if self.trust == TrustState.WATCH:
            return "Something has moved; numbers are still usable. " + " ".join(self.reasons)
        return (
            "DO NOT QUOTE the calibrated probabilities or anything derived from them. "
            + " ".join(self.reasons)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trust": self.trust,
            "ece_now": self.ece_now,
            "ece_at_fit": self.ece_at_fit,
            "agreement_rate": self.agreement_rate,
            "n_anchor": self.n_anchor,
            "n_judged": self.n_judged,
            "calibration_alerted": self.calibration_alerted,
            "agreement_alerted": self.agreement_alerted,
            "input_shift": self.input_shift,
            "reasons": self.reasons,
            "safe_to_quote_probabilities": self.safe_to_quote_probabilities,
            "statement": self.statement(),
        }


@dataclass
class MetaMonitor:
    """Re-scores the human anchor set and watches the fast lane's agreement.

    The anchor set is the **hand-labelled** one (D2-B3), not the induced data. Induced
    failures come from a generator, so re-scoring them checks that the calibrator still
    fits the generator -- which it will, forever, while real traffic walks away.
    """

    ece_at_fit: float | None = None
    ece_alarm: float = ECE_ALARM
    agreement_floor: float = AGREEMENT_FLOOR
    min_anchor: int = MIN_ANCHOR
    #: Anytime-valid monitor over judge disagreements.
    disagreement: EValueMonitor = field(
        default_factory=lambda: EValueMonitor(mu0=1.0 - AGREEMENT_FLOOR, alpha=0.05)
    )
    #: Domain mix at the time the calibrator was fitted, for the input-shift comparison.
    baseline_domains: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------ #

    def observe_disagreement(self, indicator: float | None) -> None:
        """Feed one judge sample. ``None`` (unjudgeable) is skipped, not counted as
        agreement -- silently scoring 'unclear' as a win is how a meta-monitor becomes
        the most agreeable component in the system."""
        if indicator is not None:
            self.disagreement.update(indicator)

    def input_shift(self, current_domains: dict[str, float]) -> dict[str, float]:
        """Per-domain change in traffic share since the calibrator was fitted.

        Reported, never alarmed on. Traffic moving is normal; it is the *explanation*
        for a calibration alarm, and treating it as a failure in itself would fire on
        every product launch.
        """
        if not self.baseline_domains:
            return {}
        keys = set(self.baseline_domains) | set(current_domains)
        return {
            key: round(current_domains.get(key, 0.0) - self.baseline_domains.get(key, 0.0), 4)
            for key in sorted(keys)
        }

    def assess(
        self,
        *,
        anchor_probabilities: Sequence[float],
        anchor_labels: Sequence[int],
        current_domains: dict[str, float] | None = None,
    ) -> DriftReport:
        """The verdict. Cheap enough to run continuously."""
        from interlock.risk.calibration import expected_calibration_error

        reasons: list[str] = []
        n_anchor = len(anchor_labels)

        ece_now: float | None = None
        calibration_alerted = False
        if n_anchor >= self.min_anchor:
            import numpy as np

            ece_now = expected_calibration_error(
                np.asarray(anchor_probabilities, dtype=float),
                np.asarray(anchor_labels, dtype=float),
            )
            if ece_now >= self.ece_alarm:
                calibration_alerted = True
                reasons.append(
                    f"ECE on the anchor set is {ece_now:.4f}, at or above the {self.ece_alarm} "
                    f"alarm; the calibrated probabilities no longer mean what they say"
                )
            elif self.ece_at_fit is not None and ece_now > self.ece_at_fit * 3:
                reasons.append(
                    f"ECE has tripled since the fit ({self.ece_at_fit:.4f} -> {ece_now:.4f}) "
                    f"but is still below the alarm; worth a look before it is not"
                )
        else:
            reasons.append(
                f"only {n_anchor} anchor items -- below {self.min_anchor}, so no "
                f"calibration verdict is offered. Absence of an alarm is not reassurance."
            )

        agreement_alerted = self.disagreement.alerted
        agreement_rate: float | None = None
        judged = self.disagreement._n
        if judged:
            agreement_rate = 1.0 - (self.disagreement._sum / judged)
        if agreement_alerted:
            reasons.append(
                f"fast-lane/judge disagreement is anytime-valid significant "
                f"(e={self.disagreement.running_max_e:.1f}, p={self.disagreement.p_value:.4f}); "
                f"one of the two has moved and the fast lane is the likelier candidate"
            )

        shift = self.input_shift(current_domains or {})
        moved = {k: v for k, v in shift.items() if abs(v) >= 0.10}
        if moved and (calibration_alerted or agreement_alerted):
            reasons.append(
                f"traffic mix has also moved ({moved}) -- likely the CAUSE of the above "
                f"rather than a separate problem"
            )

        if calibration_alerted or agreement_alerted:
            trust = TrustState.UNTRUSTED
        elif reasons:
            trust = TrustState.WATCH
        else:
            trust = TrustState.TRUSTED

        return DriftReport(
            trust=trust,
            ece_now=ece_now,
            ece_at_fit=self.ece_at_fit,
            agreement_rate=round(agreement_rate, 4) if agreement_rate is not None else None,
            n_anchor=n_anchor,
            n_judged=judged,
            calibration_alerted=calibration_alerted,
            agreement_alerted=agreement_alerted,
            input_shift=shift,
            reasons=reasons,
        )
