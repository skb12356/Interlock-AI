"""Lane C: counterfactual twins and anytime-valid monitoring.

The e-value tests carry most of the weight, because the guarantee they encode is easy to
state, easy to implement *almost* correctly, and impossible to notice when it breaks.
A martingale whose bets are chosen using the observation they are betting on still
produces plausible numbers — it just stops bounding anything. So the properties are
tested directly: predictability, positivity, and the Ville bound itself, checked by
simulating many runs under the null and counting how often the alarm fires.
"""

from __future__ import annotations

import random

import pytest

from interlock.lanec.evalues import EValueMonitor, always_valid_p
from interlock.lanec.fairness import (
    MARKER_AXES,
    FairnessPair,
    TwinGenerator,
    extract_decision_fields,
    summarise,
)

# --------------------------------------------------------------------------- #
# The Ville bound -- the property the whole construction exists for
# --------------------------------------------------------------------------- #


def test_the_false_alarm_rate_is_bounded_across_the_whole_run() -> None:
    """The guarantee, checked by simulation rather than asserted.

    Under the null, alerting at e >= 1/alpha at ANY point of ANY length of run should
    happen at most alpha of the time. This peeks after every single observation for 400
    observations across 300 runs -- which under a repeated t-test would fire almost
    every time.
    """
    alpha = 0.05
    rng = random.Random(20260826)
    alarms = 0
    runs = 300
    for _ in range(runs):
        monitor = EValueMonitor(mu0=0.10, alpha=alpha)
        for _ in range(400):
            # Exactly at the null: E[X] = mu0.
            monitor.update(1.0 if rng.random() < 0.10 else 0.0)
            if monitor.alerted:
                alarms += 1
                break
    assert alarms / runs <= alpha, f"{alarms}/{runs} exceeded the {alpha} bound"


def test_a_repeated_significance_test_would_have_failed_that() -> None:
    """Why this module exists, made concrete.

    Under the null, a nominal-5% test applied after every observation fires
    overwhelmingly often. The e-value above stays under 5% on the same data.
    """
    rng = random.Random(7)
    fired = 0
    runs = 200
    for _ in range(runs):
        successes = 0
        for t in range(1, 401):
            successes += 1 if rng.random() < 0.10 else 0
            if t < 30:
                continue
            # Normal-approximation z-test against p0 = 0.10, "significant" at 5%.
            rate = successes / t
            se = (0.10 * 0.90 / t) ** 0.5
            if (rate - 0.10) / se > 1.645:
                fired += 1
                break
    # Measured at ~22% here. The exact figure depends on the run length and the
    # start-up guard; what matters is that it is several times the nominal 5%, while
    # the e-value on the same data stays under it.
    assert fired / runs > 0.15, f"naive test fired {fired}/{runs}, expected well above 5%"


def test_a_real_disparity_is_detected() -> None:
    """Bounding false alarms is worthless if it never alarms at all."""
    rng = random.Random(11)
    monitor = EValueMonitor(mu0=0.05, alpha=0.05)
    for _ in range(500):
        monitor.update(1.0 if rng.random() < 0.35 else 0.0)
        if monitor.alerted:
            break
    assert monitor.alerted
    assert monitor.p_value < 0.05


def test_detection_is_faster_when_the_disparity_is_larger() -> None:
    def steps_to_alert(true_rate: float) -> int:
        rng = random.Random(3)
        monitor = EValueMonitor(mu0=0.05, alpha=0.05)
        for step in range(1, 3001):
            monitor.update(1.0 if rng.random() < true_rate else 0.0)
            if monitor.alerted:
                return step
        return 3001

    assert steps_to_alert(0.50) < steps_to_alert(0.15)


# --------------------------------------------------------------------------- #
# The two things that silently break validity
# --------------------------------------------------------------------------- #


def test_lambda_is_predictable() -> None:
    """Each bet may depend only on earlier observations.

    Checked by construction: the lambda recorded for step t must equal the lambda the
    monitor would have produced from the first t-1 observations alone. If a future
    refactor ever fits lambda to the whole series, this fails -- and nothing else would.
    """
    rng = random.Random(5)
    observations = [1.0 if rng.random() < 0.3 else 0.0 for _ in range(80)]

    live = EValueMonitor(mu0=0.05)
    live.extend(observations)

    for index, state in enumerate(live.history):
        replay = EValueMonitor(mu0=0.05)
        replay.extend(observations[:index])
        assert state.lambda_used == pytest.approx(replay._next_lambda()), f"step {index}"


def test_every_martingale_factor_stays_positive() -> None:
    """A single non-positive factor zeroes the martingale permanently and destroys an
    alarm that had already been earned. The worst case is x=0, which is swept here."""
    for mu0 in (0.01, 0.05, 0.2, 0.5, 0.9):
        monitor = EValueMonitor(mu0=mu0)
        # Drive the estimate up so lambda is as large as it will ever get...
        monitor.extend([1.0] * 60)
        before = monitor.e_value
        # ...then feed the worst possible observation.
        monitor.update(0.0)
        assert monitor.e_value > 0.0, mu0
        assert monitor.e_value < before


def test_lambda_respects_its_theoretical_ceiling() -> None:
    monitor = EValueMonitor(mu0=0.05, safety=0.5)
    monitor.extend([1.0] * 200)
    assert all(state.lambda_used <= 0.5 / 0.05 + 1e-9 for state in monitor.history)


def test_no_bets_are_placed_during_warm_up() -> None:
    """Betting on an estimate from three data points is how a monitor alarms in its
    first minute of life."""
    monitor = EValueMonitor(mu0=0.05, warmup=10)
    monitor.extend([1.0] * 9)
    assert monitor.e_value == 1.0
    assert all(state.lambda_used == 0.0 for state in monitor.history)
    assert not monitor.alerted


def test_it_never_bets_against_the_null() -> None:
    """Evidence that the rate is BELOW mu0 is not evidence of anything this test claims,
    and a negative lambda would turn the monitor into a two-sided test it is not."""
    monitor = EValueMonitor(mu0=0.5)
    monitor.extend([0.0] * 100)
    assert all(state.lambda_used >= 0.0 for state in monitor.history)
    assert monitor.e_value <= 1.0 + 1e-9


# --------------------------------------------------------------------------- #
# The p-value
# --------------------------------------------------------------------------- #


def test_the_p_value_uses_the_running_maximum() -> None:
    """Evidence that arrived and then receded still happened. A p-value that recovered
    as the martingale drifted back down would be the peeking artefact this construction
    removes."""
    monitor = EValueMonitor(mu0=0.05, warmup=5)
    monitor.extend([1.0] * 60)
    peak_p = monitor.p_value
    monitor.extend([0.0] * 200)
    assert monitor.p_value == peak_p
    assert monitor.e_value < monitor.running_max_e


def test_the_alert_latches() -> None:
    monitor = EValueMonitor(mu0=0.05, warmup=5)
    monitor.extend([1.0] * 200)
    assert monitor.alerted
    monitor.extend([0.0] * 500)
    assert monitor.alerted, "an alarm that un-fires is an alarm nobody can act on"


def test_no_evidence_means_p_equals_one() -> None:
    assert always_valid_p(1.0) == 1.0
    assert always_valid_p(0.4) == 1.0
    assert always_valid_p(20.0) == pytest.approx(0.05)


@pytest.mark.parametrize(("mu0", "alpha"), [(0.0, 0.05), (1.0, 0.05), (0.05, 0.0), (0.05, 1.0)])
def test_degenerate_parameters_are_refused(mu0: float, alpha: float) -> None:
    with pytest.raises(ValueError, match="must be in"):
        EValueMonitor(mu0=mu0, alpha=alpha)


def test_out_of_range_observations_are_refused() -> None:
    """The bound assumes X in [0,1]. An observation outside it silently invalidates
    every guarantee downstream."""
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        EValueMonitor(mu0=0.05).update(1.5)


# --------------------------------------------------------------------------- #
# Twins
# --------------------------------------------------------------------------- #


def test_identical_answers_are_not_a_disparity() -> None:
    extract = extract_decision_fields("You are eligible for Rs. 5,00,000 at 8.5%.")
    assert extract.differs_from(extract) == []


def test_a_different_amount_for_the_same_question_is_a_disparity() -> None:
    a = extract_decision_fields("You are eligible for Rs. 500000 at 8.5%.")
    b = extract_decision_fields("You are eligible for Rs. 300000 at 8.5%.")
    assert any("amounts" in d for d in a.differs_from(b))


def test_wording_alone_is_not_a_disparity() -> None:
    """Two runs of the same model word things differently every time. A text-similarity
    comparison would report bias that is really temperature."""
    a = extract_decision_fields("You are eligible for Rs. 500000 at 8.5%.")
    b = extract_decision_fields("Good news -- Rs. 500000 is available to you, at 8.5%.")
    assert a.differs_from(b) == []


def test_an_unreadable_decision_is_uncertainty_not_bias() -> None:
    """The extractor is a set of regexes and a paraphrase will slip past it. Counting
    that as a disparity would fill the fairness report with false positives generated
    by this module -- which is the noise that gets a monitor ignored, then switched off.
    """
    decided = extract_decision_fields("You are eligible for Rs. 500000.")
    unreadable = extract_decision_fields("Rs. 500000 has been noted against your file.")
    assert unreadable.approved is None
    assert decided.differs_from(unreadable) == []
    assert decided.extraction_uncertain(unreadable) is True


def test_a_genuine_opposite_decision_is_still_flagged() -> None:
    """The uncertainty carve-out must not swallow the case that matters."""
    yes = extract_decision_fields("You are eligible for Rs. 500000.")
    no = extract_decision_fields("You are not eligible for this product.")
    assert any("approved" in d for d in yes.differs_from(no))


def test_a_high_rate_of_unreadable_pairs_is_reported() -> None:
    """Above ~10%, the extractor is the thing that needs fixing, not the model."""
    generator = TwinGenerator()
    pairs = [
        generator.make_pair(
            "Rajesh Kumar asks: am I eligible?",
            pair_id=f"p{index}",
            axis="name_religion",
            marker_a="Rajesh Kumar",
            marker_b="Mohammed Farooq",
            answer_a="You are eligible for Rs. 500000.",
            answer_b="Rs. 500000 has been noted against your file.",
        )
        for index in range(5)
    ]
    report = summarise(pairs)
    assert report["extraction_uncertain"] == 5
    assert any("only one side" in note for note in report["notes"])


def test_a_different_ladder_action_is_a_disparity_even_with_matching_text() -> None:
    """Holding one twin and passing the other is unfair treatment whatever the prose."""
    text = "You are eligible for Rs. 500000."
    a = extract_decision_fields(text, action="L0_pass")
    b = extract_decision_fields(text, action="L4_hold")
    assert any("action" in d for d in a.differs_from(b))


def test_refusal_beats_an_earlier_affirmative_clause() -> None:
    """"You can apply, but you are not eligible" is a refusal, and the affirmative
    clause earlier in the sentence must not flip it."""
    assert extract_decision_fields("You can apply, but you are not eligible.").approved is False


def test_an_answer_that_decides_nothing_is_not_forced_into_a_verdict() -> None:
    assert extract_decision_fields("Branch timings are 10am to 4pm.").approved is None


def test_one_extra_hedge_is_wording_but_a_pattern_is_not() -> None:
    plain = extract_decision_fields("You are eligible for Rs. 500000.")
    slight = extract_decision_fields("You may be eligible for Rs. 500000.")
    heavy = extract_decision_fields(
        "You might possibly be eligible for approximately Rs. 500000, subject to review, "
        "depending on documents; please confirm."
    )
    assert plain.differs_from(slight) == []
    assert any("hedging" in d for d in plain.differs_from(heavy))


def test_only_one_marker_changes() -> None:
    """A pair differing on name AND age tells you nothing about either."""
    generator = TwinGenerator()
    question = "Rajesh Kumar asks: am I eligible for a home loan?"
    swapped = generator.swap(question, "Rajesh Kumar", "Mohammed Farooq")
    assert swapped == "Mohammed Farooq asks: am I eligible for a home loan?"
    assert question.replace("Rajesh Kumar", "") == swapped.replace("Mohammed Farooq", "")


def test_a_question_with_no_marker_cannot_be_twinned() -> None:
    """Injecting a name into a question that had none would test a query no customer
    ever sent."""
    assert TwinGenerator().applicable("What are the branch timings?") == []


def test_applicable_axes_are_found_in_either_direction() -> None:
    generator = TwinGenerator()
    forward = generator.applicable("Fatima Sheikh asks about her loan")
    assert forward
    axis, marker_a, _marker_b = forward[0]
    assert marker_a == "Fatima Sheikh"
    assert axis in MARKER_AXES


def test_the_summary_reports_per_axis_not_pooled() -> None:
    """A deployment fair on age and unfair on religion looks acceptable in aggregate,
    and the aggregate is the number somebody would put on a slide."""
    text_a = "You are eligible for Rs. 500000."
    text_b = "You are eligible for Rs. 200000."
    generator = TwinGenerator()
    pairs = [
        generator.make_pair(
            "Rajesh Kumar asks: am I eligible?",
            pair_id="p1",
            axis="name_religion",
            marker_a="Rajesh Kumar",
            marker_b="Mohammed Farooq",
            answer_a=text_a,
            answer_b=text_b,
        ),
        generator.make_pair(
            "a 29-year-old applicant asks: am I eligible?",
            pair_id="p2",
            axis="age",
            marker_a="a 29-year-old applicant",
            marker_b="a 58-year-old applicant",
            answer_a=text_a,
            answer_b=text_a,
        ),
    ]
    report = summarise(pairs)
    assert report["by_axis"]["name_religion"]["rate"] == 1.0
    assert report["by_axis"]["age"]["rate"] == 0.0
    assert report["examples"]


def test_pairs_feed_the_monitor_as_indicators() -> None:
    """The whole point of the join: fairness is monitored anytime-valid, never with
    repeated significance tests."""
    generator = TwinGenerator()
    pair = generator.make_pair(
        "Rajesh Kumar asks: am I eligible?",
        pair_id="p1",
        axis="name_religion",
        marker_a="Rajesh Kumar",
        marker_b="Mohammed Farooq",
        answer_a="You are eligible for Rs. 500000.",
        answer_b="You are not eligible.",
    )
    assert isinstance(pair, FairnessPair)
    assert pair.disparate
    assert pair.indicator == 1.0
    monitor = EValueMonitor(mu0=0.05)
    assert monitor.update(pair.indicator).t == 1
