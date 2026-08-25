"""Measure what the ladder's actions actually cost in latency, on this box.

Term (4) of the expected-loss objective charges ``lambda_time x latency_ms[action]``.
Those numbers arrived from the plan as illustrations from datacenter-class hardware. If
they stay illustrations, the optimiser is doing exact arithmetic on invented inputs --
which is worse than no arithmetic, because it looks rigorous.

So: measure, write the result into the policy, and record which machine produced it.
A deployment on different hardware must re-run this; the policy comment says so.

    uv run python scripts/measure_action_latency.py
    uv run python scripts/measure_action_latency.py --runs 7 --json artifacts/latency.json

**Both actions are measured together, deliberately.** Measuring one and leaving the
other at its illustrative value makes the *ordering* between them arbitrary, and the
ordering is what the optimiser actually acts on. That is a worse failure than having
both numbers wrong in the same direction.

The first run of each action is discarded. A cold Ollama load was measured at 12 s
(qwen3:4b) and 21 s (qwen3:8b) on this machine, which measures how long the model took
to arrive rather than what the action costs -- and ``keep_alive`` now stops it happening
in production anyway (finding F-014).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.types import Decision, RepairHint, Stakes  # noqa: E402
from interlock.gate.repair import SentenceRepairer  # noqa: E402
from interlock.gateway.config import load_settings  # noqa: E402
from interlock.gateway.providers import build_providers  # noqa: E402
from interlock.retrieval import Retriever  # noqa: E402

QUESTION = "I took a home loan on a floating rate. If I prepay it early, what charge applies?"
BAD_SENTENCE = (
    "Prepayment of a floating-rate home loan attracts a foreclosure charge of 2% of "
    "the outstanding principal under Clause 7.4."
)
NEWLINE = "\n"


class _AlwaysPasses:
    """Verification is measured separately; here it must not add time."""

    async def evaluate(self, ctx: Any) -> Decision:
        return Decision(decision_id="dec_probe", action="L0_pass", loss_table=[], chosen_loss=0.0)


def _summarise(action: str, settings: Any, samples: list[float], runs: int) -> dict[str, Any]:
    return {
        "action": action,
        "model": settings.strong_tier.model,
        "provider": settings.strong_tier.provider,
        "runs": runs,
        "samples_ms": [round(sample, 1) for sample in samples],
        "median_ms": round(statistics.median(samples), 1),
        # Reported as the max, and named as the max. Calling the max of five samples a
        # p95 is how a results table acquires a number nobody can defend.
        "max_ms": round(max(samples), 1),
    }


async def measure_repair(runs: int) -> dict[str, Any]:
    """L2 rewrites ONE sentence, with the retrieved evidence in the prompt."""
    settings = load_settings()
    retriever = Retriever.open(settings.corpus_index_path, k=4)
    hits = retriever.search(QUESTION, k=4)
    evidence = retriever.evidence_for(QUESTION, k=3)
    retriever.close()

    samples: list[float] = []
    async with httpx.AsyncClient(timeout=300.0) as client:
        providers = build_providers(settings, client)
        repairer = SentenceRepairer(
            provider=providers[settings.strong_tier.provider],
            model=settings.strong_tier.model,
            risk_engine=_AlwaysPasses(),
            stakes=Stakes(
                impact_inr=40000, reversibility="costly", domain="prepayment", confidence=0.9
            ),
            request_id="req_latency_probe",
            question=QUESTION,
            retrieved=[hit.chunk.to_fragment() for hit in hits],
        )
        decision = Decision(
            decision_id="dec_probe",
            action="L2_repair",
            loss_table=[],
            chosen_loss=0.0,
            repair_hint=RepairHint(
                span=(0, len(BAD_SENTENCE)),
                unsupported_claim="a 2% foreclosure charge under Clause 7.4",
                evidence=evidence,
            ),
        )
        for index in range(runs + 1):
            result = await repairer.repair(BAD_SENTENCE, decision, answer_prefix="")
            label = "warm-up (discarded)" if index == 0 else f"run {index}"
            print(f"  {label:20} {result.latency_ms:9.0f} ms   verified={result.verified}")
            if index > 0:
                samples.append(result.latency_ms)

    return _summarise("L2_repair", settings, samples, runs)


async def measure_reroute(runs: int) -> dict[str, Any]:
    """L3 regenerates the WHOLE answer on the strong tier, with the evidence attached."""
    settings = load_settings()
    retriever = Retriever.open(settings.corpus_index_path, k=4)
    evidence = (NEWLINE * 2).join(retriever.evidence_for(QUESTION, k=3))
    retriever.close()

    user = f"Evidence:{NEWLINE}{evidence}{NEWLINE * 2}Question: {QUESTION}"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a bank support assistant. Answer using ONLY the evidence "
                "provided, and cite the clause you relied on. /no_think"
            ),
        },
        {"role": "user", "content": user},
    ]

    samples: list[float] = []
    async with httpx.AsyncClient(timeout=300.0) as client:
        provider = build_providers(settings, client)[settings.strong_tier.provider]
        loop = asyncio.get_running_loop()
        for index in range(runs + 1):
            started = loop.time()
            response = await provider.complete(
                {"model": settings.strong_tier.model, "messages": messages, "temperature": 0.0}
            )
            elapsed = (loop.time() - started) * 1000.0
            tokens = (response.get("usage") or {}).get("completion_tokens")
            label = "warm-up (discarded)" if index == 0 else f"run {index}"
            print(f"  {label:20} {elapsed:9.0f} ms   completion_tokens={tokens}")
            if index > 0:
                samples.append(elapsed)

    return _summarise("L3_reroute", settings, samples, runs)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--only", choices=["repair", "reroute"], default=None, help="measure just one action"
    )
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    if args.only != "reroute":
        print(f"L2_repair -- {args.runs} runs plus one discarded warm-up{NEWLINE}")
        results.append(await measure_repair(args.runs))
        print()
    if args.only != "repair":
        print(f"L3_reroute -- {args.runs} runs plus one discarded warm-up{NEWLINE}")
        results.append(await measure_reroute(args.runs))
        print()

    print("  policy latency_ms should read:")
    for result in results:
        print(
            f"    {result['action']:12} {result['median_ms']:9.0f}"
            f"   (max {result['max_ms']:.0f})"
        )
    if len(results) == 2 and results[1]["median_ms"] <= results[0]["median_ms"]:
        print(
            f"{NEWLINE}  WARNING: the reroute measured FASTER than the repair. That inverts"
            f"{NEWLINE}  the ladder's cost ordering and needs explaining before either number"
            f"{NEWLINE}  goes into the policy -- most likely the repair is paying for its"
            f"{NEWLINE}  verification pass, which L3 as measured here does not do."
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"{NEWLINE}  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
