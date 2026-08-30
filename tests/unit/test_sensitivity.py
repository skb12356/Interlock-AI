"""F-019 sensitivity boundaries after the reviewed policy resolution.

The original objective produced roughly 91% false interventions. Production now
requires an intervention to reduce expected loss by at least 50% relative to passing.
These tests retain the stakes-sensitivity invariant and pin the intended abstention
behavior. Hard-rule and unavailable-action precedence are tested in test_objective.py.
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


def test_high_stakes_boundary_is_relaxed_but_remains_conservative() -> None:
    """The policy margin moves the boundary without erasing stakes sensitivity.

    At Rs.40,000 the pre-F-019 objective required roughly 1-in-30,000. The reviewed
    50% relative-gain rule moves that to roughly 1-in-4,000: still conservative, but an
    immaterial arithmetic win no longer forces an intervention.
    """
    floor = break_even_floor(_stakes(40_000, "costly", "prepayment"))
    assert floor < 0.001, floor
    assert floor > 1 / 10_000


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


def test_near_perfect_detector_passes_even_very_high_stakes() -> None:
    """An immaterial annotation no longer wins only because it is slightly cheaper.

    The raw argmin would annotate this answer. Its expected-loss reduction is below the
    reviewed 50% policy margin, so the production decision is L0_PASS.
    """
    choice = choose_action(
        probs={"ungrounded": 0.000_01},
        stakes=_stakes(120_000, "costly", "prepayment"),
        policy=POLICY,
    )
    assert choice.action == "L0_pass"
    assert choice.runner_up == "L1_annotate"
    assert "policy margin" in " ".join(choice.why)
