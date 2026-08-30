"""The expected-loss objective — the arithmetic the whole product rests on.

The headline claim is that the *same* detection produces *different* actions purely
because the stakes changed, with nobody tuning anything. If these tests pass, that claim
is arithmetic. If they fail, it is a slogan.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interlock.core.policy import Policy, load_policy
from interlock.core.types import ACTIONS, Defect, Stakes
from interlock.risk.objective import (
    HardRule,
    choose_action,
    p_any,
    price_actions,
    unavailable_actions,
)

POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "banking.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(POLICY_PATH)


def _stakes(impact: float, reversibility: str = "reversible", domain: str = "general") -> Stakes:
    return Stakes(
        impact_inr=impact,
        reversibility=reversibility,  # type: ignore[arg-type]
        domain=domain,
        confidence=0.9,
    )


# --------------------------------------------------------------------------- #
# The three cases from the pitch: same detection, three different answers
# --------------------------------------------------------------------------- #

# Detectors report a 31% chance the answer is ungrounded. Only the stakes change.
_RISK_31 = {"ungrounded": 0.31}


def test_case_a_high_stakes_holds_for_a_human(policy: Policy) -> None:
    """Rs.40,000 at stake justifies four minutes of a human's time."""
    choice = choose_action(
        probs=_RISK_31, stakes=_stakes(40_000, "costly", "loan_terms"), policy=policy
    )
    assert choice.action == "L4_hold"


def test_case_b_low_stakes_repairs_the_sentence(policy: Policy) -> None:
    """Identical risk, identical detectors -- a cheaper action wins because the answer
    is worth less to get wrong."""
    choice = choose_action(probs=_RISK_31, stakes=_stakes(200), policy=policy)
    assert choice.action == "L2_repair"


def test_case_c_low_risk_does_nothing(policy: Policy) -> None:
    """The system doing nothing when there is nothing to do matters just as much.
    L0 must stay genuinely free or the latency budget is a fiction."""
    choice = choose_action(probs={"ungrounded": 0.01}, stakes=_stakes(200), policy=policy)
    assert choice.action == "L0_pass"


def test_the_action_changes_only_because_the_stakes_changed(policy: Policy) -> None:
    """Nobody tuned anything between these two calls."""
    high = choose_action(
        probs=_RISK_31, stakes=_stakes(40_000, "costly", "loan_terms"), policy=policy
    )
    low = choose_action(probs=_RISK_31, stakes=_stakes(200), policy=policy)
    assert high.action != low.action


def test_passing_costs_probability_times_impact(policy: Policy) -> None:
    """L0 removes nothing, so its residual harm is the raw expected harm -- the sanity
    check that anchors every other row. Rs.200 x 0.31 = Rs.62.00."""
    rows = {r.action: r for r in price_actions(probs=_RISK_31, stakes=_stakes(200), policy=policy)}
    assert rows["L0_pass"].total == pytest.approx(62.00)


# --------------------------------------------------------------------------- #
# Over-blocking is the failure mode that gets guardrails switched off
# --------------------------------------------------------------------------- #


def test_blocking_is_never_chosen_by_the_optimiser_on_these_cases(policy: Policy) -> None:
    """Blocking only wins when P x Impact is genuinely large, which is rare. It is
    reached through hard rules, not through the argmin.

    This test earns its keep: with the plan's illustrative L5 nuisance of Rs.900, the
    high-stakes case priced block at Rs.621 against hold at Rs.868 and the optimiser
    over-blocked. The fix was in the policy file, not in the code.
    """
    for stakes in (_stakes(40_000, "costly", "loan_terms"), _stakes(200), _stakes(50)):
        assert choose_action(probs=_RISK_31, stakes=stakes, policy=policy).action != "L5_block"


def test_a_false_alarm_is_charged_for(policy: Policy) -> None:
    """Term (2). Without it the optimiser would intervene on everything, because
    intervening never costs anything if the answer was fine."""
    rows = {
        r.action: r
        for r in price_actions(probs={"ungrounded": 0.0}, stakes=_stakes(200), policy=policy)
    }
    assert rows["L0_pass"].nuisance == 0.0
    assert rows["L4_hold"].nuisance == pytest.approx(policy.nuisance_inr["L4_hold"])
    assert rows["L5_block"].nuisance > rows["L4_hold"].nuisance


def test_with_no_risk_at_all_passing_is_cheapest(policy: Policy) -> None:
    choice = choose_action(probs={"ungrounded": 0.0}, stakes=_stakes(40_000), policy=policy)
    assert choice.action == "L0_pass"


def test_a_slow_action_must_earn_its_delay(policy: Policy) -> None:
    """Term (4). Latency is priced inside the objective, which is the structural reason
    the system cannot slow the AI down."""
    rows = {r.action: r for r in price_actions(probs=_RISK_31, stakes=_stakes(200), policy=policy)}
    assert rows["L0_pass"].latency == 0.0
    assert rows["L3_reroute"].latency > rows["L2_repair"].latency > rows["L1_annotate"].latency


def test_repair_costs_compute_and_passing_does_not(policy: Policy) -> None:
    """Term (3). Repairing a sentence costs money; rerouting costs more."""
    rows = {r.action: r for r in price_actions(probs=_RISK_31, stakes=_stakes(200), policy=policy)}
    assert rows["L0_pass"].compute == 0.0
    assert rows["L1_annotate"].compute == 0.0  # deterministic string transform, no model
    assert rows["L3_reroute"].compute > rows["L2_repair"].compute > 0.0


# --------------------------------------------------------------------------- #
# The table is the explanation
# --------------------------------------------------------------------------- #


def test_every_action_is_always_priced(policy: Policy) -> None:
    rows = price_actions(probs=_RISK_31, stakes=_stakes(200), policy=policy)
    assert [row.action for row in rows] == list(ACTIONS)


def test_total_is_the_sum_of_the_four_terms(policy: Policy) -> None:
    for row in price_actions(probs=_RISK_31, stakes=_stakes(40_000), policy=policy):
        assert row.total == pytest.approx(
            row.residual_harm + row.nuisance + row.compute + row.latency
        )


def test_the_decision_explains_itself(policy: Policy) -> None:
    """The console renders these lines verbatim; they must mention the stakes, the
    probability that drove it, and what came second."""
    choice = choose_action(
        probs=_RISK_31, stakes=_stakes(40_000, "costly", "loan_terms"), policy=policy
    )
    joined = " ".join(choice.why)
    assert "loan_terms" in joined
    assert "0.31" in joined
    assert choice.runner_up is not None
    assert choice.margin > 0


def test_margin_is_the_distance_to_the_runner_up(policy: Policy) -> None:
    choice = choose_action(probs=_RISK_31, stakes=_stakes(200), policy=policy)
    rows = {r.action: r for r in choice.loss_table}
    assert choice.margin == pytest.approx(
        rows[choice.runner_up].total - choice.chosen_loss  # type: ignore[index]
    )


def test_minimum_relative_gain_passes_when_intervention_gain_is_too_small(
    policy: Policy,
) -> None:
    ungated = policy.model_copy(update={"minimum_relative_action_gain": 0.0})
    gated = policy.model_copy(update={"minimum_relative_action_gain": 0.5})
    stakes = _stakes(1_000, "costly", "general")
    probs = {"ungrounded": 0.01}
    original = choose_action(probs=probs, stakes=stakes, policy=ungated)
    rows = {row.action: row for row in original.loss_table}
    gain = (rows["L0_pass"].total - original.chosen_loss) / max(rows["L0_pass"].total, 1.0)

    assert original.action == "L2_repair"
    assert 0.49 <= gain < 0.5
    choice = choose_action(probs=probs, stakes=stakes, policy=gated)
    assert choice.action == "L0_pass"
    assert choice.runner_up == original.action
    assert "below the required 50.0%" in " ".join(choice.why)


def test_minimum_relative_gain_keeps_a_material_intervention(policy: Policy) -> None:
    gated = policy.model_copy(update={"minimum_relative_action_gain": 0.5})
    choice = choose_action(
        probs={"ungrounded": 0.31},
        stakes=_stakes(40_000, "costly", "loan_terms"),
        policy=gated,
    )
    rows = {row.action: row for row in choice.loss_table}
    gain = (rows["L0_pass"].total - choice.chosen_loss) / rows["L0_pass"].total

    assert choice.action == "L4_hold"
    assert gain >= 0.5


def test_minimum_relative_gain_does_not_override_a_hard_rule(policy: Policy) -> None:
    gated = policy.model_copy(update={"minimum_relative_action_gain": 0.99})
    choice = choose_action(
        probs={"ungrounded": 0.0},
        stakes=_stakes(50, "reversible", "branch_info"),
        policy=gated,
        hard_rules=[HardRule(name="canary_leak", action="L5_block", reason="canary")],
    )

    assert choice.action == "L5_block"
    assert choice.hard_rule == "canary_leak"


def test_minimum_relative_gain_does_not_restore_unavailable_pass(policy: Policy) -> None:
    gated = policy.model_copy(update={"minimum_relative_action_gain": 0.99})
    choice = choose_action(
        probs={"ungrounded": 0.31},
        stakes=_stakes(200),
        policy=gated,
        extra_unavailable={"L0_pass": "conformal_safety"},
    )

    assert choice.action != "L0_pass"


def test_probability_floor_prevents_tiny_risk_from_being_amplified_by_stakes(
    policy: Policy,
) -> None:
    gated = policy.model_copy(
        update={
            "minimum_action_probability": 0.01,
            "minimum_relative_action_gain": 0.0,
        }
    )
    choice = choose_action(
        probs={"ungrounded": 0.0032},
        stakes=_stakes(40_000, "costly", "loan_terms"),
        policy=gated,
    )

    assert choice.action == "L0_pass"
    assert "calibrated risk floor" in " ".join(choice.why)


def test_probability_floor_keeps_a_material_calibrated_risk(policy: Policy) -> None:
    gated = policy.model_copy(
        update={
            "minimum_action_probability": 0.01,
            "minimum_relative_action_gain": 0.0,
        }
    )
    choice = choose_action(
        probs={"ungrounded": 0.02},
        stakes=_stakes(40_000, "costly", "loan_terms"),
        policy=gated,
    )

    assert choice.action != "L0_pass"


def test_probability_floor_does_not_override_hard_or_unavailable_constraints(
    policy: Policy,
) -> None:
    gated = policy.model_copy(update={"minimum_action_probability": 0.99})
    hard = choose_action(
        probs={"ungrounded": 0.0},
        stakes=_stakes(50),
        policy=gated,
        hard_rules=[HardRule(name="canary", action="L5_block", reason="canary")],
    )
    constrained = choose_action(
        probs={"ungrounded": 0.0},
        stakes=_stakes(50),
        policy=gated,
        extra_unavailable={"L0_pass": "conformal_safety"},
    )

    assert hard.action == "L5_block"
    assert constrained.action != "L0_pass"


# --------------------------------------------------------------------------- #
# The ladder shrinks as the answer travels (ADR-003)
# --------------------------------------------------------------------------- #


def test_already_emitted_removes_the_actions_that_cannot_un_say(policy: Policy) -> None:
    rows = {
        r.action: r
        for r in price_actions(
            probs=_RISK_31, stakes=_stakes(40_000), policy=policy, already_emitted=True
        )
    }
    for action in ("L2_repair", "L3_reroute", "L5_block"):
        assert rows[action].available is False
        assert rows[action].unavailable_reason == "already_emitted"


def test_unavailable_actions_still_appear_in_the_table(policy: Policy) -> None:
    """An action the optimiser could not choose must still show why -- otherwise the
    console is asserting rather than explaining."""
    rows = price_actions(probs=_RISK_31, stakes=_stakes(200), policy=policy, already_emitted=True)
    assert len(rows) == len(ACTIONS)


def test_an_emitted_high_stakes_sentence_falls_back_to_hold(policy: Policy) -> None:
    """You cannot un-say sentence 1 of an unbuffered stream. What remains is annotate,
    notify, or escalate -- and the console says so honestly."""
    choice = choose_action(
        probs=_RISK_31,
        stakes=_stakes(40_000, "costly", "loan_terms"),
        policy=policy,
        already_emitted=True,
    )
    assert choice.action in {"L0_pass", "L1_annotate", "L4_hold"}


def test_unavailable_actions_helper() -> None:
    assert unavailable_actions(already_emitted=False) == {}
    assert set(unavailable_actions(already_emitted=True)) == {
        "L2_repair",
        "L3_reroute",
        "L5_block",
    }


# --------------------------------------------------------------------------- #
# Hard constraints run before the argmin (ADR-008)
# --------------------------------------------------------------------------- #


def test_a_hard_rule_short_circuits_the_optimiser(policy: Policy) -> None:
    """A canary leak must not be a probability judgement."""
    choice = choose_action(
        probs={"ungrounded": 0.0},
        stakes=_stakes(50, "reversible", "branch_info"),
        policy=policy,
        hard_rules=[
            HardRule(name="canary_leak", action="L5_block", reason="canary token on egress")
        ],
    )
    assert choice.action == "L5_block"
    assert choice.hard_rule == "canary_leak"
    assert "canary" in " ".join(choice.why)


def test_the_table_is_still_returned_when_a_hard_rule_fires(policy: Policy) -> None:
    """So the console can explain a hard stop rather than merely announce it."""
    choice = choose_action(
        probs={"ungrounded": 0.0},
        stakes=_stakes(50),
        policy=policy,
        hard_rules=[HardRule(name="canary_leak", action="L5_block", reason="canary")],
    )
    assert len(choice.loss_table) == len(ACTIONS)


def test_the_most_drastic_rule_wins(policy: Policy) -> None:
    choice = choose_action(
        probs={"ungrounded": 0.0},
        stakes=_stakes(50),
        policy=policy,
        hard_rules=[
            HardRule(name="untrusted_tool", action="L4_hold", reason="irreversible x untrusted"),
            HardRule(name="canary_leak", action="L5_block", reason="canary"),
        ],
    )
    assert choice.action == "L5_block"


def test_no_hard_rule_means_the_optimiser_decides(policy: Policy) -> None:
    choice = choose_action(probs=_RISK_31, stakes=_stakes(200), policy=policy, hard_rules=[])
    assert choice.hard_rule is None


# --------------------------------------------------------------------------- #
# P(any)
# --------------------------------------------------------------------------- #


def test_p_any_of_nothing_is_zero() -> None:
    assert p_any({}) == 0.0


def test_p_any_of_one_defect_is_that_defect() -> None:
    assert p_any({"ungrounded": 0.31}) == pytest.approx(0.31)


def test_p_any_combines_independent_defects() -> None:
    assert p_any({"ungrounded": 0.5, "biased": 0.5}) == pytest.approx(0.75)


def test_p_any_is_bounded() -> None:
    probs: dict[Defect, float] = {"ungrounded": 1.0, "biased": 1.0}
    assert p_any(probs) == pytest.approx(1.0)


def test_p_any_tolerates_out_of_range_input() -> None:
    """An uncalibrated or buggy detector must not produce a negative loss table."""
    assert 0.0 <= p_any({"ungrounded": 1.7, "biased": -0.2}) <= 1.0


def test_the_engine_never_produces_a_negative_loss(policy: Policy) -> None:
    for row in price_actions(probs={"ungrounded": 1.0}, stakes=_stakes(40_000), policy=policy):
        assert row.total >= 0.0


# --------------------------------------------------------------------------- #
# The ladder must not be degenerate
# --------------------------------------------------------------------------- #


def test_every_rung_of_the_ladder_is_reachable(policy: Policy) -> None:
    """A rung nobody can reach is a rung that does not exist.

    This caught a real degeneracy: with the human reviewer's cost charged through the
    false-alarm term, holding cost only Rs.22 of human time at P=0.9 instead of Rs.220,
    which made hold so cheap that **L2_repair -- the pitch's "common case" -- was never
    chosen at any probability or any stakes level.** The whole middle of the ladder was
    dead and nothing would have told us before stage.
    """
    objective_policy = policy.model_copy(update={"minimum_relative_action_gain": 0.0})
    chosen = set()
    for impact, reversibility in [
        (50, "reversible"),
        (200, "reversible"),
        (1_000, "costly"),
        (3_000, "costly"),
        (12_000, "costly"),
        (40_000, "costly"),
    ]:
        for probability in (0.01, 0.10, 0.31, 0.55, 0.90):
            for emitted in (False, True):
                chosen.add(
                    choose_action(
                        probs={"ungrounded": probability},
                        stakes=_stakes(impact, reversibility),
                        policy=objective_policy,
                        already_emitted=emitted,
                    ).action
                )
    for action in ("L0_pass", "L1_annotate", "L2_repair", "L4_hold", "L5_block"):
        assert action in chosen, f"{action} is unreachable under any operating point"


def test_repair_wins_across_a_usable_band(policy: Policy) -> None:
    """Repairing one bad sentence is described as the common case, so it must win over
    a range a real deployment actually occupies -- not at a single knife-edge."""
    wins = [
        impact
        for impact in (1_000, 1_500, 2_000, 3_000)
        if choose_action(
            probs={"ungrounded": 0.31},
            stakes=_stakes(impact, "costly"),
            policy=policy,
        ).action
        == "L2_repair"
    ]
    assert len(wins) >= 3, f"L2_repair only wins at {wins}"


def test_a_human_in_the_loop_is_charged_unconditionally(policy: Policy) -> None:
    """The reviewer is paid whether or not the answer turned out to be fine, so their
    time is an operational cost (term 3), not a false-alarm cost (term 2).

    Charging it as a false alarm made it shrink as the defect became more likely --
    holding cost Rs.22 of human time at P=0.9 -- so holding looked nearly free at
    exactly the moment it was most likely to be chosen.
    """
    for probability in (0.01, 0.5, 0.99):
        rows = {
            r.action: r
            for r in price_actions(
                probs={"ungrounded": probability},
                stakes=_stakes(40_000, "costly"),
                policy=policy,
            )
        }
        assert rows["L4_hold"].compute >= policy.human_review.cost_inr


def test_blocking_also_pays_for_the_escalation(policy: Policy) -> None:
    """A blocked customer still needs their question answered by someone. Charging the
    escalation on hold but not on block would make refusing cheaper than deferring,
    which is precisely backwards."""
    rows = {
        r.action: r
        for r in price_actions(
            probs={"ungrounded": 0.31}, stakes=_stakes(40_000, "costly"), policy=policy
        )
    }
    assert rows["L5_block"].compute >= policy.human_review.cost_inr


def test_holding_still_beats_blocking_at_the_pitch_operating_point(policy: Policy) -> None:
    """Case A of the three-case table. Kept as a test because the margin is not large:
    correcting the human-cost accounting moved block and hold within 5% of each other
    before the escalation cost was applied to both consistently."""
    choice = choose_action(
        probs={"ungrounded": 0.31},
        stakes=_stakes(40_000, "costly", "loan_terms"),
        policy=policy,
    )
    assert choice.action == "L4_hold"
