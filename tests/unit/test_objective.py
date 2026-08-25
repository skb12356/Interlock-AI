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
