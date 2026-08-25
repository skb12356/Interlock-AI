"""Calibration and conformal tests.

Calibration is on the never-cut list because everything downstream does arithmetic in
rupees, and that arithmetic is only meaningful if P means what it says. The failure this
file is mostly guarding against is not "the numbers are bad" -- it is "the numbers look
good because they were measured wrong". An in-sample ECE, a threshold selected on the
data it is then certified against, a p-value that is really a normal approximation: each
of those produces a confident, precise, indefensible number.
"""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from interlock.core.types import Fragment
from interlock.eval.induce import FAILURE_MODES, TripleGenerator
from interlock.retrieval import corpus_chunks, load_corpus
from interlock.risk.calibration import (
    ECE_TARGET,
    SignalCalibrator,
    build_report,
    expected_calibration_error,
    reliability_curve,
)
from interlock.risk.conformal import (
    bentkus_p_value,
    binom_cdf,
    hoeffding_bentkus_p_value,
    hoeffding_p_value,
    select_threshold,
)
from interlock.signals.grounding import GROUNDING_SIGNALS, grounding_signals

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dataset() -> tuple[np.ndarray, np.ndarray, list[str]]:
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    # 2000, matching what scripts/calibrate.py actually ships. Deliberately not a
    # smaller set for test speed: ECE improves with data (each isotonic fit sees
    # more points), and at 600 items it comes out at 0.059 -- above the target. A
    # test that asserted the target on 600 items would either fail or, if the
    # target were relaxed to suit it, would stop testing the published claim.
    triples = TripleGenerator(chunks=corpus_chunks(documents)).generate(2000)
    features = np.array(
        [
            list(
                grounding_signals(t.answer, t.context, question=t.question).as_features().values()
            )
            for t in triples
        ]
    )
    labels = np.array([int(t.is_defective) for t in triples])
    return features, labels, [t.failure_mode for t in triples]


# --------------------------------------------------------------------------- #
# ECE
# --------------------------------------------------------------------------- #


def test_a_perfectly_calibrated_predictor_scores_zero() -> None:
    rng = np.random.default_rng(0)
    probabilities = rng.uniform(0, 1, 20000)
    labels = (rng.uniform(0, 1, 20000) < probabilities).astype(int)
    assert expected_calibration_error(probabilities, labels) < 0.02


def test_a_confidently_wrong_predictor_scores_badly() -> None:
    probabilities = np.full(1000, 0.9)
    labels = np.zeros(1000, dtype=int)
    assert expected_calibration_error(probabilities, labels) == pytest.approx(0.9, abs=0.01)


def test_empty_bins_do_not_flatter_the_score() -> None:
    """A predictor that only ever says 0.2 must not look calibrated *everywhere*.

    Counting an empty bin as a zero gap averages fiction into the result.
    """
    probabilities = np.full(100, 0.2)
    labels = np.ones(100, dtype=int)
    assert expected_calibration_error(probabilities, labels) == pytest.approx(0.8, abs=0.01)


def test_ece_of_nothing_is_zero_not_an_error() -> None:
    assert expected_calibration_error(np.array([]), np.array([])) == 0.0


def test_the_reliability_curve_reports_bin_counts() -> None:
    """A curve drawn through bins of three items is noise; the counts are how a reader
    tells the difference, so they must be in the data, not just the picture."""
    rng = np.random.default_rng(1)
    probabilities = rng.uniform(0, 1, 500)
    labels = (rng.uniform(0, 1, 500) < probabilities).astype(int)
    curve = reliability_curve(probabilities, labels)
    assert len(curve) == 10
    assert sum(row["count"] for row in curve) == 500


# --------------------------------------------------------------------------- #
# Cross-fitting: the thing that makes the reported number honest
# --------------------------------------------------------------------------- #


def test_out_of_fold_scores_are_worse_than_in_sample(
    dataset: tuple[np.ndarray, np.ndarray, list[str]],
) -> None:
    """The whole reason ``evaluate`` exists separately from ``fit``.

    Isotonic regression can drive in-sample error to nothing by memorising. If these
    two ever came out equal, cross-fitting would have quietly stopped happening.
    """
    features, labels, _ = dataset
    calibrator = SignalCalibrator(signals=list(GROUNDING_SIGNALS))

    out_of_fold, _ = calibrator.evaluate(features, labels)
    calibrator.fit(features, labels)
    in_sample = np.array([calibrator.predict(_row(features, i)) for i in range(len(labels))])

    from sklearn.metrics import brier_score_loss

    assert brier_score_loss(labels, in_sample) <= brier_score_loss(labels, out_of_fold)


def _row(features: np.ndarray, index: int) -> dict[str, float]:
    return dict(zip(GROUNDING_SIGNALS, features[index], strict=True))


def test_the_calibrator_meets_the_ece_target_out_of_fold(
    dataset: tuple[np.ndarray, np.ndarray, list[str]],
) -> None:
    features, labels, modes = dataset
    calibrator = SignalCalibrator(signals=list(GROUNDING_SIGNALS))
    probabilities, _ = calibrator.evaluate(features, labels)
    report = build_report(
        probabilities=probabilities,
        labels=labels,
        features=features,
        signals=list(GROUNDING_SIGNALS),
        modes=modes,
        folds=5,
    )
    assert report.ece < ECE_TARGET, report.ece
    assert report.auroc > 0.8


def test_the_report_names_the_failure_modes_the_signals_cannot_see(
    dataset: tuple[np.ndarray, np.ndarray, list[str]],
) -> None:
    """'unanswerable' scores at the clean baseline. That must appear in the report as a
    note, not be left for someone to notice in a table."""
    features, labels, modes = dataset
    calibrator = SignalCalibrator(signals=list(GROUNDING_SIGNALS))
    probabilities, _ = calibrator.evaluate(features, labels)
    report = build_report(
        probabilities=probabilities,
        labels=labels,
        features=features,
        signals=list(GROUNDING_SIGNALS),
        modes=modes,
        folds=5,
    )
    assert any("unanswerable" in note for note in report.notes)
    assert any("observer probe" in note for note in report.notes)


def test_predicting_before_fitting_raises_rather_than_guessing() -> None:
    calibrator = SignalCalibrator(signals=list(GROUNDING_SIGNALS))
    with pytest.raises(RuntimeError, match="not fitted"):
        calibrator.predict({name: 0.5 for name in GROUNDING_SIGNALS})


def test_the_calibrator_serialises_as_plain_data(
    dataset: tuple[np.ndarray, np.ndarray, list[str]], tmp_path: Path
) -> None:
    """Not pickle: these artefacts ship in the evidence pack and a reviewer must be
    able to read them, without executing whatever is inside."""
    features, labels, _ = dataset
    calibrator = SignalCalibrator(signals=list(GROUNDING_SIGNALS))
    calibrator.fit(features, labels)

    path = tmp_path / "calibrator.json"
    calibrator.save(path)
    text = path.read_text(encoding="utf-8")
    assert "isotonic" in text and "fusion" in text
    import json

    payload = json.loads(text)
    assert set(payload["isotonic"]) == set(GROUNDING_SIGNALS)
    assert len(payload["fusion"]["coefficients"]) == len(GROUNDING_SIGNALS)


def test_isotonic_output_is_monotone(
    dataset: tuple[np.ndarray, np.ndarray, list[str]],
) -> None:
    """A higher raw score must never map to a lower probability -- that is the one
    property isotonic regression is chosen for."""
    features, labels, _ = dataset
    calibrator = SignalCalibrator(signals=list(GROUNDING_SIGNALS))
    calibrator.fit(features, labels)
    for name in GROUNDING_SIGNALS:
        values = [calibrator.calibrate_one(name, x) for x in np.linspace(0, 1, 50)]
        assert all(b >= a - 1e-9 for a, b in pairwise(values)), name


# --------------------------------------------------------------------------- #
# The bounds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("successes", "trials", "probability", "expected"),
    [(5, 10, 0.5, 0.623046875), (0, 10, 0.1, 0.3486784401), (10, 10, 0.5, 1.0)],
)
def test_the_binomial_cdf_is_exact(
    successes: int, trials: int, probability: float, expected: float
) -> None:
    """Checked against closed-form values, because a certified bound built on an
    approximate tail is not certified."""
    assert binom_cdf(successes, trials, probability) == pytest.approx(expected, abs=1e-9)


def test_bentkus_beats_hoeffding_in_the_small_rate_regime() -> None:
    """The entire reason both are computed. alpha=0.01 is the operating point."""
    assert bentkus_p_value(0.0, 0.01, 500) < hoeffding_p_value(0.0, 0.01, 500)


def test_an_observed_rate_at_or_above_alpha_cannot_be_rejected() -> None:
    """You cannot certify a bound the data already violates."""
    assert hoeffding_bentkus_p_value(0.02, 0.01, 1000) == 1.0
    assert hoeffding_bentkus_p_value(0.01, 0.01, 1000) == 1.0


def test_more_data_makes_the_same_rate_more_certifiable() -> None:
    p_values = [hoeffding_bentkus_p_value(0.0, 0.01, n) for n in (100, 200, 500, 1000)]
    assert all(b < a for a, b in pairwise(p_values))


def test_a_bound_is_unreachable_below_a_sample_size_whatever_the_detector() -> None:
    """A 1% bound at 90% confidence simply cannot be made on 100 items. Worth an
    assertion because the temptation, on seeing 'not certified', is to blame the
    detector and start tuning."""
    assert hoeffding_bentkus_p_value(0.0, 0.01, 100) > 0.10
    assert hoeffding_bentkus_p_value(0.0, 0.01, 500) < 0.10


# --------------------------------------------------------------------------- #
# Threshold selection
# --------------------------------------------------------------------------- #


def test_a_perfect_detector_certifies_a_useful_threshold() -> None:
    rng = np.random.default_rng(3)
    labels = np.array([0] * 700 + [1] * 700)
    probabilities = np.concatenate(
        [rng.uniform(0.0, 0.2, 700), rng.uniform(0.8, 1.0, 700)]
    )
    result = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    assert result.certified
    assert 0.2 <= (result.threshold or 0) <= 0.8, result.threshold
    assert result.escape_rate == 0.0
    assert result.intervention_rate == pytest.approx(0.5, abs=0.02)


def test_the_search_walks_from_the_easiest_hypothesis_not_the_hardest() -> None:
    """The direction bug this module was written with, and the reason it is called out
    in the source: reversed, EVERY input reports 'no threshold could be certified',
    because the first candidate fails and fixed-sequence testing stops there."""
    labels = np.array([0] * 700 + [1] * 700)
    probabilities = np.concatenate([np.full(700, 0.05), np.full(700, 0.95)])
    result = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    assert result.certified, "the sequence stopped before it started"
    assert result.candidates[0]["threshold"] < result.candidates[-1]["threshold"]


def test_a_useless_detector_certifies_nothing_rather_than_pretending() -> None:
    """Refusing to certify is information. It must never be papered over."""
    rng = np.random.default_rng(4)
    labels = rng.integers(0, 2, 800)
    probabilities = rng.uniform(0, 1, 800)
    result = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    if result.certified:
        # The only certifiable threshold for a coin-flip detector intervenes on
        # everything, and the result must say so.
        assert result.intervention_rate == pytest.approx(1.0, abs=0.01)
    assert result.notes


def test_a_certified_but_total_intervention_rate_is_flagged() -> None:
    """The state the current detector is actually in, so it must be reported.

    Clean items score ABOVE defective ones here, so no threshold separates them: the
    only certifiable one sits below everything and intervenes on all traffic. The bound
    genuinely holds. It is also useless, and a result that reported only the bound
    would be technically true and thoroughly misleading.
    """
    labels = np.array([0] * 500 + [1] * 500)
    probabilities = np.concatenate([np.full(500, 0.40), np.full(500, 0.35)])
    result = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    assert result.certified
    assert result.intervention_rate == pytest.approx(1.0)
    assert any("intervenes on" in note for note in result.notes)


def test_no_defective_items_is_reported_not_certified() -> None:
    result = select_threshold(np.full(100, 0.5), np.zeros(100, dtype=int), alpha=0.01, delta=0.10)
    assert not result.certified
    assert "nothing to bound" in result.notes[0]


def test_a_small_sample_says_so_in_its_notes() -> None:
    labels = np.array([0] * 40 + [1] * 40)
    probabilities = np.concatenate([np.full(40, 0.1), np.full(40, 0.9)])
    result = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    assert not result.certified
    assert any("defective items" in note for note in result.notes)


def test_the_statement_never_overclaims() -> None:
    labels = np.array([0] * 40 + [1] * 40)
    probabilities = np.concatenate([np.full(40, 0.1), np.full(40, 0.9)])
    uncertified = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    assert "cannot be made" in uncertified.statement()

    labels = np.array([0] * 700 + [1] * 700)
    probabilities = np.concatenate([np.full(700, 0.05), np.full(700, 0.95)])
    certified = select_threshold(probabilities, labels, alpha=0.01, delta=0.10)
    statement = certified.statement()
    assert "1%" in statement and "90%" in statement and "1400" not in statement
    assert "700 held-out items" in statement, "n must be the DEFECTIVE count, not the total"


# --------------------------------------------------------------------------- #
# The induced dataset
# --------------------------------------------------------------------------- #


def test_the_taxonomy_is_balanced_as_declared() -> None:
    """D1-B5's stated test. A mode that silently fell back to clean changes every
    number measured against the set."""
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    generator = TripleGenerator(chunks=corpus_chunks(documents))
    triples = generator.generate(1000)
    assert generator.fallbacks == {}, generator.fallbacks
    for mode, share in FAILURE_MODES.items():
        actual = sum(1 for t in triples if t.failure_mode == mode) / len(triples)
        assert abs(actual - share) < 0.02, f"{mode}: {actual:.3f} vs {share}"


def test_generation_is_deterministic_given_a_seed() -> None:
    """A calibration map fitted on data nobody can regenerate is one nobody can audit."""
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    chunks = corpus_chunks(documents)
    first = TripleGenerator(chunks=chunks, seed=7).generate(120)
    second = TripleGenerator(chunks=chunks, seed=7).generate(120)
    assert [t.answer for t in first] == [t.answer for t in second]
    assert [t.failure_mode for t in first] != [
        t.failure_mode for t in TripleGenerator(chunks=chunks, seed=8).generate(120)
    ]


def test_every_triple_carries_machine_checkable_ground_truth() -> None:
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    for triple in TripleGenerator(chunks=corpus_chunks(documents)).generate(200):
        assert triple.provenance_note
        assert (triple.defect is None) == (triple.failure_mode == "clean")
        assert triple.to_row()["label"] == int(triple.is_defective)
        if triple.is_defective:
            assert triple.offending_span, triple.failure_mode


def test_corrupted_numbers_stay_plausible() -> None:
    """An absurd corruption (2% becoming 900%) is caught by a plausibility check rather
    than a grounding check, and would flatter every detector measured against it."""
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    generator = TripleGenerator(chunks=corpus_chunks(documents))
    for triple in generator.generate(600):
        if triple.failure_mode != "number_corrupted":
            continue
        original = float(triple.provenance_note.split("'")[1].replace(",", ""))
        corrupted = float(triple.provenance_note.split("'")[3].replace(",", ""))
        if original:
            assert 0.4 <= corrupted / original <= 2.5


# --------------------------------------------------------------------------- #
# The grounding signals themselves
# --------------------------------------------------------------------------- #


def test_no_context_means_maximally_unsupported() -> None:
    from interlock.signals.grounding import unsupported_content

    assert unsupported_content("Anything at all here.", []) == 1.0


def test_an_invented_figure_is_caught_exactly() -> None:
    from interlock.signals.grounding import numeric_unsupported

    context = [Fragment(text="A charge of 2% applies.", provenance="retrieved_verified")]
    score, missing = numeric_unsupported("A charge of 5% applies.", context)
    assert score == 1.0
    assert missing == ("5",)


def test_equivalent_number_formats_are_not_false_positives() -> None:
    """25,000 and 25000 are the same figure. Flagging that as invented would fire on
    almost every correctly-grounded answer in the corpus."""
    from interlock.signals.grounding import numeric_unsupported

    context = [Fragment(text="The balance is Rs. 25000.", provenance="retrieved_verified")]
    score, _ = numeric_unsupported("The balance is Rs. 25,000.", context)
    assert score == 0.0


def test_a_citation_to_an_unretrieved_clause_is_caught() -> None:
    """Scene 1 in one number."""
    from interlock.signals.grounding import citation_unsupported

    context = [Fragment(text="Clause 9.1 applies.", provenance="retrieved_verified")]
    score, missing = citation_unsupported("This falls under Clause 7.4.", context)
    assert score == 1.0
    assert missing == ("7.4",)


def test_context_conflict_sees_what_the_answer_side_checks_cannot() -> None:
    """When a superseded clause sits beside the current one, every answer-side check
    reports 'supported' -- correctly. The disagreement is between two fragments."""
    from interlock.signals.grounding import context_conflict

    agreeing = [
        Fragment(text="A charge of 2% applies.", provenance="retrieved_verified", domain="fees"),
        Fragment(text="A charge of 2% applies here too.", provenance="retrieved_verified",
                 domain="fees"),
    ]
    conflicting = [
        Fragment(text="A charge of 2% applies.", provenance="retrieved_verified", domain="fees"),
        Fragment(text="No charge applies; the rate is 0%.", provenance="retrieved_verified",
                 domain="fees"),
    ]
    assert context_conflict(agreeing) == 0.0
    assert context_conflict(conflicting) > 0.0


def test_hedging_lowers_the_overconfidence_signal() -> None:
    blunt = grounding_signals("The charge is 2%.", [])
    hedged = grounding_signals(
        "The charge may typically be around 2%, subject to confirmation.", []
    )
    assert hedged.overconfidence < blunt.overconfidence


def test_signal_readings_match_the_declared_names() -> None:
    scores = grounding_signals("Some answer.", [], question="Some question?")
    readings = scores.as_readings()
    assert [r.name for r in readings] == list(GROUNDING_SIGNALS)
    assert set(scores.as_features()) == set(GROUNDING_SIGNALS)
    assert all(0.0 <= r.raw <= 1.0 for r in readings)


def test_every_signal_is_bounded_on_real_data() -> None:
    """The calibrator clips, but a signal outside [0,1] means a bug upstream of it."""
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    for triple in TripleGenerator(chunks=corpus_chunks(documents)).generate(300):
        for name, value in grounding_signals(
            triple.answer, triple.context, question=triple.question
        ).as_features().items():
            assert 0.0 <= value <= 1.0, f"{name}={value}"
            assert not math.isnan(value), name
