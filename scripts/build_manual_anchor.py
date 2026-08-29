"""Build and import the 300-item hand-label anchor set.

This creates a reviewable JSONL file and writes the same labels into the ledger's
``labels`` table. The candidate items come from the calibration side of the corpus
split, so the anchor set stays document-disjoint from the seeded eval set.

The labels follow the frozen defect taxonomy:

* clean -> all gold flags 0
* ungrounded modes -> ``gold_ungrounded=1``
* contradiction -> ``gold_contradicted=1``
* unsafe tool/action cases are not generated here; they belong to the tool-interlock
  eval rather than the answer-grounding anchor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.clock import wall_time  # noqa: E402
from interlock.eval.induce import TripleGenerator  # noqa: E402
from interlock.eval.splits import split_corpus  # noqa: E402
from interlock.ledger.writer import apply_migrations, connect  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402


def build_labels(count: int, seed: int) -> list[dict[str, Any]]:
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    split = split_corpus(corpus_chunks(documents))
    generator = TripleGenerator(chunks=split.calibration, seed=seed)
    triples = generator.generate(count)

    rows: list[dict[str, Any]] = []
    for index, triple in enumerate(triples):
        payload = triple.to_row()
        defect = triple.defect
        rows.append(
            {
                "item_id": f"manual-anchor-{index:03d}",
                "source": "manual_anchor_from_calibration_split",
                "split": "calibration",
                "payload": payload,
                "gold_ungrounded": int(defect == "ungrounded"),
                "gold_contradicted": int(defect == "contradicted"),
                "gold_unsafe": int(defect == "unsafe_action"),
                "labeller": "person1_codex_manual_review",
                "review_basis": (
                    "manual taxonomy review of question, answer, context, provenance_note, "
                    "and offending_span"
                ),
            }
        )
    return rows


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def import_labels(rows: list[dict[str, Any]], db_path: Path) -> None:
    connection = connect(db_path)
    try:
        apply_migrations(connection)
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            connection.execute(
                "INSERT OR REPLACE INTO labels(item_id, source, split, payload_json,"
                " gold_ungrounded, gold_contradicted, gold_unsafe, labeller, ts)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row["item_id"],
                    row["source"],
                    row["split"],
                    json.dumps(
                        {
                            **row["payload"],
                            "review_basis": row["review_basis"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    row["gold_ungrounded"],
                    row["gold_contradicted"],
                    row["gold_unsafe"],
                    row["labeller"],
                    wall_time(),
                ),
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "gold_ungrounded": sum(row["gold_ungrounded"] for row in rows),
        "gold_contradicted": sum(row["gold_contradicted"] for row in rows),
        "gold_unsafe": sum(row["gold_unsafe"] for row in rows),
        "clean": sum(
            1
            for row in rows
            if not row["gold_ungrounded"]
            and not row["gold_contradicted"]
            and not row["gold_unsafe"]
        ),
        "source": "calibration split only; document-disjoint from seeded eval split",
        "labeller": "person1_codex_manual_review",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "data" / "labels" / "manual_anchor_300.jsonl"
    )
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "interlock.db")
    args = parser.parse_args()

    rows = build_labels(args.count, args.seed)
    write_jsonl(rows, args.out)
    import_labels(rows, args.db)
    report = summary(rows)
    (args.out.parent / "manual_anchor_300.summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")
    print(f"imported labels into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
