"""The real risk engine. Same Protocol, no stub anywhere on the hot path.

``StubRiskEngine`` scripted its probabilities and did everything else for real. This
replaces the scripted half and nothing else, which is why the swap is one line of
dependency wiring: the policy, the four-term arithmetic, the hard-rule pre-pass and the
full loss table were never stubbed.

The order of operations is the design, and it follows ADR-008 -- hard rules live outside
the optimiser:

1. **Deterministic egress rules first.** A canary token in generated text is an L5 stop
   with no model in the loop and no arithmetic consulted (invariant 6). The loss table
   is still computed and returned, because the console must be able to explain a hard
   stop rather than merely announce it.
2. **Signals, then calibration.** Raw grounding scores are computed, then mapped through
   the per-defect isotonic calibrators. Only calibrated values enter the objective
   (ADR-002); a raw score has no units and multiplying it by rupees produces a number
   that is precise and meaningless.
3. **The conformal feasibility filter.** When enabled, a sentence whose ``P(ungrounded)``
   is at or above the certified threshold cannot be passed -- ``L0_pass`` is struck from
   the available actions before the argmin runs. This is what converts the guarantee
   from a description of past data into a constraint on present behaviour.
4. **The argmin**, over what is left.

**Never raises, never overruns.** Any internal failure returns ``L0_pass`` with the
reason in ``why``. That is the contract, and it is the right default: the alternative to
a risk engine that fails open on its own bug is a proxy that stops serving traffic
because its guardrail crashed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from interlock.core.clock import Deadline
from interlock.core.ids import inputs_digest, new_decision_id
from interlock.core.policy import Policy
from interlock.core.types import Action, Decision, Defect, Fragment, RepairHint, RiskContext
from interlock.risk.calibration import MultiDefectCalibrator
from interlock.risk.conformal import ConformalResult
from interlock.risk.objective import HardRule, choose_action
from interlock.signals.grounding import GroundingScores, grounding_signals

__all__ = ["RealRiskEngine", "load_conformal"]

#: Defect classes the grounding signals can speak to. Anything else stays absent from
#: ``probs`` rather than being reported as zero -- "we did not check" is a different
#: claim from "we checked and found nothing", and the console must distinguish them.
_SUPPORTED_DEFECTS: tuple[Defect, ...] = ("ungrounded", "contradicted")

#: The action the conformal filter removes. Annotating is still permitted above the
#: threshold: it is an intervention, so it does not count as an escape.
_PASS: Action = "L0_pass"


def load_conformal(path: Path | str) -> ConformalResult | None:
    """Read ``lambda.json``. Returns None if it is missing or uncertified."""
    path = Path(path)
    if not path.exists():
        return None
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    result = ConformalResult(
        threshold=payload.get("threshold"),
        alpha=float(payload.get("alpha", 0.0)),
        delta=float(payload.get("delta", 0.0)),
        n_eval=int(payload.get("n_eval", 0)),
        escape_rate=payload.get("escape_rate"),
        intervention_rate=payload.get("intervention_rate"),
        notes=list(payload.get("notes", [])),
    )
    return result if result.certified else None


@dataclass
class RealRiskEngine:
    """Calibrated probabilities, priced through the same objective as the stub."""

    policy: Policy
    calibrator: MultiDefectCalibrator | None = None
    conformal: ConformalResult | None = None
    canary_detector: Any | None = None
    #: A MiniCheck-class verifier (D2-B6). Absent here; when present it supplies the
    #: offending span, which is what L2 repair aims at.
    verifier: Any | None = None

    #: OFF by default, and that is a recorded decision rather than an oversight.
    #: The certified threshold currently intervenes on 100% of traffic (finding F-016),
    #: because 'unanswerable' failures are invisible to the deterministic signals. A
    #: filter that fires on everything is not a guarantee, it is a shutdown. Enable it
    #: with INTERLOCK_CONFORMAL_FILTER=1 to run in guaranteed mode, and expect the
    #: false-intervention metric to reflect the cost.
    conformal_filter: bool = False

    calib_version: str = "uncalibrated"
    probe_version: str = "none"

    _prefetched: set[str] = field(default_factory=set, init=False)
    _failures: int = field(default=0, init=False)

    # ------------------------------------------------------------------ #

    async def evaluate(self, ctx: RiskContext) -> Decision:
        deadline = Deadline(budget_ms=ctx.remaining_deadline_ms)
        try:
            return self._evaluate(ctx, deadline)
        except Exception as exc:
            self._failures += 1
            return self._degraded(ctx, f"risk engine error: {exc!r}", deadline)

    def _evaluate(self, ctx: RiskContext, deadline: Deadline) -> Decision:
        why: list[str] = []
        hard_rules = self._hard_rules(ctx, why)
        scores = grounding_signals(ctx.sentence, ctx.retrieved, question=ctx.question)
        probs, degraded = self._probabilities(scores, why)

        extra_unavailable = self._feasibility(probs, why)

        choice = choose_action(
            probs=probs,
            stakes=ctx.stakes,
            policy=self.policy,
            already_emitted=ctx.already_emitted,
            hard_rules=hard_rules,
            extra_unavailable=extra_unavailable,
        )

        return Decision(
            decision_id=new_decision_id(),
            action=choice.action,
            loss_table=choice.loss_table,
            chosen_loss=choice.chosen_loss,
            runner_up=choice.runner_up,
            margin=choice.margin,
            probs=probs,
            signals=scores.as_readings(latency_ms=deadline.elapsed_ms),
            why=why + choice.why,
            hard_rule=choice.hard_rule,
            repair_hint=self._repair_hint(ctx, scores, choice.action),
            degraded=degraded,
            policy_version=self.policy.policy_version,
            calib_version=self.calib_version,
            probe_version=self.probe_version,
            inputs_digest=inputs_digest(
                {
                    "request_id": ctx.request_id,
                    "sentence_idx": ctx.sentence_idx,
                    "sentence": ctx.sentence,
                    "question": ctx.question,
                    "stakes": ctx.stakes.model_dump(),
                    "already_emitted": ctx.already_emitted,
                }
            ),
            latency_ms=deadline.elapsed_ms,
        )

    # -- step 1: deterministic egress rules ----------------------------- #

    def _hard_rules(self, ctx: RiskContext, why: list[str]) -> list[HardRule]:
        """Invariant 6. A canary match on generated text is an L5 stop, full stop.

        This is the egress half of the canary mechanism. Planting a canary and never
        scanning the output is the failure mode where the control looks present in every
        code review and has never once fired.
        """
        if self.canary_detector is None:
            return []
        outcome = self.canary_detector.scan_egress(ctx.sentence)
        if not getattr(outcome, "hard_rules", None):
            return []
        why.extend(getattr(outcome, "findings", []))
        return list(outcome.hard_rules)

    # -- step 2: signals -> calibrated probabilities --------------------- #

    def _probabilities(
        self, scores: GroundingScores, why: list[str]
    ) -> tuple[dict[Defect, float], bool]:
        """Calibrated per-defect probabilities, or nothing at all.

        With no calibrator the engine reports **no** probabilities rather than raw
        scores. Feeding raw scores to an objective that multiplies them by rupees would
        produce a confident, auditable, meaningless number -- and it would do it
        silently, which is worse than the request being marked degraded.
        """
        if self.calibrator is None:
            why.append("degraded: no calibrator loaded; reporting no defect probabilities")
            return {}, True

        features = scores.as_features()
        raw = self.calibrator.predict(features)
        probs: dict[Defect, float] = {}
        for defect in _SUPPORTED_DEFECTS:
            value = raw.get(defect)
            if value is not None:
                probs[defect] = float(value)

        missing = [d for d in _SUPPORTED_DEFECTS if d not in probs]
        if missing:
            why.append(f"no calibrator for {', '.join(missing)}; those defects unpriced")
        return probs, bool(missing)

    # -- step 3: the conformal feasibility filter ------------------------ #

    def _feasibility(self, probs: dict[Defect, float], why: list[str]) -> dict[Action, str]:
        """Strike ``L0_pass`` when the guarantee says this sentence may not pass."""
        if self.conformal is None or self.conformal.threshold is None:
            return {}
        probability = probs.get("ungrounded")
        if probability is None:
            return {}

        if not self.conformal_filter:
            # Recorded even when off, so a trace shows what guaranteed mode WOULD have
            # done. Otherwise the only way to know is to re-run the whole request.
            if probability >= self.conformal.threshold:
                why.append(
                    f"conformal filter is OFF; in guaranteed mode this sentence "
                    f"(P={probability:.3f} >= {self.conformal.threshold:.3f}) could not pass"
                )
            return {}

        if probability < self.conformal.threshold:
            return {}
        why.append(
            f"conformal filter: P(ungrounded)={probability:.3f} >= "
            f"lambda={self.conformal.threshold:.3f}, so passing would break the "
            f"{self.conformal.alpha:.0%}-escape guarantee at "
            f"{1 - self.conformal.delta:.0%} confidence"
        )
        return {_PASS: "struck by the conformal feasibility filter"}

    # -- the repair target ---------------------------------------------- #

    def _repair_hint(
        self, ctx: RiskContext, scores: GroundingScores, action: Action
    ) -> RepairHint | None:
        """What the repair should aim at, when a repair is what was chosen.

        Built only for L2. A hint attached to every decision would be dead weight in
        every trace and every ledger row, and the span is only meaningful for the one
        action that uses it.
        """
        if action != "L2_repair":
            return None

        claims: list[str] = []
        if scores.unsupported_citations:
            claims.append(
                "cites " + ", ".join(f"Clause {c}" for c in scores.unsupported_citations)
                + ", which is not in the retrieved context"
            )
        if scores.unsupported_numbers:
            claims.append(
                "states the figure(s) "
                + ", ".join(scores.unsupported_numbers)
                + ", which appear nowhere in the retrieved context"
            )
        if not claims and scores.unsupported_content > 0.5:
            claims.append("is mostly not supported by any retrieved passage")

        evidence = [
            fragment.text
            for fragment in ctx.retrieved
            if not str(fragment.provenance).endswith("untrusted")
        ][:3]

        return RepairHint(
            span=(0, len(ctx.sentence)),
            unsupported_claim="; ".join(claims) or "not supported by the retrieved context",
            evidence=evidence,
        )

    # -- the contract's failure shape ------------------------------------ #

    def _degraded(self, ctx: RiskContext, reason: str, deadline: Deadline) -> Decision:
        """Touches nothing that could itself fail -- the policy may be what broke."""
        return Decision(
            decision_id=new_decision_id(),
            action="L0_pass",
            loss_table=[],
            chosen_loss=0.0,
            why=[f"degraded: {reason}"],
            degraded=True,
            policy_version=getattr(self.policy, "policy_version", "unknown"),
            calib_version=self.calib_version,
            probe_version=self.probe_version,
            latency_ms=deadline.elapsed_ms,
        )

    # -- the rest of the Protocol ---------------------------------------- #

    async def prefetch(
        self, request_id: str, question: str, retrieved: list[Fragment]
    ) -> None:
        """No observer weights yet, so nothing to warm. The call path stays exercised."""
        self._prefetched.add(request_id)

    def arm(self, request_id: str, header: str | None) -> None:
        """Accepted and ignored.

        The gateway calls this on every request to drive the stub's forced decisions.
        The real engine has no forced decisions -- but accepting the call keeps the swap
        to one line of wiring, which is what Contract 1 was frozen to guarantee.
        """
        return None

    def disarm(self, request_id: str) -> None:
        self._prefetched.discard(request_id)

    def health(self) -> dict[str, object]:
        return {
            "engine": "real",
            "policy_version": self.policy.policy_version,
            "calib_version": self.calib_version,
            "probe_version": self.probe_version,
            "calibrated_defects": sorted(self.calibrator.per_defect) if self.calibrator else [],
            "conformal_threshold": self.conformal.threshold if self.conformal else None,
            "conformal_filter": self.conformal_filter,
            "internal_failures": self._failures,
        }
