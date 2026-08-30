"""Tests for honest OpenRouter anchor-run reporting."""

from __future__ import annotations

from typing import Any

import pytest

from interlock.eval.anchor_report import build_anchor_report, render_anchor_markdown


def _label(
    item_id: str,
    gold: str,
    *,
    mode: str,
    level: str,
    domain: str,
    cluster: str,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "gold_ungrounded": int(gold == "ungrounded"),
        "gold_contradicted": int(gold == "contradicted"),
        "gold_unsafe": 0,
        "review_status": "unreviewed",
        "review_basis": "automatically induced; not manually reviewed",
        "payload": {
            "failure_mode": mode,
            "challenge_level": level,
            "domain": domain,
            "evidence_cluster_id": cluster,
        },
    }


def _judgment(
    item_id: str,
    gold: str,
    label: str | None,
    *,
    batch: str,
    status: str = "valid",
    cost: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "model": "openai/gpt-4o-mini",
        "gold": gold,
        "judge_label": label,
        "status": status,
        "batch_id": batch,
        "accounted_cost_usd": cost,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": float(cost),
        },
        "latency_ms": latency_ms,
        "error": None if status == "valid" else "invalid output",
        "rationale": "Evidence comparison.",
    }


def _fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labels = [
        _label("c1", "clean", mode="clean", level="L1", domain="loans", cluster="a"),
        _label("c2", "clean", mode="clean", level="L2", domain="loans", cluster="a"),
        _label("c3", "clean", mode="clean", level="L3", domain="fees", cluster="b"),
        _label(
            "u1", "ungrounded", mode="retrieval_dropped", level="L1", domain="loans", cluster="a"
        ),
        _label("u2", "ungrounded", mode="unanswerable", level="L2", domain="fees", cluster="b"),
        _label(
            "x1", "contradicted", mode="contradiction", level="L3", domain="claims", cluster="c"
        ),
    ]
    judgments = [
        _judgment(
            "c1",
            "clean",
            "clean",
            batch="b1",
            cost="0.1",
            prompt_tokens=100,
            completion_tokens=10,
            latency_ms=10,
        ),
        _judgment(
            "c2",
            "clean",
            "ungrounded",
            batch="b1",
            cost="0.1",
            prompt_tokens=100,
            completion_tokens=10,
            latency_ms=10,
        ),
        _judgment(
            "c3",
            "clean",
            None,
            batch="b2",
            status="invalid_json",
            cost="0.2",
            prompt_tokens=200,
            completion_tokens=20,
            latency_ms=20,
        ),
        _judgment(
            "u1",
            "ungrounded",
            "ungrounded",
            batch="b3",
            cost="0.3",
            prompt_tokens=300,
            completion_tokens=30,
            latency_ms=30,
        ),
        _judgment(
            "u2",
            "ungrounded",
            "clean",
            batch="b3",
            cost="0.3",
            prompt_tokens=300,
            completion_tokens=30,
            latency_ms=30,
        ),
        _judgment(
            "x1",
            "contradicted",
            "ungrounded",
            batch="b3",
            cost="0.3",
            prompt_tokens=300,
            completion_tokens=30,
            latency_ms=30,
        ),
    ]
    return labels, judgments


def test_anchor_report_computes_strict_binary_validity_and_request_usage() -> None:
    labels, judgments = _fixture()

    report = build_anchor_report(labels, judgments, model="openai/gpt-4o-mini")

    assert report["source"]["human_reviewed"] is False
    assert "generated" in report["source"]["taxonomy_warning"].lower()
    assert report["validity"]["valid"] == 5
    assert report["validity"]["total"] == 6
    assert report["validity"]["rate"] == pytest.approx(5 / 6)
    assert len(report["validity"]["ci_95"]) == 2
    assert report["agreement"]["strict_three_class"]["correct"] == 2
    assert report["agreement"]["strict_three_class"]["rate"] == pytest.approx(2 / 5)
    assert report["agreement"]["binary_grounding"]["correct"] == 3
    assert report["agreement"]["binary_grounding"]["rate"] == pytest.approx(3 / 5)
    assert report["agreement"]["false_intervention_on_clean"]["rate"] == pytest.approx(1 / 2)
    assert report["agreement"]["grounding_escape"]["rate"] == pytest.approx(1 / 3)
    assert report["usage"] == {
        "request_count": 3,
        "prompt_tokens": 600,
        "completion_tokens": 60,
        "accounted_cost_usd": "0.6",
        "latency_ms": {"median": 20.0, "p95": 30.0},
    }


def test_anchor_report_keeps_slices_clusters_invalids_and_bounded_failures() -> None:
    labels, judgments = _fixture()

    report = build_anchor_report(labels, judgments, model="openai/gpt-4o-mini")
    markdown = render_anchor_markdown(report)

    assert set(report["slices"]) == {"failure_mode", "challenge_level", "domain"}
    assert report["slices"]["failure_mode"]["clean"]["valid"] == 2
    assert report["evidence_clusters"]["unique"] == 3
    assert report["evidence_clusters"]["with_multiple_items"] == 2
    assert report["invalid_results"]["by_status"] == {"invalid_json": 1}
    assert report["invalid_results"]["examples"] == ["c3"]
    assert len(report["failed_examples"]) == 3
    assert "NOT human-reviewed" in markdown
    assert "False intervention on clean anchors" in markdown
    assert "openai/gpt-4o-mini" in markdown
