"""Manual anchor label export/import."""

from __future__ import annotations

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


@pytest.fixture(scope="module")
def anchor_rows() -> list[dict[str, Any]]:
    return build_labels(300, seed=20260829)


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


def test_manual_anchor_rejects_counts_without_an_approved_matrix() -> None:
    with pytest.raises(ValueError, match="defined only for exactly 300 rows"):
        build_labels(299, seed=20260829)


def test_manual_anchor_jsonl_round_trips(tmp_path: Path, anchor_rows: list[dict[str, Any]]) -> None:
    rows = anchor_rows[:12]
    path = tmp_path / "anchor.jsonl"
    write_jsonl(rows, path)
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(loaded) == 12
    assert loaded[0]["labeller"] == "person1_codex_manual_review"


def test_manual_anchor_imports_to_labels_table(
    tmp_path: Path, anchor_rows: list[dict[str, Any]]
) -> None:
    db = tmp_path / "labels.db"
    rows = anchor_rows[:12]
    import_labels(rows, db)
    connection = connect(db)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM labels WHERE labeller='person1_codex_manual_review'"
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
