"""Manual anchor label export/import."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.build_manual_anchor import build_labels, import_labels, summary, write_jsonl
from interlock.ledger.writer import connect


def test_manual_anchor_has_exactly_the_requested_count() -> None:
    rows = build_labels(30, seed=20260829)
    report = summary(rows)
    assert report["count"] == 30
    assert report["clean"] + report["gold_ungrounded"] + report["gold_contradicted"] == 30
    assert report["source"].startswith("calibration split")


def test_manual_anchor_jsonl_round_trips(tmp_path: Path) -> None:
    rows = build_labels(12, seed=20260829)
    path = tmp_path / "anchor.jsonl"
    write_jsonl(rows, path)
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(loaded) == 12
    assert loaded[0]["labeller"] == "person1_codex_manual_review"


def test_manual_anchor_imports_to_labels_table(tmp_path: Path) -> None:
    db = tmp_path / "labels.db"
    rows = build_labels(12, seed=20260829)
    import_labels(rows, db)
    connection = connect(db)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM labels WHERE labeller='person1_codex_manual_review'"
        ).fetchone()[0]
        assert count == 12
    finally:
        connection.close()
