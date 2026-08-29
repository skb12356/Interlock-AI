"""Run the gateway load pass and persist measured latency evidence.

The gateway must already be running. The script uses only the OpenAI-compatible stream
endpoint and the read-only latency admin endpoint; it never writes to the ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]


async def one(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore, index: int
) -> dict[str, Any]:
    body = {
        "model": "qwen3:4b",
        "messages": [{"role": "user", "content": f"What time does the branch open? run {index}"}],
        "stream": True,
    }
    async with semaphore:
        started = time.perf_counter()
        try:
            response = await client.post(url, json=body, timeout=60.0)
            # Consume the stream so connection reuse and server-side completion are real.
            await response.aread()
            return {
                "ok": response.is_success,
                "status": response.status_code,
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            }
        except (httpx.HTTPError, TimeoutError) as exc:
            return {
                "ok": False,
                "status": None,
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                "error": type(exc).__name__,
            }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return sorted(values)[min(len(values) - 1, round(fraction * (len(values) - 1)))]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(
        max_connections=args.concurrency * 2,
        max_keepalive_connections=args.concurrency,
    )
    async with httpx.AsyncClient(limits=limits) as client:
        url = f"{args.gateway.rstrip('/')}/v1/chat/completions"
        if args.duration_seconds > 0:
            deadline = time.perf_counter() + args.duration_seconds

            async def worker(worker_id: int) -> list[dict[str, Any]]:
                output: list[dict[str, Any]] = []
                sequence = worker_id
                while time.perf_counter() < deadline:
                    output.append(await one(client, url, semaphore, sequence))
                    sequence += args.concurrency
                return output

            batches = await asyncio.gather(*(worker(i) for i in range(args.concurrency)))
            results = [item for batch in batches for item in batch]
        else:
            total = max(args.requests, args.concurrency)
            results = await asyncio.gather(
                *(one(client, url, semaphore, i) for i in range(total))
            )
        health = await client.get(f"{args.gateway.rstrip('/')}/admin/latency", timeout=10.0)
    elapsed = [float(item["elapsed_ms"]) for item in results]
    successes = sum(bool(item["ok"]) for item in results)
    return {
        "gateway": args.gateway,
        "requests": len(results),
        "concurrency": args.concurrency,
        "successes": successes,
        "failures": len(results) - successes,
        "client_elapsed_ms": {
            "p50": round(percentile(elapsed, 0.50), 2),
            "p95": round(percentile(elapsed, 0.95), 2),
            "max": round(max(elapsed) if elapsed else 0.0, 2),
            "mean": round(statistics.fmean(elapsed) if elapsed else 0.0, 2),
        },
        "gateway_latency_report": health.json(),
        "captured_ts": time.time(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--json", type=Path, default=REPO_ROOT / "artifacts" / "load" / "load_pass.json")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.duration_seconds < 0:
        parser.error("--requests and --concurrency must be positive; duration cannot be negative")
    payload = asyncio.run(run(args))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
