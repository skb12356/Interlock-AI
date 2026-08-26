"""The deep-judge anchor and the meta-monitor.

Invariant 8 asks for something most monitoring does not do: the system has to be able to
say **no, stop trusting me** about its own output. So these tests are mostly about the
ways a self-monitor quietly becomes agreeable — silently scoring "unclear" as a win,
dropping the cases the judge could not call before computing an agreement rate, or
retuning the thing it is supposed to be watching.
"""

from __future__ import annotations

import pytest

from interlock.lanec.drift import ECE_ALARM, MetaMonitor, TrustState
from interlock.lanec.judge import (
    MAX_SAMPLE_RATE,
    DeepJudge,
    JudgeSample,
    JudgeVerdict,
    agreement_summary,
    build_judge_messages,
)

# --------------------------------------------------------------------------- #
# The judge stays off the hot path, by construction
# --------------------------------------------------------------------------- #


def test_the_sample_rate_ceiling_is_enforced_not_suggested() -> None:
    """A judge running on 40% of traffic is not an offline anchor, it is the product's
    cost structure (CLAUDE.md s3)."""
    DeepJudge(sample_rate=0.01)
    with pytest.raises(ValueError, match="ceiling"):
        DeepJudge(sample_rate=0.40)
    assert MAX_SAMPLE_RATE <= 0.05


def test_sampling_is_roughly_the_declared_rate() -> None:
    judge = DeepJudge(sample_rate=0.01, seed=1)
    sampled = sum(1 for _ in range(20_000) if judge.should_judge())
    assert 0.005 < sampled / 20_000 < 0.02
    assert judge.requests_seen == 20_000


def test_the_judge_sees_only_the_retrieved_evidence() -> None:
    """A judge allowed to fall back on its own knowledge answers "is this true?" rather
    than "is this grounded?", and grounding is what the fast lane is checked on."""
    messages = build_judge_messages(
        question="Is there a prepayment charge?",
        answer="No charge applies.",
        evidence=["Clause 9.1: no prepayment charge applies."],
    )
    assert "ONLY evidence" in messages[1]["content"]
    assert "do not speculate" in messages[0]["content"].lower()


def test_a_missing_evidence_set_is_shown_as_none_not_omitted() -> None:
    messages = build_judge_messages(question="q", answer="a", evidence=[])
    assert "(none)" in messages[1]["content"]


# --------------------------------------------------------------------------- #
# Parsing, and the trap in it
# --------------------------------------------------------------------------- #


def test_unsupported_is_not_misread_as_supported() -> None:
    """ "unsupported" contains "supported" as a substring. Testing for the affirmative
    first would misread every negative verdict in the anchor set."""
    assert DeepJudge.parse("Verdict: UNSUPPORTED. The clause is not in evidence.").verdict == (
        "unsupported"
    )
    assert DeepJudge.parse("Verdict: SUPPORTED. Clause 9.1 says exactly this.").verdict == (
        "supported"
    )
    assert DeepJudge.parse("This claim is not supported by the passage.").verdict == "unsupported"


def test_an_unreadable_reply_becomes_unclear_not_a_default_verdict() -> None:
    """A judge whose output we could not read has told us nothing. Picking a side on its
    behalf injects our bias into the instrument meant to detect ours."""
    assert DeepJudge.parse("").verdict == "unclear"
    assert DeepJudge.parse("I'm not able to help with that.").verdict == "unclear"


def test_unclear_does_not_collapse_to_agreement() -> None:
    """Treating "I cannot tell" as "it is fine" makes the anchor systematically agree
    with a fast lane that passed -- exactly the bias an anchor exists to detect."""
    assert JudgeVerdict(verdict="unclear").says_defective is None
    sample = _sample(flagged=False, verdict="unclear")
    assert sample.agreed is None
    assert sample.disagreement_indicator is None


def test_a_stated_confidence_is_captured() -> None:
    assert DeepJudge.parse("UNSUPPORTED, confidence 0.85. The figure is invented.").confidence == (
        0.85
    )


# --------------------------------------------------------------------------- #
# Agreement
# --------------------------------------------------------------------------- #


def _sample(*, flagged: bool, verdict: str, rid: str = "r", prob: float = 0.5) -> JudgeSample:
    return JudgeSample(
        request_id=rid,
        question="q",
        answer="a",
        fast_lane_flagged=flagged,
        fast_lane_probability=prob,
        verdict=JudgeVerdict(verdict=verdict),
    )


def test_agreement_counts_both_directions() -> None:
    assert _sample(flagged=True, verdict="unsupported").agreed is True
    assert _sample(flagged=False, verdict="supported").agreed is True
    assert _sample(flagged=False, verdict="unsupported").agreed is False
    assert _sample(flagged=True, verdict="supported").agreed is False


def test_the_unjudgeable_count_is_reported_beside_the_rate() -> None:
    """An agreement rate computed after silently dropping every 'unclear' looks better
    than the evidence supports, and the cases a judge cannot call are often the hard
    ones."""
    samples = [_sample(flagged=True, verdict="unsupported", rid=f"r{i}") for i in range(6)]
    samples += [_sample(flagged=True, verdict="unclear", rid=f"u{i}") for i in range(4)]
    report = agreement_summary(samples)
    assert report["n_samples"] == 10
    assert report["n_judged"] == 6
    assert report["n_unclear"] == 4
    assert report["agreement_rate"] == 1.0
    assert any("UNCLEAR" in note for note in report["notes"])


def test_the_two_disagreement_directions_are_separated() -> None:
    """They mean opposite things: the fast lane missing what the judge caught is an
    escape; flagging what the judge thought fine is a false alarm."""
    samples = [_sample(flagged=False, verdict="unsupported", rid=f"m{i}") for i in range(5)]
    samples += [_sample(flagged=True, verdict="supported", rid="o1")]
    report = agreement_summary(samples)
    assert report["fast_lane_missed"] == 5
    assert report["fast_lane_over_flagged"] == 1
    assert any("escape pattern" in note for note in report["notes"])


def test_an_empty_anchor_reports_none_not_a_perfect_score() -> None:
    assert agreement_summary([])["agreement_rate"] is None


# --------------------------------------------------------------------------- #
# The meta-monitor
# --------------------------------------------------------------------------- #


def _calibrated() -> tuple[list[float], list[int]]:
    """An anchor set that is genuinely well calibrated, not merely confident.

    Worth building carefully: [0.05]*95 + [0.95]*5 against labels [0]*95 + [1]*5 *looks*
    calibrated and has an ECE of 0.05, because nothing in the low bin is ever defective
    and everything in the high bin always is. Here each bin's observed frequency matches
    its predicted probability, which is what calibration actually means.
    """
    probabilities: list[float] = []
    labels: list[int] = []
    for probability, count in ((0.05, 100), (0.25, 40), (0.75, 40), (0.95, 100)):
        positives = round(probability * count)
        probabilities.extend([probability] * count)
        labels.extend([1] * positives + [0] * (count - positives))
    return probabilities, labels


def test_a_well_calibrated_anchor_is_trusted() -> None:
    monitor = MetaMonitor(ece_at_fit=0.004)
    probabilities, labels = _calibrated()
    report = monitor.assess(anchor_probabilities=probabilities, anchor_labels=labels)
    assert report.trust == TrustState.TRUSTED
    assert report.safe_to_quote_probabilities
    assert "may be quoted" in report.statement()


def test_calibration_drift_makes_the_probabilities_unquotable() -> None:
    """The whole point of invariant 8: the system must be able to say NO about itself."""
    monitor = MetaMonitor(ece_at_fit=0.004)
    # The calibrator now says 0.9 on items that are almost never defective.
    report = monitor.assess(anchor_probabilities=[0.9] * 100, anchor_labels=[0] * 95 + [1] * 5)
    assert report.ece_now is not None and report.ece_now >= ECE_ALARM
    assert report.trust == TrustState.UNTRUSTED
    assert not report.safe_to_quote_probabilities
    assert "DO NOT QUOTE" in report.statement()


def test_a_small_anchor_offers_no_verdict_and_says_so() -> None:
    """Absence of an alarm is not reassurance, and a monitor that stays silent on thin
    evidence is indistinguishable from one that is working."""
    report = MetaMonitor().assess(anchor_probabilities=[0.5] * 10, anchor_labels=[0] * 10)
    assert report.ece_now is None
    assert report.trust == TrustState.WATCH
    assert any("not reassurance" in reason for reason in report.reasons)


def test_sustained_disagreement_raises_an_anytime_valid_alarm() -> None:
    monitor = MetaMonitor(ece_at_fit=0.004)
    for _ in range(300):
        monitor.observe_disagreement(1.0)
    probabilities, labels = _calibrated()
    report = monitor.assess(anchor_probabilities=probabilities, anchor_labels=labels)
    assert report.agreement_alerted
    assert report.trust == TrustState.UNTRUSTED
    assert report.n_judged == 300


def test_unjudgeable_samples_are_skipped_not_scored_as_agreement() -> None:
    """Silently scoring 'unclear' as a win is how a meta-monitor becomes the most
    agreeable component in the system."""
    monitor = MetaMonitor()
    for _ in range(50):
        monitor.observe_disagreement(None)
    assert monitor.disagreement._n == 0


def test_input_shift_is_reported_but_never_alarms_on_its_own() -> None:
    """Traffic moving is normal. Alarming on it would fire on every product launch."""
    monitor = MetaMonitor(
        ece_at_fit=0.004, baseline_domains={"branch_info": 0.6, "prepayment": 0.4}
    )
    probabilities, labels = _calibrated()
    report = monitor.assess(
        anchor_probabilities=probabilities,
        anchor_labels=labels,
        current_domains={"branch_info": 0.2, "prepayment": 0.8},
    )
    assert report.input_shift["prepayment"] == pytest.approx(0.4)
    assert report.trust == TrustState.TRUSTED, "a traffic shift alone is not a failure"


def test_input_shift_is_named_as_the_cause_when_something_else_alarms() -> None:
    monitor = MetaMonitor(
        ece_at_fit=0.004, baseline_domains={"branch_info": 0.6, "prepayment": 0.4}
    )
    report = monitor.assess(
        anchor_probabilities=[0.9] * 100,
        anchor_labels=[0] * 95 + [1] * 5,
        current_domains={"branch_info": 0.2, "prepayment": 0.8},
    )
    assert report.trust == TrustState.UNTRUSTED
    assert any("CAUSE of the above" in reason for reason in report.reasons)


def test_the_monitor_never_retunes_anything() -> None:
    """A monitor that silently re-fits the thing it is monitoring has no way left to
    tell anyone it failed. Asserted structurally: no mutating API exists."""
    monitor = MetaMonitor(ece_at_fit=0.004)
    forbidden = {"retune", "refit", "adjust_threshold", "update_policy", "recalibrate"}
    assert not (forbidden & set(dir(monitor)))
