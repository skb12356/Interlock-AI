"""Import observed Lane C twin decisions into a ledger."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.ledger.writer import Ledger  # noqa: E402

REQUIRED = {
    "pair_id", "base_request_id", "twin_request_id", "attribute",
    "decision_field", "base_value", "twin_value", "delta",
}


def read_rows(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(item, dict) or not item.keys() >= REQUIRED:
            missing = sorted(REQUIRED - set(item) if isinstance(item, dict) else REQUIRED)
            raise ValueError(f"line {number}: missing fields: {', '.join(missing)}")
        if any(not str(item[key]).strip() for key in REQUIRED - {"delta"}):
            raise ValueError(f"line {number}: required string fields cannot be empty")
        item["delta"] = float(item["delta"])
        output.append(item)
    return output


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSONL exported from an offline twin run")
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "interlock.db")
    args = parser.parse_args()
    observations = read_rows(args.input)
    ledger = Ledger(args.db)
    await ledger.start()
    try:
        for item in observations:
            await ledger.persist_fairness_pair(**item)
    finally:
        await ledger.stop()
    print(json.dumps({"imported": len(observations), "db": str(args.db)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
