"""Build the evidence pack used beside the pitch scorecard.

The output is derived from committed evaluation JSON and the versioned policy. It
records targets as targets, never as measured results, and keeps failed metrics visible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from interlock.core.policy import load_policy

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return dict(value)


def metric_table(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [item["name"] for item in reports[0]["metrics"]]
    output: list[dict[str, Any]] = []
    for name in names:
        values = []
        for report in reports:
            metric = next(item for item in report["metrics"] if item["name"] == name)
            values.append(
                {
                    "seed": report["seed"],
                    "value": metric["value"],
                    "unit": metric["unit"],
                    "ci": metric.get("ci"),
                    "met": metric.get("met"),
                }
            )
        source = next(item for item in reports[0]["metrics"] if item["name"] == name)
        output.append({"name": name, "target": source["target"], "measurements": values})
    return output


def stakes_table(policy_path: Path) -> list[dict[str, Any]]:
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    domains = policy["stakes"]["domains"]
    selected = ["branch_info", "fees", "prepayment"]
    multipliers = policy["stakes"]["multipliers"]["reversibility"]
    return [
        {
            "domain": domain,
            "impact_inr": values["impact_inr"],
            "reversibility": values["reversibility"],
            "reversibility_multiplier": multipliers[values["reversibility"]],
        }
        for domain in selected
        for values in [domains[domain]]
    ]


MECHANISMS = [
    {"mechanism": "stakes-aware routing", "evidence": "interlock/signals/stakes.py"},
    {"mechanism": "calibrated multi-defect risk", "evidence": "artifacts/calibration/report.json"},
    {"mechanism": "conformal escape bound", "evidence": "artifacts/calibration/lambda.json"},
    {
        "mechanism": "durable response holds",
        "evidence": "tests/contract/test_tool_interlock_stream.py",
    },
    {"mechanism": "cost regret and rework ledger", "evidence": "interlock/ledger/writer.py"},
    {"mechanism": "live ConsoleHub trail", "evidence": "interlock/gateway/console_ws.py"},
]


def build(output: Path) -> dict[str, Any]:
    reports = [
        load_json(path)
        for path in sorted((REPO_ROOT / "artifacts/eval").glob("report-seed-*.json"))
    ]
    if not reports:
        raise ValueError("no regenerated seed reports found")
    report_policy = load_json(REPO_ROOT / "artifacts/eval/report.json")
    current_policy = load_policy(REPO_ROOT / "policies/banking.yaml")
    if report_policy.get("policy_version") != current_policy.policy_version:
        raise ValueError(
            "evaluation report policy does not match policies/banking.yaml: "
            f"{report_policy.get('policy_version')} != {current_policy.policy_version}"
        )
    payload = {
        "policy_version": current_policy.policy_version,
        "metrics": metric_table(reports),
        "mechanisms": MECHANISMS,
        "stakes": stakes_table(REPO_ROOT / "policies/banking.yaml"),
        "sources": {
            "design_targets": "TODO.md and Implementation/Implementation02.md",
            "limitations": "docs/LIMITATIONS.md",
            "seed_reports": [
                str(path.relative_to(REPO_ROOT))
                for path in sorted((REPO_ROOT / "artifacts/eval").glob("report-seed-*.json"))
            ],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "artifacts/eval/pitch_pack.json")
    args = parser.parse_args()
    payload = build(args.output)
    print(
        json.dumps(
            {
                "policy_version": payload["policy_version"],
                "metrics": len(payload["metrics"]),
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
