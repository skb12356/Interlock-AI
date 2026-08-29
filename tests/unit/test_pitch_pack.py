import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "build_pitch_pack.py"
SPEC = importlib.util.spec_from_file_location("build_pitch_pack", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_pitch_pack_uses_current_policy_and_seed_evidence(tmp_path: Path) -> None:
    output = tmp_path / "pitch_pack.json"
    payload = MODULE.build(output)
    stored = json.loads(output.read_text(encoding="utf-8"))

    assert stored == payload
    assert payload["policy_version"].startswith("banking-v3@sha256:")
    assert len(payload["metrics"]) >= 6
    assert len(payload["stakes"]) == 3
    assert {item["seed"] for item in payload["metrics"][0]["measurements"]} == {
        20260826,
        20260827,
        20260828,
    }
