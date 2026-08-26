"""Ledger economics: pricing, cost regret, rework attribution.

These three produce the numbers that make Interlock's commercial case, which makes them
the numbers most worth being suspicious of. CLAUDE.md §9 calls for honest accounting —
waste and rework reported *with confidence intervals*, and the conservative end of any
published range. So most of what is asserted here is about the ways a spend report can
be precise and wrong:

* a blended token price that hides the saving routing produces;
* a bare point estimate from a handful of shadow runs;
* an inferred retry charged at full confidence, inflating the one number that argues
  hardest for the product.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from interlock.ledger.pricing import DEFAULT_PRICES, PriceBook, load_price_book
from interlock.ledger.regret import (
    MIN_SAMPLE_FOR_CONFIDENCE,
    RegretLedger,
    ShadowResult,
    bootstrap_ci,
)
from interlock.ledger.rework import (
    RETRY_CONFIDENCE_FLOOR,
    ReworkLedger,
    SessionTurn,
    similarity,
)

#: 18 shared content tokens out of 19 -> cosine 0.9474, just past the 0.90 bar.
#: Built deliberately: shorter phrases cannot land between 0.90 and 1.0 at all,
#: because the cosine of small token sets is coarse (8/9 = 0.889, 9/9 = 1.0).
_LONG = (
    "prepayment charge home loan floating rate agreement clause borrower principal "
    "outstanding tenure interest individual regulator directive retail sanctioned"
)

# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


def test_prompt_and_completion_are_priced_separately() -> None:
    """Every hosted provider charges 3-5x more for completion, and RAG requests are
    prompt-heavy. A blended rate over-states the cost of exactly the traffic Interlock
    is trying to make cheap."""
    book = PriceBook.default()
    price = book.price_for("gpt-4o")
    assert price.completion_inr_per_1k > price.prompt_inr_per_1k * 2

    prompt_heavy = book.cost_inr("gpt-4o", prompt_tokens=800, completion_tokens=100)
    blended_rate = (price.prompt_inr_per_1k + price.completion_inr_per_1k) / 2
    blended = 900 * blended_rate / 1000
    assert prompt_heavy < blended, "a blended rate would over-charge prompt-heavy traffic"


def test_the_cheap_tier_really_is_cheaper() -> None:
    """The entire routing argument. If this inverts, the router is losing money."""
    book = PriceBook.default()
    assert book.cheaper_of("qwen3:4b", "qwen3:8b") == "qwen3:4b"
    assert book.cheaper_of("gpt-4o", "gpt-4o-mini") == "gpt-4o-mini"


def test_local_models_are_not_free() -> None:
    """They are unmetered, which is a different thing. Pricing them at zero would make
    every efficiency claim trivially true and completely meaningless."""
    book = PriceBook.default()
    for model in ("qwen3:4b", "qwen3:8b"):
        price = book.price_for(model)
        assert price.completion_inr_per_1k > 0
        assert price.imputed is True
        assert price.basis, "an imputed price with no stated basis is a guess"


def test_every_default_price_records_where_it_came_from() -> None:
    for price in DEFAULT_PRICES:
        assert price.basis, f"{price.model} has no basis"


def test_an_unpriced_model_is_reported_not_silently_defaulted() -> None:
    """Silently applying a default to a model somebody added last week is how a spend
    report drifts away from the invoice without anyone noticing."""
    book = PriceBook.default()
    book.cost_inr("mistral-nemo:12b", prompt_tokens=100)
    assert "mistral-nemo:12b" in book.report()["unknown_models"]


def test_the_fallback_is_expensive_on_purpose() -> None:
    """An unpriced model should make the bill look worse than it is, so somebody goes
    and prices it. A cheap default would never get noticed."""
    book = PriceBook.default()
    unknown = book.cost_inr("who-knows", prompt_tokens=1000, completion_tokens=1000)
    cheapest = min(
        book.cost_inr(p.model, prompt_tokens=1000, completion_tokens=1000) for p in DEFAULT_PRICES
    )
    assert unknown > cheapest


def test_a_version_suffix_is_not_an_unknown_model() -> None:
    """A provider renaming a snapshot is not the same event as somebody adding an
    unpriced model, and conflating them would fill the report with false alarms."""
    book = PriceBook.default()
    assert book.price_for("gpt-4o-2024-11-20").model == "gpt-4o"
    assert book.report()["unknown_models"] == {}


def test_prices_load_from_config(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "model": "custom",
                        "prompt_inr_per_1k": 1.0,
                        "completion_inr_per_1k": 2.0,
                        "basis": "negotiated",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    book = load_price_book(path)
    assert book.price_for("custom").basis == "negotiated"
    assert book.price_for("gpt-4o").model == "gpt-4o", "defaults must survive an override file"


def test_a_malformed_price_file_raises_rather_than_reverting(tmp_path: Path) -> None:
    """Quietly ignoring a price file somebody wrote is how a deployment ends up
    reporting on rates nobody intended."""
    path = tmp_path / "prices.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_price_book(path)


def test_a_missing_price_file_is_fine() -> None:
    """A clean checkout must still produce a spend report."""
    assert load_price_book(Path("does-not-exist.json")).price_for("gpt-4o").model == "gpt-4o"


# --------------------------------------------------------------------------- #
# Cost regret
# --------------------------------------------------------------------------- #


def _shadow(regret: float, sufficed: bool = True, rid: str = "r") -> ShadowResult:
    return ShadowResult(
        request_id=rid,
        served_model="qwen3:8b",
        cheaper_model="qwen3:4b",
        served_inr=regret + 1.0,
        cheaper_inr=1.0,
        cheaper_action="L0_pass" if sufficed else "L2_repair",
        cheaper_sufficed=sufficed,
    )


def test_regret_is_zero_when_the_upgrade_was_justified() -> None:
    """A router that never over-spends is a router that is not routing -- but an
    upgrade the cheap model could not have matched is money well spent."""
    assert _shadow(5.0, sufficed=False).regret_inr == 0.0
    assert _shadow(5.0, sufficed=True).regret_inr == 5.0


def test_the_estimate_always_carries_an_interval() -> None:
    ledger = RegretLedger()
    for index in range(60):
        ledger.strong_tier_requests += 1
        ledger.record(_shadow(2.0 if index % 3 else 0.0, rid=f"r{index}"))
    estimate = ledger.estimate()
    assert estimate.ci_low_inr < estimate.mean_regret_inr < estimate.ci_high_inr
    assert "[" in estimate.statement() and "confidence" in estimate.statement()


def test_a_small_sample_says_so_rather_than_pretending() -> None:
    """A regret figure from six observations is arithmetic, not evidence."""
    ledger = RegretLedger()
    for index in range(6):
        ledger.strong_tier_requests += 1
        ledger.record(_shadow(3.0, rid=f"r{index}"))
    estimate = ledger.estimate()
    assert not estimate.reliable
    assert "indicative only" in estimate.statement()
    assert any(str(MIN_SAMPLE_FOR_CONFIDENCE) in note for note in estimate.notes)


def test_no_shadow_runs_means_unmeasured_not_zero() -> None:
    """The distinction the whole ledger turns on."""
    estimate = RegretLedger().estimate()
    assert estimate.estimated_total_regret_inr == 0.0
    assert "unmeasured, not zero" in estimate.statement()


def test_the_point_and_the_interval_are_scaled_together() -> None:
    """Reporting a scaled point estimate beside an unscaled interval is a units error
    that looks like precision."""
    ledger = RegretLedger()
    ledger.strong_tier_requests = 1000
    for index in range(50):
        ledger.record(_shadow(4.0 if index % 2 else 0.0, rid=f"r{index}"))
    estimate = ledger.estimate()
    assert estimate.estimated_total_regret_inr == pytest.approx(estimate.mean_regret_inr * 1000)
    assert estimate.estimated_total_ci[0] == pytest.approx(estimate.ci_low_inr * 1000)


def test_only_strong_tier_traffic_is_shadowed() -> None:
    """Shadowing cheap-tier requests asks whether an even cheaper model would do, which
    is a different and much less interesting question. The money is in the paid tier."""
    ledger = RegretLedger(sample_rate=1.0)
    assert ledger.should_shadow("strong") is True
    assert ledger.should_shadow("cheap") is False
    assert ledger.strong_tier_requests == 1


def test_universal_sufficiency_is_flagged_as_suspicious() -> None:
    ledger = RegretLedger()
    for index in range(40):
        ledger.strong_tier_requests += 1
        ledger.record(_shadow(1.0, sufficed=True, rid=f"r{index}"))
    assert any("EVERY sampled request" in note for note in ledger.estimate().notes)


def test_the_bootstrap_interval_does_not_go_negative_on_a_zero_heavy_sample() -> None:
    """Per-request regret is mostly zeros with a few large values. A normal-approximation
    interval would be symmetric and would extend below zero -- implying we might have
    SAVED money by over-spending."""
    values = [0.0] * 90 + [50.0] * 10
    low, high = bootstrap_ci(values)
    assert low >= 0.0
    assert low < sum(values) / len(values) < high


def test_bootstrap_handles_degenerate_samples() -> None:
    assert bootstrap_ci([]) == (0.0, 0.0)
    assert bootstrap_ci([7.0]) == (7.0, 7.0)


def test_the_bootstrap_is_deterministic() -> None:
    """A confidence interval nobody can reproduce is not evidence."""
    values = [0.0, 1.0, 5.0, 0.0, 12.0, 0.0, 3.0]
    assert bootstrap_ci(values, seed=1) == bootstrap_ci(values, seed=1)


# --------------------------------------------------------------------------- #
# Rework attribution
# --------------------------------------------------------------------------- #


def _turn(rid: str, question: str, ts: float, cost: float = 10.0, **kwargs: object) -> SessionTurn:
    return SessionTurn(
        request_id=rid, session_id="s1", question=question, ts=ts, cost_inr=cost, **kwargs
    )


def test_a_re_asked_question_is_attributed_to_the_answer_that_failed() -> None:
    edges = ReworkLedger().attribute(
        [
            _turn("r1", "What is the prepayment charge on my home loan?", 0.0),
            _turn("r2", "What is the prepayment charge on my home loan", 30.0),
        ]
    )
    assert len(edges) == 1
    assert edges[0].kind == "retry"
    assert edges[0].parent_request_id == "r1"


def test_an_unrelated_follow_up_is_not_rework() -> None:
    """Rework is the number that argues hardest for the product, so it is the one that
    most needs to resist flattering itself."""
    edges = ReworkLedger().attribute(
        [
            _turn("r1", "What is the prepayment charge on my home loan?", 0.0),
            _turn("r2", "What are the branch timings in Mumbai?", 30.0),
        ]
    )
    assert edges == []


def test_a_question_re_asked_tomorrow_is_a_new_conversation() -> None:
    edges = ReworkLedger().attribute(
        [
            _turn("r1", "What is the prepayment charge on my home loan?", 0.0),
            _turn("r2", "What is the prepayment charge on my home loan?", 90_000.0),
        ]
    )
    assert edges == []


def test_an_inferred_retry_is_charged_at_its_confidence_not_in_full() -> None:
    """Charging the full amount on a maybe lets a coincidental follow-up inflate the
    rework figure."""
    edges = ReworkLedger().attribute(
        [
            _turn("r1", f"{_LONG} nine", 0.0),
            _turn("r2", f"{_LONG} seven", 20.0, cost=10.0),
        ]
    )
    assert len(edges) == 1
    edge = edges[0]
    assert edge.confidence < 1.0
    assert edge.inr_charged == pytest.approx(10.0 * edge.confidence)
    assert edge.inr_charged < 10.0


def test_a_retry_at_the_threshold_still_charges_something() -> None:
    """The floor exists because scaling confidence from zero at the bar would make the
    bar meaningless -- everything between 0.90 and 0.91 similarity would be detected
    and then costed at approximately nothing."""
    edges = ReworkLedger().attribute(
        [
            _turn("r1", f"{_LONG} nine", 0.0),
            _turn("r2", f"{_LONG} seven", 20.0, cost=10.0),
        ]
    )
    assert edges[0].confidence >= RETRY_CONFIDENCE_FLOOR
    assert edges[0].inr_charged > 0.0


def test_a_verbatim_repeat_is_charged_more_than_a_near_miss() -> None:
    verbatim = ReworkLedger().attribute(
        [
            _turn("r1", f"{_LONG} nine", 0.0),
            _turn("r2", f"{_LONG} nine", 20.0),
        ]
    )
    near = ReworkLedger().attribute(
        [
            _turn("r1", f"{_LONG} nine", 0.0),
            _turn("r2", f"{_LONG} seven", 20.0),
        ]
    )
    assert verbatim and near
    assert verbatim[0].confidence > near[0].confidence


def test_an_explicit_regenerate_beats_inference() -> None:
    edges = ReworkLedger().attribute(
        [
            _turn("r1", "What is the fee?", 0.0),
            _turn("r2", "Something else entirely", 10.0, explicit_regenerate=True),
        ]
    )
    assert len(edges) == 1
    assert edges[0].kind == "regenerate"
    assert edges[0].confidence > 0.9


def test_a_human_escalation_charges_the_reviewer_s_time_too() -> None:
    """The reviewer is paid whether they approve or reject, and that cost was caused by
    the answer that got held."""
    ledger = ReworkLedger(human_review_inr=220.0)
    edges = ledger.attribute(
        [
            _turn("r1", "Can you email my claim summary?", 0.0, raised_hold_id="hold_1"),
            _turn("r2", "resolved", 300.0, cost=5.0, resolves_hold_id="hold_1"),
        ]
    )
    assert len(edges) == 1
    assert edges[0].kind == "human_escalation"
    assert edges[0].confidence == 1.0
    assert edges[0].inr_charged == pytest.approx(225.0)


def test_escalation_is_not_bounded_by_the_retry_window() -> None:
    """A human takes 15 minutes by policy. A time window built for retries would miss
    every escalation, which is the most expensive edge there is."""
    edges = ReworkLedger().attribute(
        [
            _turn("r1", "q", 0.0, raised_hold_id="hold_1"),
            _turn("r2", "resolved", 3_600.0, resolves_hold_id="hold_1"),
        ]
    )
    assert len(edges) == 1
    assert edges[0].kind == "human_escalation"


def test_the_report_says_which_edges_were_inferred() -> None:
    ledger = ReworkLedger()
    ledger.attribute(
        [
            _turn("r1", "prepayment charge home loan floating rate", 0.0),
            _turn("r2", "prepayment charge home loan floating rate", 20.0),
        ]
    )
    report = ledger.report()
    assert report["by_kind"]["retry"]["count"] == 1
    assert any("INFERRED" in note for note in report["notes"])


def test_the_worst_parents_are_ranked() -> None:
    """ "Which answer cost us the most afterwards" is the question an operator asks."""
    ledger = ReworkLedger()
    ledger.attribute(
        [
            _turn("r1", "q", 0.0, raised_hold_id="h1"),
            _turn("r2", "x", 10.0, cost=5.0, resolves_hold_id="h1"),
        ]
    )
    ledger.attribute(
        [
            _turn("r3", "prepayment charge home loan floating", 0.0),
            _turn("r4", "prepayment charge home loan floating", 20.0, cost=1.0),
        ]
    )
    worst = ledger.report()["worst_parents"]
    assert worst[0]["request_id"] == "r1"


def test_similarity_ignores_function_words() -> None:
    """Left in, two unrelated banking questions sit around 0.5 purely on 'what is my'."""
    assert similarity("What is my balance?", "What is my address?") < 0.6
    # Clamped: identical inputs come out a float epsilon above 1 without it, and a
    # similarity above 1.0 in an edge's reason string reads as a bug.
    assert similarity("prepayment charge floating", "prepayment charge floating") == 1.0
    assert similarity("", "anything") == 0.0
