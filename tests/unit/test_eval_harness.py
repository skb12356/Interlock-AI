"""Seeded-set and harness tests.

The eval set is the proof of the product, which makes it the thing most worth breaking
deliberately. Two failure modes matter more than the rest:

* **A set that quietly drifted.** If poisoned_document silently becomes 3 cases instead
  of 10, every number measured against it moves and nothing complains. The counts are
  asserted exactly.
* **A metric that flatters.** Counting L1_annotate as a catch, or measuring false
  interventions over the whole set instead of over the clean half, would both make the
  headline numbers look better while measuring something else entirely.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from interlock.core.policy import load_policy
from interlock.eval.harness import LOOP_STRIKES, _loop_breaker, compute_metrics, run_eval
from interlock.eval.metrics import PRE_ACTION_ACTIONS, wilson_interval
from interlock.eval.seeded import CASE_COUNTS, build_seeded_set
from interlock.interlock_tools.holds import ToolInterlock
from interlock.ledger.writer import Ledger
from interlock.retrieval import corpus_chunks, load_corpus
from interlock.risk.calibration import MultiDefectCalibrator
from interlock.risk.engine import RealRiskEngine
from interlock.signals.canary import CanaryDetector, CanaryRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(REPO_ROOT / "policies" / "banking.yaml")
CALIB = REPO_ROOT / "artifacts" / "calibration" / "calibrator_per_defect.json"


@pytest.fixture(scope="module")
def chunks() -> list:
    return corpus_chunks(load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT))


@pytest.fixture(scope="module")
def cases(chunks: list) -> list:
    return build_seeded_set(chunks, canary="INTERLOCK-CANARY-TESTONLY-0001")


# --------------------------------------------------------------------------- #
# The set itself
# --------------------------------------------------------------------------- #


def test_the_set_is_exactly_two_hundred_conversations(cases: list) -> None:
    assert len(cases) == 200
    assert sum(CASE_COUNTS.values()) == 200


def test_every_category_has_exactly_its_declared_count(cases: list) -> None:
    """A set that drifted to 3 poisoned documents moves every number measured on it."""
    counts = Counter(case.category for case in cases)
    for category, expected in CASE_COUNTS.items():
        assert counts[category] == expected, f"{category}: {counts[category]} != {expected}"


def test_most_of_the_set_is_clean(cases: list) -> None:
    """An eval set made mostly of attacks measures eagerness and calls it a catch rate.

    The false-intervention target can only be measured against traffic that did not
    deserve intervening on, so most of the set has to be traffic like that.
    """
    clean = sum(1 for case in cases if not case.is_defective)
    assert clean / len(cases) > 0.7


def test_the_plan_s_sixty_induced_failures_are_present(cases: list) -> None:
    """The plan specifies 60. Twin pairs are not failures -- they are a fairness probe
    where BOTH members are legitimate -- so they are counted separately."""
    induced = sum(
        1 for case in cases if case.category not in {"clean", "demographic_twin"}
    )
    assert induced == 50
    twins = sum(1 for case in cases if case.category == "demographic_twin")
    assert induced + twins == 60


def test_every_case_carries_machine_checkable_ground_truth(cases: list) -> None:
    for case in cases:
        assert case.question and case.answer
        assert case.note, case.case_id
        if case.category == "clean":
            assert not case.is_defective
        if case.category == "poisoned_document":
            assert case.should_freeze_tool and case.tool_call
        if case.category == "agent_loop":
            assert len(case.loop_calls) >= LOOP_STRIKES
        if case.category == "demographic_twin":
            assert case.twin_id


def test_the_set_is_deterministic(chunks: list) -> None:
    """A measurement nobody can reproduce is an anecdote."""
    first = build_seeded_set(chunks, canary="c", seed=7)
    second = build_seeded_set(chunks, canary="c", seed=7)
    assert [c.case_id for c in first] == [c.case_id for c in second]
    assert [c.answer for c in first] == [c.answer for c in second]


def test_twins_differ_in_exactly_one_marker(cases: list) -> None:
    """Any difference in outcome must be attributable to the marker and nothing else."""
    pairs: dict[str, list] = {}
    for case in cases:
        if case.twin_id:
            pairs.setdefault(case.twin_id, []).append(case)
    assert pairs
    for members in pairs.values():
        assert len(members) == 2
        left, right = members
        assert left.answer == right.answer, "the twins must share their generation"
        assert left.question != right.question
        assert left.context == right.context


def test_the_poisoned_cases_carry_a_benign_looking_answer(cases: list) -> None:
    """The incident is the TOOL CALL. An assistant that summarises a claim correctly
    and then emails it to an attacker has not hallucinated anything, and a set that
    scored these on the prose would be measuring the wrong thing."""
    poisoned = [c for c in cases if c.category == "poisoned_document"]
    assert poisoned
    for case in poisoned:
        assert case.expected_defect is None
        assert case.should_freeze_tool
        assert any(f.provenance == "retrieved_untrusted" for f in case.context)


# --------------------------------------------------------------------------- #
# The loop breaker
# --------------------------------------------------------------------------- #


def test_a_repeated_call_is_cut_after_three_strikes(cases: list) -> None:
    case = next(c for c in cases if c.category == "agent_loop")
    cut, saved = _loop_breaker(case)
    assert cut
    assert saved >= 0


def test_a_case_with_no_loop_is_not_cut(cases: list) -> None:
    case = next(c for c in cases if c.category == "clean")
    assert _loop_breaker(case) == (False, 0)


# --------------------------------------------------------------------------- #
# The metrics
# --------------------------------------------------------------------------- #


def test_annotate_does_not_count_as_a_catch() -> None:
    """L1 ships the answer with a note beside it. The reader still receives the claim.

    Counting it as a pre-action catch would let the system take credit for delivering
    the defect politely.
    """
    assert "L1_annotate" not in PRE_ACTION_ACTIONS
    assert "L0_pass" not in PRE_ACTION_ACTIONS
    assert {"L2_repair", "L3_reroute", "L4_hold", "L5_block"} == PRE_ACTION_ACTIONS


def test_wilson_does_not_claim_certainty_from_a_perfect_score() -> None:
    """60/60 has a normal-approximation interval of exactly zero width."""
    low, high = wilson_interval(60, 60)
    # approx, not ==: the closed form lands a float-epsilon below 1.0 at p=1.
    assert high == pytest.approx(1.0)
    assert low < 1.0, "a perfect score must not report a zero-width interval"
    assert low > 0.9


def test_wilson_handles_the_empty_case() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


@pytest.fixture
async def interlock(tmp_path: Path):  # type: ignore[no-untyped-def]
    ledger = Ledger(db_path=tmp_path / "eval.db")
    await ledger.start()
    yield ToolInterlock(policy=POLICY, ledger=ledger)
    await ledger.stop()


async def test_the_harness_produces_all_six_metrics(cases: list, interlock: ToolInterlock) -> None:
    """Day-3 exit criterion: `make eval` prints six numbers, even if they are bad."""
    if not CALIB.exists():
        pytest.skip("run scripts/calibrate.py first")
    registry = CanaryRegistry()
    registry.mint("eval")
    engine = RealRiskEngine(
        policy=POLICY,
        calibrator=MultiDefectCalibrator.load(CALIB),
        canary_detector=CanaryDetector(registry=registry),
    )
    _off, on, metrics = await run_eval(
        cases=cases, engine=engine, policy=POLICY, tool_interlock=interlock
    )
    names = {m.name for m in metrics.metrics}
    assert {
        "Pre-Action Catch Rate",
        "Added p95 latency",
        "Verification cost",
        "Net spend change",
        "Ungrounded escapes",
        "False interventions",
    } <= names
    assert len(on.outcomes) == len(cases)


async def test_the_deterministic_defects_are_caught_at_one_hundred_percent(
    cases: list, interlock: ToolInterlock
) -> None:
    """Canary and PII are rule-based with no model in the loop. Anything below 100%
    here is a broken rule, not a weak detector."""
    if not CALIB.exists():
        pytest.skip("run scripts/calibrate.py first")
    registry = CanaryRegistry()
    canary = registry.mint("eval")
    rebuilt = build_seeded_set(
        corpus_chunks(load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)),
        canary=canary,
    )
    engine = RealRiskEngine(
        policy=POLICY,
        calibrator=MultiDefectCalibrator.load(CALIB),
        canary_detector=CanaryDetector(registry=registry),
    )
    _off, on, _metrics = await run_eval(
        cases=rebuilt, engine=engine, policy=POLICY, tool_interlock=interlock
    )
    canary_cases = [o for o in on.outcomes if o.category == "canary_pii"]
    assert canary_cases
    assert all(o.caught_pre_action for o in canary_cases)


async def test_every_poisoned_tool_call_is_frozen(
    cases: list, interlock: ToolInterlock
) -> None:
    if not CALIB.exists():
        pytest.skip("run scripts/calibrate.py first")
    registry = CanaryRegistry()
    registry.mint("eval")
    engine = RealRiskEngine(
        policy=POLICY,
        calibrator=MultiDefectCalibrator.load(CALIB),
        canary_detector=CanaryDetector(registry=registry),
    )
    _off, on, _metrics = await run_eval(
        cases=cases, engine=engine, policy=POLICY, tool_interlock=interlock
    )
    poisoned = [o for o in on.outcomes if o.category == "poisoned_document"]
    assert poisoned
    assert all(o.tool_frozen for o in poisoned)


async def test_the_off_arm_intervenes_on_nothing(
    cases: list, interlock: ToolInterlock
) -> None:
    """The control arm has to be a real control: nothing checked, everything shipped."""
    if not CALIB.exists():
        pytest.skip("run scripts/calibrate.py first")
    registry = CanaryRegistry()
    registry.mint("eval")
    engine = RealRiskEngine(
        policy=POLICY,
        calibrator=MultiDefectCalibrator.load(CALIB),
        canary_detector=CanaryDetector(registry=registry),
    )
    off, _on, _metrics = await run_eval(
        cases=cases, engine=engine, policy=POLICY, tool_interlock=interlock
    )
    assert all(o.action == "L0_pass" for o in off.outcomes)
    assert not any(o.tool_frozen for o in off.outcomes)
    assert not any(o.caught_pre_action for o in off.outcomes)


async def test_the_false_intervention_rate_is_measured_over_clean_cases_only(
    cases: list, interlock: ToolInterlock
) -> None:
    """Measuring it over the whole set would let a defective case we correctly
    intervened on count against us."""
    if not CALIB.exists():
        pytest.skip("run scripts/calibrate.py first")
    registry = CanaryRegistry()
    registry.mint("eval")
    engine = RealRiskEngine(
        policy=POLICY,
        calibrator=MultiDefectCalibrator.load(CALIB),
        canary_detector=CanaryDetector(registry=registry),
    )
    _off, _on, metrics = await run_eval(
        cases=cases, engine=engine, policy=POLICY, tool_interlock=interlock
    )
    metric = metrics.by_name("False interventions")
    assert metric is not None
    clean = sum(1 for case in cases if not case.is_defective)
    assert metric.denominator == clean


def test_the_harness_reports_what_it_does_not_measure(cases: list) -> None:
    """Generation latency and billing are modelled, not observed. A report that did not
    say so would be claiming a live measurement it never took."""
    from interlock.eval.harness import EvalRun

    metrics = compute_metrics(
        off=EvalRun(label="off"), on=EvalRun(label="on"), cases=[], policy=POLICY
    )
    assert any("NOT observed" in note for note in metrics.notes)
