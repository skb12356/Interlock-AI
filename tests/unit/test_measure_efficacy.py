import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "measure_efficacy.py"
SPEC = importlib.util.spec_from_file_location("measure_efficacy", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_measure_reports_observed_cells_with_wilson_interval() -> None:
    report = MODULE.measure(
        [
            {"item_id": "a", "action": "L2_repair", "defect": "ungrounded", "removed": True},
            {"item_id": "b", "action": "L2_repair", "defect": "ungrounded", "removed": False},
        ]
    )

    cell = report["cells"]["L2_repair/ungrounded"]
    assert cell["successes"] == 1
    assert cell["trials"] == 2
    assert cell["source"] == "measured"
    assert cell["lo"] < cell["value"] < cell["hi"]


def test_read_outcomes_rejects_unknown_ids_and_duplicate_rows(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.jsonl"
    row = {"item_id": "known", "action": "L2_repair", "defect": "ungrounded", "removed": True}
    write_jsonl(path, [row])
    with pytest.raises(ValueError, match="outside the manual anchor set"):
        MODULE.read_outcomes(path, {"other"})

    write_jsonl(path, [row, row])
    with pytest.raises(ValueError, match="duplicate forced outcome"):
        MODULE.read_outcomes(path, {"known"})


def test_read_outcomes_requires_boolean_removed(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.jsonl"
    write_jsonl(
        path,
        [{"item_id": "known", "action": "L2_repair", "defect": "ungrounded", "removed": 1}],
    )

    with pytest.raises(ValueError, match="removed must be a JSON boolean"):
        MODULE.read_outcomes(path, {"known"})
