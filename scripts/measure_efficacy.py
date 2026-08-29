"""Compute action efficacy from human-reviewed forced-action outcomes.

Each JSONL row must be an observed post-action outcome::

    {"item_id":"manual-anchor-001", "action":"L2_repair",
     "defect":"ungrounded", "removed":true}

Pre-action labels are deliberately not accepted as efficacy evidence. The command
produces a JSON report and a YAML-shaped policy patch; applying that patch remains an
explicit review step.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.eval.metrics import wilson_interval  # noqa: E402

ACTIONS = {"L0_pass", "L1_annotate", "L2_repair", "L3_reroute", "L4_hold", "L5_block"}
DEFECTS = {
    "ungrounded",
    "contradicted",
    "overconfident",
    "unsafe_action",
    "pii_leak",
    "canary_leak",
    "biased",
}


def label_ids(path: Path) -> set[str]:
    return {
        str(json.loads(line)["item_id"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def read_outcomes(path: Path, allowed_ids: set[str]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    outcomes: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        required = {"item_id", "action", "defect", "removed"}
        if not isinstance(item, dict) or not required <= item.keys():
            raise ValueError(f"line {number}: required fields are {sorted(required)}")
        item_id, action, defect = str(item["item_id"]), str(item["action"]), str(item["defect"])
        if item_id not in allowed_ids:
            raise ValueError(f"line {number}: item_id is outside the manual anchor set: {item_id}")
        if action not in ACTIONS or defect not in DEFECTS:
            raise ValueError(f"line {number}: unknown action or defect")
        key = (item_id, action, defect)
        if key in seen:
            raise ValueError(f"line {number}: duplicate forced outcome {key}")
        seen.add(key)
        if not isinstance(item["removed"], bool):
            raise ValueError(f"line {number}: removed must be a JSON boolean")
        outcomes.append(
            {"item_id": item_id, "action": action, "defect": defect, "removed": item["removed"]}
        )
    return outcomes


def measure(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for item in outcomes:
        cells[(item["action"], item["defect"])].append(item["removed"])
    report: dict[str, Any] = {"n_outcomes": len(outcomes), "cells": {}, "policy_patch": {}}
    for (action, defect), values in sorted(cells.items()):
        successes = sum(values)
        lo, hi = wilson_interval(successes, len(values))
        value = successes / len(values)
        entry = {
            "value": round(value, 6),
            "source": "measured",
            "lo": round(lo, 6),
            "hi": round(hi, 6),
            "n": len(values),
        }
        report["cells"][f"{action}/{defect}"] = {
            "successes": successes,
            "trials": len(values),
            **entry,
        }
        report["policy_patch"].setdefault(action, {})[defect] = entry
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outcomes", type=Path, help="JSONL of post-action reviewed outcomes")
    parser.add_argument(
        "--labels", type=Path, default=REPO_ROOT / "data/labels/manual_anchor_300.jsonl"
    )
    parser.add_argument("--json", type=Path, default=REPO_ROOT / "artifacts/eval/efficacy.json")
    args = parser.parse_args()
    outcomes = read_outcomes(args.outcomes, label_ids(args.labels))
    report = measure(outcomes)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "n_outcomes": len(outcomes),
                "measured_cells": len(report["cells"]),
                "json": str(args.json),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
