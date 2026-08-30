"""Assemble the final failure-preserving submission evidence report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.eval.product_report import (  # noqa: E402
    build_product_report,
    render_product_markdown,
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _optional(path: Path) -> dict[str, Any] | None:
    return _json(path) if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "artifacts/eval/product_report.json"
    )
    parser.add_argument(
        "--markdown", type=Path, default=REPO_ROOT / "artifacts/eval/product_report.md"
    )
    args = parser.parse_args()
    try:
        seeded = [
            _json(path)
            for path in sorted((REPO_ROOT / "artifacts/eval").glob("report-seed-*.json"))
        ]
        report = build_product_report(
            _json(REPO_ROOT / "artifacts/eval/manual_anchor_report.json"),
            seeded,
            _json(REPO_ROOT / "artifacts/eval/policy_comparison.json"),
            {
                "calibration": _optional(REPO_ROOT / "artifacts/calibration/report.json"),
                "conformal": _optional(REPO_ROOT / "artifacts/calibration/lambda.json"),
                "load": _optional(REPO_ROOT / "artifacts/load/load_pass.json"),
                "fairness": _optional(REPO_ROOT / "artifacts/eval/fairness_run.json"),
                "security": _optional(REPO_ROOT / "artifacts/security/security_sweep.json"),
                "economics": None,
                "penetration_test": None,
            },
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_product_markdown(report), encoding="utf-8")
    print(f"wrote {args.json}")
    print(f"wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
