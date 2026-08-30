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


def _review_attestation() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "review_status": "human_verified",
        "reviewer_role": "project_author",
        "reviewed_at": "2026-08-31",
        "reviewed_items": 6,
        "review_scope": ["ground_truth_labels", "openrouter_judgments"],
        "labels_digest": "sha256:cc8e4cdbfd13268e00323720fcf2ba5137be79fee2ea2c78b74647f025e8d041",
        "judgments_digest": "sha256:10f4d4f61fc077b64d0fbd1c063ec5cbf7079f33f62b2f9e1a69985ba6a9d81c",
        "statement": (
            "The project author manually verified the ground-truth label and "
            "GPT-4o Mini judgment for every item."
        ),
    }


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


def test_anchor_report_marks_only_digest_matched_attestations_as_human_reviewed() -> None:
    """Catches a stale review attestation being applied to changed labels or judgments."""
    labels, judgments = _fixture()

    report = build_anchor_report(
        labels,
        judgments,
        model="openai/gpt-4o-mini",
        review_attestation=_review_attestation(),
    )
    markdown = render_anchor_markdown(report)

    assert report["source"] == {
        "kind": "human_reviewed_openrouter_judge_on_generated_anchor",
        "human_reviewed": True,
        "production_traffic": False,
        "review_status": "human_verified",
        "reviewer_role": "project_author",
        "reviewed_at": "2026-08-31",
        "reviewed_items": 6,
        "review_scope": ["ground_truth_labels", "openrouter_judgments"],
        "labels_digest": _review_attestation()["labels_digest"],
        "judgments_digest": _review_attestation()["judgments_digest"],
        "review_statement": _review_attestation()["statement"],
        "taxonomy_warning": (
            "These generated anchors and the external-model judgments were manually "
            "verified item by item. This remains offline evidence, not production traffic "
            "or the product's stakes-aware intervention rate."
        ),
    }
    assert "Human-reviewed external-model audit" in markdown
    assert "NOT human-reviewed" not in markdown

    changed_labels = [dict(row) for row in labels]
    changed_labels[0]["review_basis"] = "changed after the attested review"
    with pytest.raises(ValueError, match="labels_digest"):
        build_anchor_report(
            changed_labels,
            judgments,
            model="openai/gpt-4o-mini",
            review_attestation=_review_attestation(),
        )
