"""F-019 sensitivity boundaries after the reviewed policy resolution.

Production requires at least 1% calibrated defect probability and a 50% expected-loss
gain before an ordinary intervention. These tests pin the governed floor while retaining
stakes sensitivity above it. Hard-rule and unavailable-action precedence are tested in
test_objective.py.
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
    # The governed 1% floor caps the two costliest bands without flattening low stakes.
    assert floors[0] / floors[-1] > 40
    assert floors[-2:] == pytest.approx([0.01, 0.01])


def test_low_stakes_traffic_is_servable_by_a_real_detector() -> None:
    """Branch-hours questions must pass with the detector we actually have.

    If this ever fails, Interlock has stopped being usable on the traffic that makes up
    most of a support queue, and the product is finished regardless of what the
    high-stakes numbers say.
    """
    floor = break_even_floor(_stakes(50, "reversible", "branch_info"))
    assert floor > 0.003, f"the current 0.2568% clean mean no longer clears {floor:.4f}"


def test_high_stakes_boundary_is_the_governed_probability_floor() -> None:
    """Tiny calibrated residuals cannot be amplified solely by monetary stakes."""
    floor = break_even_floor(_stakes(40_000, "costly", "prepayment"))
    assert floor == pytest.approx(0.01)


@pytest.mark.parametrize("impact", [3_000, 12_000, 40_000])
def test_current_clean_risk_passes_every_stakes_band(impact: int) -> None:
    """The measured 0.2568% mean is below the governed floor."""
    choice = choose_action(
        probs={"ungrounded": 0.002568},
        stakes=_stakes(impact, "costly", "fees"),
        policy=POLICY,
    )
    assert choice.action == "L0_pass"


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


def test_near_perfect_detector_passes_even_very_high_stakes() -> None:
    """The probability floor resolves the decision before relative-gain comparison."""
    choice = choose_action(
        probs={"ungrounded": 0.000_01},
        stakes=_stakes(120_000, "costly", "prepayment"),
        policy=POLICY,
    )
    assert choice.action == "L0_pass"
    assert choice.runner_up is None
    assert "calibrated risk floor" in " ".join(choice.why)
