"""Build JSON and Markdown evidence from a completed OpenRouter anchor run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.eval.anchor_report import (  # noqa: E402
    build_anchor_report,
    render_anchor_markdown,
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"expected an object at {path}:{line_number}")
        rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels", type=Path, default=REPO_ROOT / "data/labels/manual_anchor_300.jsonl"
    )
    parser.add_argument(
        "--judgments",
        type=Path,
        default=REPO_ROOT / "artifacts/eval/manual_anchor_judgments_openai-gpt-4o-mini.jsonl",
    )
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "artifacts/eval/manual_anchor_report.json"
    )
    parser.add_argument(
        "--markdown", type=Path, default=REPO_ROOT / "artifacts/eval/manual_anchor_report.md"
    )
    args = parser.parse_args()

    try:
        report = build_anchor_report(
            _jsonl(args.labels),
            _jsonl(args.judgments),
            model=args.model,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_anchor_markdown(report), encoding="utf-8")
    print(f"wrote {args.json}")
    print(f"wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
