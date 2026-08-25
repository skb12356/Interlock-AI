"""Contract 4 — policy as code (Implementation03 §5).

The policy file is the governance artefact: it is what makes "who decided a wrong answer
is worth Rs.40,000?" answerable. These tests pin the shape, the failure behaviour, and the
arithmetic that reads from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interlock.core.errors import PolicyError
from interlock.core.policy import Policy, load_policy
from interlock.core.types import ACTIONS

POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "banking.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(POLICY_PATH)


# --------------------------------------------------------------------------- #
# Loading, versioning, validation
# --------------------------------------------------------------------------- #


def test_the_shipped_banking_policy_loads(policy: Policy) -> None:
    assert policy.version == "banking-v3"
    assert policy.currency == "INR"


def test_policy_version_is_the_digest_of_the_file(policy: Policy) -> None:
    """Stamped on every decision, so an auditor asking 'which version priced this?'
    gets an answer that cannot be fudged after the fact."""
    assert policy.policy_version.startswith("banking-v3@sha256:")


def test_policy_version_changes_when_the_file_changes(tmp_path: Path) -> None:
    original = POLICY_PATH.read_text(encoding="utf-8")
    a = tmp_path / "a.yaml"
    a.write_text(original, encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text(original.replace("impact_inr: 40000", "impact_inr: 45000"), encoding="utf-8")
    assert load_policy(a).policy_version != load_policy(b).policy_version


def test_invalid_policy_is_refused(tmp_path: Path) -> None:
    """Refuse to start rather than run on a policy nobody validated."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: x\nnot_a_real_field: 1\n", encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(bad)


def test_malformed_yaml_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="not valid YAML"):
        load_policy(bad)


def test_missing_policy_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="cannot read"):
        load_policy(tmp_path / "nope.yaml")


def test_every_action_must_be_priced(tmp_path: Path) -> None:
    """A missing row would silently make an action look free, and the argmin would
    choose it every time."""
    text = POLICY_PATH.read_text(encoding="utf-8").replace("  L4_hold: 220.0\n", "")
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PolicyError, match="every action must be priced"):
        load_policy(path)


def test_unknown_tools_must_have_a_default(tmp_path: Path) -> None:
    text = POLICY_PATH.read_text(encoding="utf-8").replace(
        "  default:             {reversibility: costly}\n", ""
    )
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PolicyError, match="default"):
        load_policy(path)


def test_all_six_actions_are_priced_for_nuisance_and_latency(policy: Policy) -> None:
    assert set(policy.nuisance_inr) == set(ACTIONS)
    assert set(policy.latency_ms) == set(ACTIONS)


# --------------------------------------------------------------------------- #
# The arithmetic the objective reads
# --------------------------------------------------------------------------- #


def test_impact_is_derived_not_typed_in(policy: Policy) -> None:
    """Impact_d = stakes.impact_inr x defect_multiplier x reversibility_multiplier."""
    impact = policy.impact_for(impact_inr=40000, defect="ungrounded", reversibility="costly")
    assert impact == pytest.approx(40000 * 1.0 * 2.5)


def test_irreversibility_multiplies_impact(policy: Policy) -> None:
    """This is why the tool interlock fires on requests a content filter waves through."""
    reversible = policy.impact_for(
        impact_inr=1000, defect="unsafe_action", reversibility="reversible"
    )
    irreversible = policy.impact_for(
        impact_inr=1000, defect="unsafe_action", reversibility="irreversible"
    )
    assert irreversible == pytest.approx(reversible * 8.0)


def test_canary_leak_is_the_most_expensive_defect(policy: Policy) -> None:
    multipliers = policy.stakes.multipliers.defect
    assert multipliers["canary_leak"] == max(multipliers.values())


def test_monetary_bands_take_the_highest_band_cleared(policy: Policy) -> None:
    assert policy.monetary_multiplier(0) == 1.0
    assert policy.monetary_multiplier(9_999) == 1.0
    assert policy.monetary_multiplier(10_000) == 1.5
    assert policy.monetary_multiplier(99_999) == 1.5
    assert policy.monetary_multiplier(1_000_000) == 3.0


def test_unknown_domain_falls_back_to_the_default_impact(policy: Policy) -> None:
    """Unknown must not mean expensive, or every unclassified query holds for a human."""
    assert policy.domain("astrology").impact_inr == policy.stakes.default_impact_inr


def test_known_domains_carry_their_own_stakes(policy: Policy) -> None:
    assert policy.domain("loan_terms").impact_inr == 40000
    assert policy.domain("branch_info").impact_inr == 50


def test_unknown_defect_action_pair_removes_nothing(policy: Policy) -> None:
    """Conservative by design: an action gets no credit for removing a defect nobody
    has measured it against."""
    assert policy.efficacy_for("L1_annotate", "canary_leak") == 0.0
    assert policy.efficacy_for("L0_pass", "ungrounded") == 0.0


def test_blocking_removes_everything_by_definition(policy: Policy) -> None:
    for defect in ("ungrounded", "contradicted", "canary_leak", "unsafe_action"):
        assert policy.efficacy_for("L5_block", defect) == 1.0  # type: ignore[arg-type]


def test_holding_beats_repairing_on_groundedness(policy: Policy) -> None:
    """The ladder must be monotone in strength, or the argmin will pick a weaker action
    for the wrong reason."""
    assert policy.efficacy_for("L4_hold", "ungrounded") > policy.efficacy_for(
        "L2_repair", "ungrounded"
    )
    assert policy.efficacy_for("L2_repair", "ungrounded") > policy.efficacy_for(
        "L1_annotate", "ungrounded"
    )


# --------------------------------------------------------------------------- #
# ADR-009: efficacy provenance is tracked, so a prior is never shown as a measurement
# --------------------------------------------------------------------------- #


def test_efficacy_entries_declare_where_they_came_from(policy: Policy) -> None:
    entry = policy.efficacy["L2_repair"]["ungrounded"]
    assert entry.value == 0.80
    assert entry.source == "prior"
    assert entry.measured is False


def test_day_one_ships_priors_not_measurements(policy: Policy) -> None:
    """This must flip to True at D3-B6, when each action is forced on the labelled set
    and the measured value is written back with a Wilson interval."""
    assert policy.uses_only_measured_efficacy is False


def test_a_bare_float_is_accepted_as_a_prior(tmp_path: Path) -> None:
    """The plan's example policy writes bare floats; the measured form needs an
    interval. Both must load."""
    text = POLICY_PATH.read_text(encoding="utf-8").replace(
        "    ungrounded:    {value: 0.80, source: prior}", "    ungrounded:    0.80"
    )
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    entry = load_policy(path).efficacy["L2_repair"]["ungrounded"]
    assert entry.value == 0.80
    assert entry.source == "prior"


# --------------------------------------------------------------------------- #
# Tools and guarantees
# --------------------------------------------------------------------------- #


def test_irreversible_tools_are_declared_not_inferred(policy: Policy) -> None:
    assert policy.tool("send_email").reversibility == "irreversible"
    assert policy.tool("transfer_funds").reversibility == "irreversible"
    assert policy.tool("lookup_balance").reversibility == "reversible"


def test_transfer_funds_never_runs_unattended(policy: Policy) -> None:
    assert policy.tool("transfer_funds").max_auto_inr == 0


def test_unknown_tool_defaults_to_costly(policy: Policy) -> None:
    """The safe direction -- and it is priced, so the optimiser will not freeze a
    low-stakes reversible call just because a fragment was untrusted."""
    assert policy.tool("some_tool_invented_at_runtime").reversibility == "costly"


def test_guarantees_translate_to_conformal_parameters(policy: Policy) -> None:
    """alpha = 0.01, delta = 0.10 -- the numbers the Learn-then-Test search must certify."""
    assert policy.guarantees.alpha == pytest.approx(0.01)
    assert policy.guarantees.delta == pytest.approx(0.10)


def test_one_threshold_drives_both_budgets(policy: Policy) -> None:
    """Contribution 1, in its most literal form: the same stakes estimate decides both
    which model runs and whether the commit buffer engages."""
    assert policy.thresholds.buffer_above_impact_inr == 1000
    assert policy.thresholds.strong_model_above_impact_inr == 1000


def test_a_high_stakes_domain_clears_both_thresholds(policy: Policy) -> None:
    loan = policy.domain("loan_terms").impact_inr
    assert loan >= policy.thresholds.buffer_above_impact_inr
    assert loan >= policy.thresholds.strong_model_above_impact_inr


def test_a_low_stakes_domain_clears_neither(policy: Policy) -> None:
    """Branch opening hours must stream unbuffered on a small model, or the TTFT claim
    and the savings claim both evaporate."""
    branch = policy.domain("branch_info").impact_inr
    assert branch < policy.thresholds.buffer_above_impact_inr
    assert branch < policy.thresholds.strong_model_above_impact_inr
