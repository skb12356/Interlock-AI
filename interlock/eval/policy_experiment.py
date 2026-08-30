"""Pure policy-adjustment experiments for false-intervention reduction.

The production policy is never mutated here.  Candidate adjustments create temporary
Pydantic copies and reuse the shipped expected-loss objective, which keeps the comparison
about governance inputs rather than introducing a second decision implementation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import product
from typing import Any

from interlock.core.policy import DecisionAdjustment, Policy
from interlock.core.types import Action, Decision, Defect, RiskContext, Stakes
from interlock.eval.harness import CaseOutcome, EvalRun, compute_metrics
from interlock.eval.metrics import PRE_ACTION_ACTIONS
from interlock.eval.seeded import EvalCase
from interlock.risk.objective import ActionChoice, HardRule, choose_action

__all__ = [
    "BaselineTrace",
    "CandidateResult",
    "CandidateSeedResult",
    "PolicyAdjustment",
    "apply_adjustment",
    "candidate_matrix",
    "comparison_payload",
    "reference_action_regressions",
    "render_comparison_markdown",
    "replay_seed_candidate",
    "select_candidate",
]

IMPACT_SCALES: tuple[float, ...] = (1.0, 0.5, 0.25, 0.1, 0.05, 0.025)
PROBABILITY_DEADBANDS: tuple[float, ...] = (0.0, 0.0025, 0.005, 0.01, 0.015, 0.02)
NUISANCE_MULTIPLIERS: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0)


@dataclass(frozen=True, slots=True)
class PolicyAdjustment:
    """One auditable candidate applied only to probabilistic loss pricing."""

    impact_scale: float = 1.0
    probability_deadband: float = 0.0
    nuisance_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not 0 < self.impact_scale <= 1:
            raise ValueError("impact_scale must be in (0, 1]")
        if not 0 <= self.probability_deadband < 1:
            raise ValueError("probability_deadband must be in [0, 1)")
        if self.nuisance_multiplier < 1:
            raise ValueError("nuisance_multiplier must be at least 1")

    @property
    def distance(self) -> float:
        """Normalized distance from the neutral policy for deterministic tie-breaking."""
        return (
            (1.0 - self.impact_scale)
            + self.probability_deadband
            + (self.nuisance_multiplier - 1.0) / 50.0
        )


@dataclass(frozen=True, slots=True)
class CandidateSeedResult:
    seed: int
    catch_rate: float
    escape_count: int
    false_intervention_rate: float
    disruptive_rate: float
    action_counts: Mapping[str, int]


@dataclass(slots=True)
class CandidateResult:
    name: str
    adjustment: PolicyAdjustment
    seeds: tuple[CandidateSeedResult, ...]
    reference_action_regressions: tuple[str, ...] = ()
    eligible: bool = True
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def worst_disruptive(self) -> float:
        return max((row.disruptive_rate for row in self.seeds), default=1.0)

    @property
    def worst_false_intervention(self) -> float:
        return max((row.false_intervention_rate for row in self.seeds), default=1.0)


@dataclass(frozen=True, slots=True)
class BaselineTrace:
    """One real seeded run cached for pure candidate replay."""

    seed: int
    cases: tuple[EvalCase, ...]
    off: EvalRun
    on: EvalRun
    decisions: Mapping[str, Decision]
    contexts: Mapping[str, RiskContext]


def apply_adjustment(
    *,
    probs: Mapping[Defect, float],
    stakes: Stakes,
    policy: Policy,
    adjustment: PolicyAdjustment,
    already_emitted: bool = False,
    monetary_amount_inr: float = 0.0,
    hard_rules: Sequence[HardRule] = (),
    extra_unavailable: Mapping[Action, str] | None = None,
) -> ActionChoice:
    """Price one candidate without mutating request stakes or the loaded policy."""
    return choose_action(
        probs=probs,
        stakes=stakes,
        policy=policy,
        adjustment=DecisionAdjustment(
            impact_scale=adjustment.impact_scale,
            probability_deadband=adjustment.probability_deadband,
            nuisance_multiplier=adjustment.nuisance_multiplier,
        ),
        already_emitted=already_emitted,
        monetary_amount_inr=monetary_amount_inr,
        hard_rules=hard_rules,
        extra_unavailable=extra_unavailable,
    )


def reference_action_regressions(
    *, policy: Policy, adjustment: PolicyAdjustment
) -> tuple[str, ...]:
    """Return changes to the three product-defining objective examples.

    These examples are longstanding objective contracts, not rows selected from the
    evaluation benchmark.  Candidate selection may reduce nuisance only while the
    high-stakes Hold, low-stakes Repair, and low-risk Pass remain intact.
    """
    cases = (
        (
            "high_stakes_hold",
            {"ungrounded": 0.31},
            Stakes(
                impact_inr=40_000,
                reversibility="costly",
                domain="loan_terms",
                confidence=0.9,
            ),
            "L4_hold",
        ),
        (
            "low_stakes_repair",
            {"ungrounded": 0.31},
            Stakes(
                impact_inr=200,
                reversibility="reversible",
                domain="general",
                confidence=0.9,
            ),
            "L2_repair",
        ),
        (
            "low_risk_pass",
            {"ungrounded": 0.01},
            Stakes(
                impact_inr=200,
                reversibility="reversible",
                domain="general",
                confidence=0.9,
            ),
            "L0_pass",
        ),
    )
    regressions: list[str] = []
    for name, probs, stakes, expected in cases:
        baseline = choose_action(probs=probs, stakes=stakes, policy=policy).action
        if baseline != expected:
            raise ValueError(f"reference policy contract changed: {name}:{expected}->{baseline}")
        adjusted = apply_adjustment(
            probs=probs,
            stakes=stakes,
            policy=policy,
            adjustment=adjustment,
        ).action
        if adjusted != expected:
            regressions.append(f"{name}:{expected}->{adjusted}")
    return tuple(regressions)


def select_candidate(
    results: Sequence[CandidateResult], *, baseline_escape_by_seed: Mapping[int, int]
) -> CandidateResult:
    """Select the safest cross-seed Pareto candidate using worst-seed metrics."""
    if not results:
        raise ValueError("at least one policy candidate is required")

    for candidate in results:
        candidate.eligible = True
        candidate.rejection_reasons.clear()
        seen = {row.seed for row in candidate.seeds}
        required = set(baseline_escape_by_seed)
        if seen != required:
            candidate.rejection_reasons.append("seed_coverage_mismatch")
        candidate.rejection_reasons.extend(
            f"reference_action_regression:{regression}"
            for regression in candidate.reference_action_regressions
        )
        for row in candidate.seeds:
            if row.catch_rate < 0.9:
                candidate.rejection_reasons.append(f"catch_below_90_percent:{row.seed}")
            if row.escape_count > baseline_escape_by_seed.get(row.seed, -1):
                candidate.rejection_reasons.append(f"escape_regression:{row.seed}")
        candidate.eligible = not candidate.rejection_reasons

    eligible = [candidate for candidate in results if candidate.eligible]
    if not eligible:
        raise ValueError("no candidate satisfies every seed safety gate")

    for candidate in eligible:
        for other in eligible:
            if other is candidate:
                continue
            no_worse = (
                other.worst_disruptive <= candidate.worst_disruptive
                and other.worst_false_intervention <= candidate.worst_false_intervention
            )
            strictly_better = (
                other.worst_disruptive < candidate.worst_disruptive
                or other.worst_false_intervention < candidate.worst_false_intervention
            )
            if no_worse and strictly_better:
                candidate.eligible = False
                candidate.rejection_reasons.append(f"pareto_dominated:{other.name}")
                break

    frontier = [candidate for candidate in eligible if candidate.eligible]
    return min(
        frontier,
        key=lambda candidate: (
            candidate.worst_disruptive,
            candidate.worst_false_intervention,
            candidate.adjustment.distance,
            candidate.name,
        ),
    )


def replay_seed_candidate(
    trace: BaselineTrace, *, policy: Policy, adjustment: PolicyAdjustment
) -> CandidateSeedResult:
    """Replay one candidate over cached probabilities and immutable safety outcomes."""
    by_case = {case.case_id: case for case in trace.cases}
    adjusted_outcomes: list[CaseOutcome] = []
    price_per_token = policy.price_inr_per_1k_tokens / 1000.0

    for baseline in trace.on.outcomes:
        case = by_case[baseline.case_id]
        request_id = f"eval_{baseline.case_id}"
        decision = trace.decisions[request_id]
        context = trace.contexts[request_id]
        if decision.hard_rule is not None:
            action = decision.action
            why = tuple(decision.why)
        else:
            choice = apply_adjustment(
                probs=decision.probs,
                stakes=context.stakes,
                policy=policy,
                adjustment=adjustment,
                already_emitted=context.already_emitted,
            )
            action = choice.action
            why = tuple(choice.why)

        caught = action in PRE_ACTION_ACTIONS
        if case.should_freeze_tool:
            caught = baseline.tool_frozen
        if case.loop_calls:
            caught = baseline.caught_pre_action
        adjusted_outcomes.append(
            replace(
                baseline,
                action=action,
                intervened=action != "L0_pass",
                caught_pre_action=caught,
                verification_spend_inr=policy.compute_tokens[action] * price_per_token,
                why=why,
            )
        )

    adjusted = EvalRun(label=f"candidate:{adjustment}", outcomes=adjusted_outcomes)
    metrics = compute_metrics(off=trace.off, on=adjusted, cases=list(trace.cases), policy=policy)
    catch = metrics.by_name("Pre-Action Catch Rate")
    false_interventions = metrics.by_name("False interventions")
    disruptive = metrics.by_name("  ...of those, disruptive")
    if catch is None or false_interventions is None or disruptive is None:
        raise ValueError("seeded metrics did not produce the required safety rows")
    grounding = [
        outcome
        for outcome in adjusted.outcomes
        if by_case[outcome.case_id].expected_defect in {"ungrounded", "contradicted"}
    ]
    return CandidateSeedResult(
        seed=trace.seed,
        catch_rate=catch.value,
        escape_count=sum(outcome.escaped for outcome in grounding),
        false_intervention_rate=false_interventions.value,
        disruptive_rate=disruptive.value,
        action_counts=dict(Counter(outcome.action for outcome in adjusted.outcomes)),
    )


def candidate_matrix() -> list[CandidateResult]:
    """Return the complete bounded Cartesian comparison declared by the design."""
    candidates: list[CandidateResult] = []
    for impact, deadband, nuisance in product(
        IMPACT_SCALES, PROBABILITY_DEADBANDS, NUISANCE_MULTIPLIERS
    ):
        adjustment = PolicyAdjustment(
            impact_scale=impact,
            probability_deadband=deadband,
            nuisance_multiplier=nuisance,
        )
        name = f"impact-{impact:g}_deadband-{deadband:g}_nuisance-{nuisance:g}"
        candidates.append(CandidateResult(name=name, adjustment=adjustment, seeds=()))
    return candidates


def _seed_payload(row: CandidateSeedResult) -> dict[str, Any]:
    return {
        "seed": row.seed,
        "catch_rate": row.catch_rate,
        "escape_count": row.escape_count,
        "false_intervention_rate": row.false_intervention_rate,
        "disruptive_rate": row.disruptive_rate,
        "action_counts": dict(row.action_counts),
    }


def _candidate_payload(candidate: CandidateResult) -> dict[str, Any]:
    return {
        "name": candidate.name,
        "adjustment": {
            "impact_scale": candidate.adjustment.impact_scale,
            "probability_deadband": candidate.adjustment.probability_deadband,
            "nuisance_multiplier": candidate.adjustment.nuisance_multiplier,
            "distance_from_neutral": candidate.adjustment.distance,
        },
        "eligible": candidate.eligible,
        "reference_action_regressions": list(candidate.reference_action_regressions),
        "rejection_reasons": list(candidate.rejection_reasons),
        "worst_seed_disruptive_rate": candidate.worst_disruptive,
        "worst_seed_false_intervention_rate": candidate.worst_false_intervention,
        "seeds": [_seed_payload(row) for row in candidate.seeds],
    }


def comparison_payload(
    candidates: Sequence[CandidateResult], *, selected: CandidateResult
) -> dict[str, Any]:
    """Build an evidence payload that retains the complete attempted search."""
    return {
        "schema_version": 1,
        "source": "generated seeded evaluation",
        "selected_on_production_traffic": False,
        "selection_rules": {
            "minimum_catch_rate_each_seed": 0.9,
            "escape_regression_allowed": False,
            "reference_action_regression_allowed": False,
            "ranking": [
                "worst_seed_disruptive_rate",
                "worst_seed_false_intervention_rate",
                "smallest_adjustment_distance",
            ],
        },
        "selected": _candidate_payload(selected),
        "candidates": [_candidate_payload(candidate) for candidate in candidates],
    }


def render_comparison_markdown(payload: Mapping[str, Any]) -> str:
    """Render selected and rejected evidence without implying production validation."""
    selected = payload["selected"]
    assert isinstance(selected, Mapping)
    lines = [
        "# False-intervention policy comparison",
        "",
        "> Source: generated seeded evaluation; this is not production traffic.",
        "",
        f"Selected candidate: **{selected['name']}**",
        "",
        "| Candidate | Eligible | Worst disruptive | Worst any intervention | Rejection |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, Sequence):
        raise TypeError("comparison candidates must be a sequence")
    for raw in candidates:
        if not isinstance(raw, Mapping):
            raise TypeError("comparison candidate must be an object")
        reasons = raw.get("rejection_reasons", [])
        reason_text = ", ".join(str(reason) for reason in reasons) or "-"
        lines.append(
            f"| {raw['name']} | {'yes' if raw['eligible'] else 'no'} | "
            f"{float(raw['worst_seed_disruptive_rate']):.2%} | "
            f"{float(raw['worst_seed_false_intervention_rate']):.2%} | {reason_text} |"
        )
    return "\n".join(lines) + "\n"
