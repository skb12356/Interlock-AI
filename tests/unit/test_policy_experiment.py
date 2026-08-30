"""Regression tests for measured false-intervention policy selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from interlock.core.policy import load_policy
from interlock.core.types import Decision, RiskContext, Stakes
from interlock.eval.harness import CaseOutcome, EvalRun
from interlock.eval.policy_experiment import (
    BaselineTrace,
    CandidateResult,
    CandidateSeedResult,
    PolicyAdjustment,
    apply_adjustment,
    candidate_matrix,
    comparison_payload,
    render_comparison_markdown,
    replay_seed_candidate,
    select_candidate,
)
from interlock.eval.seeded import EvalCase
from interlock.risk.objective import HardRule, choose_action

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "policies" / "banking.yaml")


def _stakes() -> Stakes:
    return Stakes(
        impact_inr=40_000,
        reversibility="costly",
        domain="prepayment",
        confidence=0.9,
    )


def test_adjustment_changes_only_effective_pricing_inputs() -> None:
    """Catches mutating the request-wide stakes object while pricing one sentence."""
    stakes = _stakes()

    choice = apply_adjustment(
        probs={"ungrounded": 0.02},
        stakes=stakes,
        policy=POLICY,
        adjustment=PolicyAdjustment(
            impact_scale=0.1,
            probability_deadband=0.005,
            nuisance_multiplier=2.0,
        ),
    )

    assert stakes.impact_inr == 40_000
    assert any("effective impact Rs.4,000" in reason for reason in choice.why)
    assert any("probability deadband 0.005" in reason for reason in choice.why)
    assert any("nuisance multiplier 2" in reason for reason in choice.why)


def test_probability_deadband_clamps_at_zero() -> None:
    """Catches allowing negative adjusted probabilities to create negative harm."""
    choice = apply_adjustment(
        probs={"ungrounded": 0.01},
        stakes=_stakes(),
        policy=POLICY,
        adjustment=PolicyAdjustment(probability_deadband=0.02),
    )

    assert choice.action == "L0_pass"
    assert all(row.residual_harm >= 0 for row in choice.loss_table)


def test_nuisance_multiplier_does_not_multiply_human_review_cost() -> None:
    """Catches hiding paid reviewer cost inside the false-alarm adjustment."""
    baseline = choose_action(probs={"ungrounded": 0.2}, stakes=_stakes(), policy=POLICY)
    adjusted = apply_adjustment(
        probs={"ungrounded": 0.2},
        stakes=_stakes(),
        policy=POLICY,
        adjustment=PolicyAdjustment(nuisance_multiplier=10.0),
    )

    baseline_hold = next(row for row in baseline.loss_table if row.action == "L4_hold")
    adjusted_hold = next(row for row in adjusted.loss_table if row.action == "L4_hold")
    assert adjusted_hold.compute == baseline_hold.compute == POLICY.human_review.cost_inr
    assert adjusted_hold.nuisance == pytest.approx(baseline_hold.nuisance * 10)


def test_adjustment_cannot_weaken_a_hard_rule() -> None:
    """Catches applying probabilistic tuning before deterministic canary enforcement."""
    choice = apply_adjustment(
        probs={"ungrounded": 0.0},
        stakes=_stakes(),
        policy=POLICY,
        adjustment=PolicyAdjustment(
            impact_scale=0.025,
            probability_deadband=0.02,
            nuisance_multiplier=50.0,
        ),
        hard_rules=(HardRule("canary", "L5_block", "tenant canary escaped"),),
    )

    assert choice.action == "L5_block"
    assert choice.hard_rule == "canary"


def _seed(
    seed: int,
    *,
    catch: float = 1.0,
    escapes: int = 0,
    false: float = 0.2,
    disruptive: float = 0.1,
) -> CandidateSeedResult:
    return CandidateSeedResult(
        seed=seed,
        catch_rate=catch,
        escape_count=escapes,
        false_intervention_rate=false,
        disruptive_rate=disruptive,
        action_counts={"L0_pass": 1},
    )


def _candidate(
    name: str,
    *,
    adjustment: PolicyAdjustment,
    seed_overrides: dict[int, dict[str, float | int]] | None = None,
    reference_action_regressions: tuple[str, ...] = (),
) -> CandidateResult:
    overrides = seed_overrides or {}
    seeds = tuple(
        _seed(
            seed,
            catch=float(overrides.get(seed, {}).get("catch", 1.0)),
            escapes=int(overrides.get(seed, {}).get("escapes", 0)),
            false=float(overrides.get(seed, {}).get("false", 0.2)),
            disruptive=float(overrides.get(seed, {}).get("disruptive", 0.1)),
        )
        for seed in (20260826, 20260827, 20260828)
    )
    return CandidateResult(
        name=name,
        adjustment=adjustment,
        seeds=seeds,
        reference_action_regressions=reference_action_regressions,
    )


def test_selection_rejects_a_candidate_that_misses_one_seed_safety_gate() -> None:
    """Catches averaging away one unsafe seed when selecting the winner."""
    unsafe = _candidate(
        "unsafe",
        adjustment=PolicyAdjustment(impact_scale=0.1),
        seed_overrides={20260828: {"catch": 0.89}},
    )
    safe = _candidate("safe", adjustment=PolicyAdjustment(impact_scale=0.25))

    selected = select_candidate(
        [unsafe, safe], baseline_escape_by_seed={s: 0 for s in (20260826, 20260827, 20260828)}
    )

    assert selected.name == "safe"
    assert unsafe.eligible is False
    assert "catch_below_90_percent:20260828" in unsafe.rejection_reasons


def test_selection_rejects_reference_action_regression() -> None:
    """Catches optimizing the benchmark by breaking the product's pitch contract."""
    unsafe = _candidate(
        "unsafe",
        adjustment=PolicyAdjustment(probability_deadband=0.015, nuisance_multiplier=50),
        reference_action_regressions=("high_stakes_hold:L4_hold->L2_repair",),
    )
    safe = _candidate(
        "safe",
        adjustment=PolicyAdjustment(probability_deadband=0.015, nuisance_multiplier=20),
    )

    selected = select_candidate(
        [unsafe, safe], baseline_escape_by_seed={s: 0 for s in (20260826, 20260827, 20260828)}
    )

    assert selected.name == "safe"
    assert unsafe.eligible is False
    assert (
        "reference_action_regression:high_stakes_hold:L4_hold->L2_repair"
        in unsafe.rejection_reasons
    )


def test_selection_rejects_escape_regression_and_pareto_dominance() -> None:
    """Catches selecting lower nuisance at the cost of a newly escaped answer."""
    escape = _candidate(
        "escape",
        adjustment=PolicyAdjustment(probability_deadband=0.02),
        seed_overrides={20260827: {"escapes": 1, "false": 0.0, "disruptive": 0.0}},
    )
    dominated = _candidate(
        "dominated",
        adjustment=PolicyAdjustment(impact_scale=0.1, nuisance_multiplier=5),
        seed_overrides={
            s: {"false": 0.3, "disruptive": 0.2} for s in (20260826, 20260827, 20260828)
        },
    )
    winner = _candidate(
        "winner",
        adjustment=PolicyAdjustment(impact_scale=0.25),
        seed_overrides={
            s: {"false": 0.2, "disruptive": 0.1} for s in (20260826, 20260827, 20260828)
        },
    )

    selected = select_candidate(
        [escape, dominated, winner],
        baseline_escape_by_seed={s: 0 for s in (20260826, 20260827, 20260828)},
    )

    assert selected.name == "winner"
    assert "escape_regression:20260827" in escape.rejection_reasons
    assert "pareto_dominated:winner" in dominated.rejection_reasons


def test_selection_uses_worst_seed_then_smallest_adjustment() -> None:
    """Catches ranking by a flattering mean or by candidate declaration order."""
    larger_change = _candidate(
        "larger",
        adjustment=PolicyAdjustment(impact_scale=0.1),
        seed_overrides={
            s: {"false": 0.1, "disruptive": 0.05} for s in (20260826, 20260827, 20260828)
        },
    )
    smaller_change = _candidate(
        "smaller",
        adjustment=PolicyAdjustment(impact_scale=0.5),
        seed_overrides={
            s: {"false": 0.1, "disruptive": 0.05} for s in (20260826, 20260827, 20260828)
        },
    )

    selected = select_candidate(
        [larger_change, smaller_change],
        baseline_escape_by_seed={s: 0 for s in (20260826, 20260827, 20260828)},
    )

    assert selected.name == "smaller"


def test_candidate_matrix_contains_every_declared_family_and_combination() -> None:
    """Catches silently dropping a method family from the release comparison."""
    candidates = candidate_matrix()

    assert len(candidates) == 216
    adjustments = {candidate.adjustment for candidate in candidates}
    assert PolicyAdjustment() in adjustments
    assert PolicyAdjustment(impact_scale=0.025) in adjustments
    assert PolicyAdjustment(probability_deadband=0.02) in adjustments
    assert PolicyAdjustment(nuisance_multiplier=50) in adjustments
    assert (
        PolicyAdjustment(impact_scale=0.1, probability_deadband=0.01, nuisance_multiplier=5)
        in adjustments
    )


def test_comparison_report_keeps_selected_and_rejected_evidence() -> None:
    """Catches rendering only the winner and hiding unsafe or dominated trials."""
    unsafe = _candidate(
        "unsafe",
        adjustment=PolicyAdjustment(probability_deadband=0.02),
        seed_overrides={20260828: {"catch": 0.8}},
    )
    selected = _candidate(
        "selected",
        adjustment=PolicyAdjustment(impact_scale=0.5),
        seed_overrides={
            s: {"false": 0.1, "disruptive": 0.05} for s in (20260826, 20260827, 20260828)
        },
    )
    winner = select_candidate(
        [unsafe, selected],
        baseline_escape_by_seed={s: 0 for s in (20260826, 20260827, 20260828)},
    )

    payload = comparison_payload([unsafe, selected], selected=winner)
    markdown = render_comparison_markdown(payload)

    assert payload["selected"]["name"] == "selected"
    assert payload["selected_on_production_traffic"] is False
    assert len(payload["candidates"]) == 2
    assert payload["candidates"][0]["rejection_reasons"] == ["catch_below_90_percent:20260828"]
    assert "unsafe" in markdown
    assert "selected" in markdown
    assert "generated seeded evaluation" in markdown


def test_replay_uses_cached_probabilities_and_preserves_safety_outcomes() -> None:
    """Catches re-running detectors per candidate or dropping tool/loop catches."""
    clean = EvalCase(
        case_id="clean",
        category="clean",
        question="q",
        answer="a",
    )
    defective = EvalCase(
        case_id="defect",
        category="missing_retrieval",
        question="q",
        answer="a",
        expected_defect="ungrounded",
        should_intervene=True,
    )
    cases = (clean, defective)
    contexts = {
        f"eval_{case.case_id}": RiskContext(
            request_id=f"eval_{case.case_id}",
            sentence_idx=0,
            sentence=case.answer,
            answer_prefix="",
            question=case.question,
            retrieved=[],
            stakes=_stakes(),
            already_emitted=False,
            remaining_deadline_ms=120,
        )
        for case in cases
    }
    decisions = {
        "eval_clean": Decision(
            decision_id="dec-clean",
            action="L2_repair",
            loss_table=[],
            chosen_loss=1,
            probs={"ungrounded": 0.01},
        ),
        "eval_defect": Decision(
            decision_id="dec-defect",
            action="L2_repair",
            loss_table=[],
            chosen_loss=1,
            probs={"ungrounded": 0.9},
        ),
    }
    off = EvalRun(
        label="off",
        outcomes=[
            CaseOutcome(
                case_id=case.case_id,
                category=case.category,
                action="L0_pass",
                intervened=False,
                caught_pre_action=False,
                tool_frozen=False,
                stakes_inr=40_000,
                tier="strong",
                overhead_ms=0,
                model_spend_inr=1,
                verification_spend_inr=0,
            )
            for case in cases
        ],
    )
    on = EvalRun(
        label="on",
        outcomes=[
            CaseOutcome(
                case_id=case.case_id,
                category=case.category,
                action="L2_repair",
                intervened=True,
                caught_pre_action=case.is_defective,
                tool_frozen=False,
                stakes_inr=40_000,
                tier="strong",
                overhead_ms=1,
                model_spend_inr=1,
                verification_spend_inr=1,
            )
            for case in cases
        ],
    )
    trace = BaselineTrace(
        seed=20260826,
        cases=cases,
        off=off,
        on=on,
        decisions=decisions,
        contexts=contexts,
    )

    result = replay_seed_candidate(
        trace,
        policy=POLICY,
        adjustment=PolicyAdjustment(probability_deadband=0.02),
    )

    assert result.catch_rate == 1
    assert result.escape_count == 0
    assert result.false_intervention_rate == 0
    assert result.disruptive_rate == 0
    assert result.action_counts == {"L0_pass": 1, "L5_block": 1}
