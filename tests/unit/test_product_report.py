"""Tests for the submission evidence index and its honesty rules."""

from __future__ import annotations

from interlock.eval.product_report import build_product_report, render_product_markdown


def _metric(name: str, value: float, met: bool | None) -> dict[str, object]:
    return {"name": name, "value": value, "met": met, "ci": [value, value]}


def test_product_report_keeps_pass_miss_inconclusive_and_absence_distinct() -> None:
    anchor = {
        "source": {"human_reviewed": False, "production_traffic": False},
        "validity": {"rate": 1.0},
        "agreement": {
            "binary_grounding": {"rate": 0.88},
            "false_intervention_on_clean": {"rate": 0.085},
            "grounding_escape": {"rate": 0.20},
        },
    }
    seeded = [
        {
            "seed": 1,
            "policy_version": "banking-v4@sha256:x",
            "metrics": [
                _metric("Pre-Action Catch Rate", 1.0, True),
                _metric("Ungrounded escapes", 0.0, True),
                _metric("False interventions", 0.65, False),
            ],
        }
    ]
    comparison = {
        "selected": {
            "name": "selected",
            "eligible": True,
            "worst_seed_false_intervention_rate": 0.65,
            "worst_seed_disruptive_rate": 0.65,
        },
        "candidates": [
            {
                "name": "impact-1_deadband-0_nuisance-1",
                "worst_seed_false_intervention_rate": 0.92,
            }
        ],
    }
    artifacts = {
        "calibration": {"ece": 0.01},
        "conformal": {"escape_rate": 0.0, "intervention_rate": 1.0},
        "load": {"gateway_latency_report": {"within_budget": False}},
        "fairness": {"n_pairs": 5, "offline": True},
        "security": {"passed": True},
        "economics": None,
        "penetration_test": None,
    }

    report = build_product_report(anchor, seeded, comparison, artifacts)

    assert report["checks"]["seeded_safety"]["status"] == "pass"
    assert report["checks"]["false_intervention"]["status"] == "miss"
    assert report["checks"]["openrouter_anchor"]["status"] == "inconclusive"
    assert report["checks"]["conformal_operability"]["status"] == "inconclusive"
    assert report["checks"]["load_latency"]["status"] == "miss"
    assert report["checks"]["fairness"]["status"] == "inconclusive"
    assert report["checks"]["security_sweep"]["status"] == "pass"
    assert report["checks"]["production_economics"]["status"] == "unavailable"
    assert report["checks"]["penetration_test"]["status"] == "not_run"
    assert report["overall_status"] == "miss"


def test_product_report_retains_provenance_failed_metrics_and_no_fake_zeroes() -> None:
    report = build_product_report(
        {
            "source": {"human_reviewed": False, "production_traffic": False},
            "validity": {"rate": 1.0},
            "agreement": {
                "binary_grounding": {"rate": 0.8},
                "false_intervention_on_clean": {"rate": 0.1},
                "grounding_escape": {"rate": 0.2},
            },
        },
        [
            {
                "seed": 1,
                "policy_version": "v",
                "metrics": [_metric("False interventions", 0.5, False)],
            }
        ],
        {"selected": {"name": "x", "eligible": True}, "candidates": []},
        {},
    )
    markdown = render_product_markdown(report)

    assert report["provenance"]["openrouter_anchor"] == "generated_unreviewed"
    assert report["seeded_metrics"]["False interventions"]["values"] == [0.5]
    assert report["checks"]["production_economics"]["value"] is None
    assert "generated/unreviewed" in markdown
    assert "unavailable" in markdown
    assert "False interventions" in markdown
