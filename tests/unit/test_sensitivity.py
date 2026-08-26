"""F-019, locked in.

The false-intervention rate is 91% against a ≤2% target, and there are two very
different stories that fit that number:

1. *the detector is weak* — improve it and the target is met;
2. *the objective is misspecified* — no detector can meet it.

They imply completely different next actions, so the difference is worth an experiment
rather than an opinion. `scripts/sensitivity.py` runs it by stipulating a detector and
sweeping its clean-text floor through the real policy and the real ladder.

These tests pin what that experiment found. They exist mainly so that a later attempt to
"fix" the metric by adjusting `impact_inr` or `lambda_time` fails loudly here, with a
docstring explaining why the number is what it is. CLAUDE.md is explicit that F-002 must
not be tuned away, and this is F-002 with an argument attached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interlock.core.policy import load_policy
from interlock.core.types import Stakes
from interlock.risk.objective import choose_action

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(REPO_ROOT / "policies" / "banking.yaml")


def break_even_floor(stakes: Stakes) -> float:
    """Largest P(ungrounded) at which L0_pass still wins. Bisection on a monotone
    predicate — raising P can only make passing worse, so there is one crossing."""
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if (
            choose_action(probs={"ungrounded": mid}, stakes=stakes, policy=POLICY).action
            == "L0_pass"
        ):
            low = mid
        else:
            high = mid
    return low


def _stakes(impact: float, reversibility: str, domain: str) -> Stakes:
    return Stakes(
        impact_inr=impact,
        reversibility=reversibility,  # type: ignore[arg-type]
        domain=domain,
        confidence=0.9,
    )


# --------------------------------------------------------------------------- #
# The structural property
# --------------------------------------------------------------------------- #


def test_the_required_detector_quality_tightens_as_stakes_rise() -> None:
    """The whole of F-019 in one assertion.

    Expected harm scales with impact; the cost of checking is nearly constant. So the
    probability at which passing stops being worth it falls as the stakes rise, and it
    falls fast. This is correct decision theory, and it is *why* high-stakes traffic
    cannot pass — not a bug, and not something a threshold change repairs.
    """
    floors = [
        break_even_floor(_stakes(50, "reversible", "branch_info")),
        break_even_floor(_stakes(200, "reversible", "general")),
        break_even_floor(_stakes(3_000, "costly", "fees")),
        break_even_floor(_stakes(40_000, "costly", "prepayment")),
    ]
    assert floors == sorted(floors, reverse=True), floors
    # Two orders of magnitude between the cheapest and dearest band.
    assert floors[0] / floors[-1] > 100


def test_low_stakes_traffic_is_servable_by_a_real_detector() -> None:
    """Branch-hours questions must pass with the detector we actually have.

    If this ever fails, Interlock has stopped being usable on the traffic that makes up
    most of a support queue, and the product is finished regardless of what the
    high-stakes numbers say.
    """
    floor = break_even_floor(_stakes(50, "reversible", "branch_info"))
    assert floor > 0.019, f"the real detector's ~1.9% clean floor no longer clears {floor:.4f}"


def test_high_stakes_traffic_demands_a_detector_nobody_has() -> None:
    """Recorded, not fixed.

    At ₹40,000 the objective needs P(clean) below roughly 1-in-30,000 before it will
    pass. No lexical detector is close, and the observer probe will not be either. That
    is the finding; the resolution is a decision about the impact model, taken
    deliberately, and NOT a quiet edit to this number.
    """
    floor = break_even_floor(_stakes(40_000, "costly", "prepayment"))
    assert floor < 0.001, floor
    assert floor < 1 / 10_000


@pytest.mark.parametrize("impact", [3_000, 12_000, 40_000])
def test_the_current_detector_cannot_pass_anything_above_low_stakes(impact: int) -> None:
    """The measured 91% false-intervention rate, derived rather than observed."""
    floor = break_even_floor(_stakes(impact, "costly", "fees"))
    assert floor < 0.019, "the real detector would clear this band, contradicting the eval"


# --------------------------------------------------------------------------- #
# What the experiment concluded, and what it did NOT conclude
# --------------------------------------------------------------------------- #


def test_a_near_perfect_detector_makes_the_disruptive_target_reachable() -> None:
    """The result that stops F-019 being a dead end.

    At a clean floor of 0.001% the objective chooses L0_pass or L1_annotate on clean
    traffic at *every* stakes band -- no repair, no reroute, no hold, no block. So the
    disruptive false-intervention target is achievable, and "build a better probe" is a
    real answer rather than wishful thinking.
    """
    disruptive = {"L2_repair", "L3_reroute", "L4_hold", "L5_block"}
    for impact, reversibility, domain in (
        (50, "reversible", "branch_info"),
        (200, "reversible", "general"),
        (3_000, "costly", "fees"),
        (12_000, "costly", "claims"),
        (40_000, "costly", "prepayment"),
        (120_000, "costly", "prepayment"),
    ):
        action = choose_action(
            probs={"ungrounded": 0.000_01},
            stakes=_stakes(impact, reversibility, domain),
            policy=POLICY,
        ).action
        assert action not in disruptive, f"Rs.{impact}: {action}"


def test_annotation_is_what_remains_and_it_is_not_a_disruption() -> None:
    """The entire residual gap between 9.55% and the 2% target, in one assertion.

    Even with a near-perfect detector the optimiser still annotates very high-stakes
    answers. L1 appends a citation and ships the answer otherwise unchanged, for 5 ms
    of added latency and Rs.0.50 of modelled nuisance. Whether that is a "false
    intervention" is a definitional question -- and it is worth being explicit that the
    answer is not obvious, rather than quietly picking whichever reading passes.
    """
    action = choose_action(
        probs={"ungrounded": 0.000_01},
        stakes=_stakes(120_000, "costly", "prepayment"),
        policy=POLICY,
    ).action
    assert action == "L1_annotate"
    assert POLICY.latency_ms["L1_annotate"] <= 5
    assert POLICY.compute_tokens["L1_annotate"] == 0, "annotation must not call a model"
    assert POLICY.nuisance_inr["L1_annotate"] < POLICY.nuisance_inr["L2_repair"]
