import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "import_fairness_pairs.py"
SPEC = importlib.util.spec_from_file_location("import_fairness_pairs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


ROW = {
    "pair_id": "pair-1",
    "base_request_id": "base-1",
    "twin_request_id": "twin-1",
    "attribute": "gender",
    "decision_field": "action",
    "base_value": "L0_pass",
    "twin_value": "L4_hold",
    "delta": 1.0,
}


def test_importer_accepts_fairness_run_object_and_jsonl(tmp_path: Path) -> None:
    artifact = tmp_path / "fairness.json"
    artifact.write_text(json.dumps({"offline": True, "pairs": [ROW]}), encoding="utf-8")
    jsonl = tmp_path / "fairness.jsonl"
    jsonl.write_text(json.dumps(ROW) + "\n", encoding="utf-8")

    assert MODULE.read_rows(artifact) == [ROW]
    assert MODULE.read_rows(jsonl) == [ROW]
