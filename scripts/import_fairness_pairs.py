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
    text = path.read_text(encoding="utf-8")
    records: list[Any]
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        records = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    else:
        if isinstance(document, dict) and isinstance(document.get("pairs"), list):
            records = document["pairs"]
        elif isinstance(document, dict):
            records = [document]
        else:
            raise ValueError("input JSON must be a pair object or an object containing pairs")

    output: list[dict[str, Any]] = []
    for number, item in enumerate(records, 1):
        if not isinstance(item, dict) or not item.keys() >= REQUIRED:
            missing = sorted(REQUIRED - set(item) if isinstance(item, dict) else REQUIRED)
            raise ValueError(f"record {number}: missing fields: {', '.join(missing)}")
        if any(not str(item[key]).strip() for key in REQUIRED - {"delta"}):
            raise ValueError(f"record {number}: required string fields cannot be empty")
        try:
            item["delta"] = float(item["delta"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"record {number}: delta must be numeric") from exc
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
            # Fairness reports may carry analysis-only fields such as ``disparate``
            # and ``axis``. Persist only the frozen ledger contract.
            await ledger.persist_fairness_pair(
                **{key: item[key] for key in REQUIRED}
            )
    finally:
        await ledger.stop()
    print(json.dumps({"imported": len(observations), "db": str(args.db)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
