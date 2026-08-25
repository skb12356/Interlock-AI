"""Record real upstream SSE responses as test fixtures.

The plan (D1-A1) calls for 12 recorded streams. They matter more than they look: every
later stream test — segmentation, the commit gate, the property test, repair — runs
against **real provider output** rather than against a hand-written idealisation of it.
Hand-written fixtures agree with your assumptions, which is exactly why they miss the
bug that stops the demo.

    uv run python scripts/record_streams.py

Each fixture is JSONL: one JSON object per raw SSE line, preserving the exact text the
provider sent so byte-for-byte passthrough can be asserted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "streams"

# Banking-domain prompts, chosen to exercise the segmentation edge cases the gate will
# meet on stage: currency amounts, clause numbers, abbreviations, lists, long sentences.
PROMPTS: list[tuple[str, str]] = [
    (
        "prepayment_penalty",
        "Does prepaying my home loan attract a penalty? Answer in two sentences.",
    ),
    (
        "clause_reference",
        "Explain what Clause 7.4 of a loan agreement typically covers. Two sentences.",
    ),
    (
        "currency_amount",
        "A customer prepays Rs. 40,000 on a loan. Explain the fee in one sentence.",
    ),
    ("branch_hours", "What time do bank branches usually open? One short sentence."),
    ("abbreviation", "Explain what e.g. EMI and i.e. principal mean, in two sentences."),
    ("numbered_list", "List three loan document requirements as 1. 2. 3."),
    ("honorific", "Write one sentence addressed to Dr. Rao about his account balance."),
    ("multi_sentence", "Describe the home loan prepayment process in four short sentences."),
    (
        "decimal_numbers",
        "Explain an interest rate of 8.75 percent versus 9.25 percent in two sentences.",
    ),
    ("refusal", "What is my current account balance?"),
    ("short_answer", "Say only: Yes."),
    ("markdown_code", "Show a one-line Python snippet that computes EMI, with a code fence."),
]


def record(
    name: str,
    prompt: str,
    *,
    base_url: str,
    model: str,
    max_tokens: int,
    timeout: float,
) -> dict[str, object]:
    """Record one stream, returning a small summary for the console."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise bank support assistant. /no_think"},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0.0,  # deterministic, so a re-record is a meaningful diff
    }

    lines: list[dict[str, object]] = []
    started = time.monotonic()
    ttft_ms: float | None = None

    with (
        httpx.Client(timeout=timeout) as client,
        client.stream("POST", f"{base_url.rstrip('/')}/chat/completions", json=body) as response,
    ):
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[len("data:") :].lstrip(" ")
            if ttft_ms is None:
                ttft_ms = (time.monotonic() - started) * 1000.0
            lines.append({"raw": raw, "at_ms": round((time.monotonic() - started) * 1000.0, 2)})

    path = FIXTURE_DIR / f"{name}.jsonl"
    meta = {
        "_meta": {
            "name": name,
            "prompt": prompt,
            "model": model,
            "provider": "ollama",
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ttft_ms": round(ttft_ms or 0.0, 2),
            "total_ms": round((time.monotonic() - started) * 1000.0, 2),
            "line_count": len(lines),
        }
    }
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for entry in lines:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    text = _assemble(lines)
    return {"name": name, "lines": len(lines), "chars": len(text), "ttft_ms": ttft_ms}


def _assemble(lines: list[dict[str, object]]) -> str:
    out = []
    for entry in lines:
        raw = str(entry["raw"])
        if raw == "[DONE]":
            continue
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            out.append(choice.get("delta", {}).get("content") or "")
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--only", default=None, help="record a single fixture by name")
    args = parser.parse_args()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    selected = [p for p in PROMPTS if args.only is None or p[0] == args.only]

    failures = 0
    for name, prompt in selected:
        try:
            summary = record(
                name,
                prompt,
                base_url=args.base_url,
                model=args.model,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
        except Exception as exc:
            failures += 1
            print(f"  FAILED {name}: {exc!r}", file=sys.stderr)
            continue
        print(
            f"  {summary['name']:<22} {summary['lines']:>4} lines "
            f"{summary['chars']:>5} chars  ttft={summary['ttft_ms']:.0f}ms"
        )

    print(f"\n{len(selected) - failures}/{len(selected)} fixtures written to {FIXTURE_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
