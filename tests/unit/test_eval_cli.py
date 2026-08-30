"""Seeded-evaluation CLI artifact contract tests."""

from __future__ import annotations

from scripts.eval import build_report_payload

from interlock.eval.metrics import MetricResult, MetricSet


def test_report_payload_keeps_metrics_flat_and_notes_adjacent() -> None:
    """Catches nesting MetricSet.to_dict() under a second ``metrics`` key."""
    metrics = MetricSet(
        metrics=[
            MetricResult(
                name="Catch",
                value=1.0,
                unit="%",
                target=">= 90%",
                met=True,
            )
        ],
        notes=["generated seeded evaluation"],
    )

    payload = build_report_payload(
        seed=7,
        conformal_filter=False,
        n_cases=2,
        n_defective=1,
        policy_version="banking-test@sha256:abc",
        metrics=metrics,
        actions={"L0_pass": 1, "L2_repair": 1},
        misses=[],
    )

    assert payload["metrics"] == [
        {
            "name": "Catch",
            "value": 1.0,
            "unit": "%",
            "target": ">= 90%",
            "met": True,
            "ci": None,
            "numerator": None,
            "denominator": None,
            "note": "",
        }
    ]
    assert payload["notes"] == ["generated seeded evaluation"]
    assert not isinstance(payload["metrics"], dict)
