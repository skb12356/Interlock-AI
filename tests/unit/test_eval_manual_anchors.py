from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "eval_manual_anchors.py"
SPEC = importlib.util.spec_from_file_location("eval_manual_anchors", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_expected_reads_reviewed_labels_from_anchor_row() -> None:
    item = {
        "gold_ungrounded": 1,
        "gold_contradicted": 0,
        "gold_unsafe": 0,
        "payload": {"gold_ungrounded": 0},
    }

    assert MODULE.expected(item) == "ungrounded"


def test_summary_reports_invalid_rows_against_full_denominator() -> None:
    rows = [
        {
            "model": "small",
            "gold": "clean",
            "judge_label": "clean",
            "valid": True,
            "agreement": True,
        },
        {
            "model": "small",
            "gold": "ungrounded",
            "judge_label": None,
            "valid": False,
            "agreement": False,
        },
    ]

    summary = MODULE.summarize(rows)["models"]["small"]
    assert summary["valid_rate"] == 0.5
    assert summary["agreement_rate"] == 0.5
    assert summary["confusion"] == {"clean->clean": 1}
