"""Manual anchor label export/import."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from scripts.build_manual_anchor import build_labels, import_labels, summary, write_jsonl

from interlock.eval.anchor import ANCHOR_FAILURE_MODES, CHALLENGE_LEVELS
from interlock.eval.induce import TripleGenerator
from interlock.ledger.writer import connect
from interlock.retrieval.chunker import Chunk

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.json"
GENERATED_SOURCE = "generated_anchor_from_calibration_split"
GENERATOR_LABELLER = "interlock_anchor_generator_v1"


@pytest.fixture(scope="module")
def anchor_rows() -> list[dict[str, Any]]:
    return build_labels(300, seed=20260829)


def expected_evidence_cluster_id(row: dict[str, Any]) -> str:
    payload = row["payload"]
    encoded = json.dumps(
        {
            "answer": payload["answer"],
            "context": payload["context"],
            "gold": [
                row["gold_ungrounded"],
                row["gold_contradicted"],
                row["gold_unsafe"],
            ],
            "failure_mode": payload["failure_mode"],
            "challenge_level": payload["challenge_level"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"evidence-{hashlib.sha256(encoded).hexdigest()}"


def test_manual_anchor_has_the_approved_200_100_mode_matrix(
    anchor_rows: list[dict[str, Any]],
) -> None:
    rows = anchor_rows
    assert Counter(row["payload"]["failure_mode"] for row in rows) == {
        "clean": 200,
        "retrieval_dropped": 20,
        "number_corrupted": 20,
        "clause_swapped": 20,
        "unanswerable": 20,
        "contradiction": 20,
    }
    assert sum(row["gold_ungrounded"] for row in rows) == 80
    assert sum(row["gold_contradicted"] for row in rows) == 20


def test_every_mode_has_its_declared_challenge_levels(
    anchor_rows: list[dict[str, Any]],
) -> None:
    rows = anchor_rows
    grouped = Counter(
        (row["payload"]["failure_mode"], row["payload"]["challenge_level"]) for row in rows
    )
    assert [grouped[("clean", level)] for level in CHALLENGE_LEVELS] == [67, 67, 66]
    for mode in ANCHOR_FAILURE_MODES:
        assert [grouped[(mode, level)] for level in CHALLENGE_LEVELS] == [7, 7, 6]


def test_manual_anchor_has_exactly_the_requested_count(
    anchor_rows: list[dict[str, Any]],
) -> None:
    report = summary(anchor_rows)
    assert report["count"] == 300
    assert report["clean"] + report["gold_ungrounded"] + report["gold_contradicted"] == 300
    assert report["source"].startswith("calibration split")
    assert report["mode_counts"]["clean"] == 200
    assert report["challenge_level_counts"] == {
        "L1_direct": 102,
        "L2_distractor": 102,
        "L3_conflict": 96,
    }


def test_exact_generation_fails_when_a_mode_cannot_be_satisfied() -> None:
    chunk = Chunk(
        doc_id="only-document",
        chunk_id="only-document#0",
        title="Only policy",
        text="Only policy\n\nThis passage is long enough to supply a clean answer but has no pair.",
        body="This passage is long enough to supply a clean answer but has no pair.",
        domain="general",
        provenance="retrieved_verified",
        ordinal=0,
    )
    generator = TripleGenerator(chunks=[chunk], seed=7)

    with pytest.raises(ValueError, match="cannot satisfy exact quota"):
        generator.generate_exact({"contradiction": 1})


def test_manual_anchor_is_deterministic_for_a_fixed_seed() -> None:
    first = json.dumps(build_labels(300, seed=7), sort_keys=True)
    second = json.dumps(build_labels(300, seed=7), sort_keys=True)
    assert first == second


def test_manual_anchor_records_context_and_review_metadata(
    anchor_rows: list[dict[str, Any]],
) -> None:
    for row in anchor_rows:
        payload = row["payload"]
        assert payload["context_count"] == len(payload["context"])
        assert payload["context_doc_ids"] == [fragment["doc_id"] for fragment in payload["context"]]
        assert payload["domain"]
        assert row["review_basis"]


def test_anchor_answers_are_complete_supported_or_contradicting_propositions(
    anchor_rows: list[dict[str, Any]],
) -> None:
    for row in anchor_rows:
        payload = row["payload"]
        if payload["failure_mode"] == "clean":
            assert payload["answer"] in payload["context"][0]["text"]
            assert payload["answer"].endswith((".", "!", "?"))
        if payload["failure_mode"] == "contradiction":
            contradicting_body = payload["context"][1]["text"].split("\n\n", 1)[1].strip()
            assert payload["answer"] == contradicting_body
            assert payload["answer"].endswith((".", "!", "?"))


def test_anchor_rows_have_stable_evidence_clusters_and_honest_summary(
    anchor_rows: list[dict[str, Any]],
) -> None:
    cluster_counts = Counter(row["payload"]["evidence_cluster_id"] for row in anchor_rows)
    for row in anchor_rows:
        assert row["payload"]["evidence_cluster_id"] == expected_evidence_cluster_id(row)
    report = summary(anchor_rows)
    assert report["unique_evidence_clusters"] == len(cluster_counts)
    assert report["prompt_variants"] == 300
    assert report["max_cluster_size"] == max(cluster_counts.values())


def test_generated_anchor_is_explicitly_unreviewed(
    anchor_rows: list[dict[str, Any]],
) -> None:
    for row in anchor_rows:
        assert row["source"] == GENERATED_SOURCE
        assert row["labeller"] == GENERATOR_LABELLER
        assert row["review_status"] == "unreviewed"
        assert "not manually reviewed" in row["review_basis"]


def test_manual_anchor_rejects_counts_without_an_approved_matrix() -> None:
    with pytest.raises(ValueError, match="defined only for exactly 300 rows"):
        build_labels(299, seed=20260829)


def test_manual_anchor_jsonl_round_trips(tmp_path: Path, anchor_rows: list[dict[str, Any]]) -> None:
    rows = anchor_rows[:12]
    path = tmp_path / "anchor.jsonl"
    write_jsonl(rows, path)
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(loaded) == 12
    assert loaded[0]["labeller"] == GENERATOR_LABELLER


def test_manual_anchor_imports_to_labels_table(
    tmp_path: Path, anchor_rows: list[dict[str, Any]]
) -> None:
    db = tmp_path / "labels.db"
    rows = anchor_rows[:12]
    import_labels(rows, db)
    connection = connect(db)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM labels WHERE labeller=?", (GENERATOR_LABELLER,)
        ).fetchone()[0]
        assert count == 12
    finally:
        connection.close()


def test_the_committed_anchor_artifact_matches_the_approved_matrix() -> None:
    path = REPO_ROOT / "data" / "labels" / "manual_anchor_300.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 300
    assert len({row["item_id"] for row in rows}) == 300
    assert Counter(row["payload"]["failure_mode"] for row in rows) == {
        "clean": 200,
        "retrieval_dropped": 20,
        "number_corrupted": 20,
        "clause_swapped": 20,
        "unanswerable": 20,
        "contradiction": 20,
    }
    grouped = Counter(
        (row["payload"]["failure_mode"], row["payload"]["challenge_level"]) for row in rows
    )
    assert [grouped[("clean", level)] for level in CHALLENGE_LEVELS] == [67, 67, 66]
    for mode in ANCHOR_FAILURE_MODES:
        assert [grouped[(mode, level)] for level in CHALLENGE_LEVELS] == [7, 7, 6]
    assert sum(row["gold_ungrounded"] for row in rows) == 80
    assert sum(row["gold_contradicted"] for row in rows) == 20
    assert sum(row["gold_unsafe"] for row in rows) == 0
    for row in rows:
        payload = row["payload"]
        assert payload["context_count"] == len(payload["context"])
        assert payload["context_doc_ids"] == [fragment["doc_id"] for fragment in payload["context"]]
        assert payload["domain"]
        assert payload["evidence_cluster_id"] == expected_evidence_cluster_id(row)


def test_committed_summary_reports_actual_evidence_clusters() -> None:
    labels_path = REPO_ROOT / "data" / "labels" / "manual_anchor_300.jsonl"
    summary_path = REPO_ROOT / "data" / "labels" / "manual_anchor_300.summary.json"
    rows = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines()]
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    clusters = Counter(row["payload"]["evidence_cluster_id"] for row in rows)
    assert report["unique_evidence_clusters"] == len(clusters)
    assert report["prompt_variants"] == len(rows)
    assert report["max_cluster_size"] == max(clusters.values())


@pytest.mark.parametrize("seed", [39, 86])
def test_anchor_builds_clustered_prompt_variants_across_seeds(seed: int) -> None:
    rows = build_labels(300, seed=seed)
    assert len(rows) == 300
    assert all(row["payload"]["evidence_cluster_id"] for row in rows)


def test_committed_contradictions_follow_manifest_direction() -> None:
    path = REPO_ROOT / "data" / "labels" / "manual_anchor_300.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    contradicts = {entry["doc_id"]: entry.get("contradicts") for entry in manifest["documents"]}
    contradiction_rows = [row for row in rows if row["payload"]["failure_mode"] == "contradiction"]
    assert len(contradiction_rows) == 20
    for row in contradiction_rows:
        payload = row["payload"]
        authoritative, contradicting = [
            doc_id.split("#", 1)[0] for doc_id in payload["context_doc_ids"][:2]
        ]
        assert payload["source_doc_id"] == authoritative
        assert contradicts[contradicting] == authoritative
        assert payload["answer"] in payload["context"][1]["text"]


def test_committed_anchor_rows_match_the_canonical_builder() -> None:
    path = REPO_ROOT / "data" / "labels" / "manual_anchor_300.jsonl"
    committed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert committed == build_labels(300, seed=20260829)
