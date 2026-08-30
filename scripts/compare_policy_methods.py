"""Compare false-intervention policy methods over three immutable seeded traces."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.policy import load_policy  # noqa: E402
from interlock.core.types import Decision, RiskContext  # noqa: E402
from interlock.eval.harness import run_eval  # noqa: E402
from interlock.eval.policy_experiment import (  # noqa: E402
    BaselineTrace,
    candidate_matrix,
    comparison_payload,
    reference_action_regressions,
    render_comparison_markdown,
    replay_seed_candidate,
    select_candidate,
)
from interlock.eval.seeded import build_seeded_set  # noqa: E402
from interlock.interlock_tools.holds import ToolInterlock  # noqa: E402
from interlock.ledger.writer import Ledger  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.risk.calibration import MultiDefectCalibrator  # noqa: E402
from interlock.risk.engine import RealRiskEngine, load_conformal  # noqa: E402
from interlock.signals.canary import CanaryDetector, CanaryRegistry  # noqa: E402

SEEDS: tuple[int, ...] = (20260826, 20260827, 20260828)
CALIBRATION_DIR = REPO_ROOT / "artifacts" / "calibration"


class RecordingEngine:
    """Record the one real detector pass that every policy candidate reuses."""

    def __init__(self, inner: RealRiskEngine) -> None:
        self.inner = inner
        self.decisions: dict[str, Decision] = {}
        self.contexts: dict[str, RiskContext] = {}

    async def evaluate(self, ctx: RiskContext) -> Decision:
        decision = await self.inner.evaluate(ctx)
        self.contexts[ctx.request_id] = ctx
        self.decisions[ctx.request_id] = decision
        return decision

    async def prefetch(self, request_id: str, question: str, retrieved: list[Any]) -> None:
        await self.inner.prefetch(request_id, question, retrieved)

    def arm(self, request_id: str, header: str | None) -> None:
        self.inner.arm(request_id, header)

    def disarm(self, request_id: str) -> None:
        self.inner.disarm(request_id)

    def health(self) -> dict[str, object]:
        return self.inner.health()


async def build_trace(seed: int, *, ledger_path: Path) -> BaselineTrace:
    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    chunks = corpus_chunks(load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT))
    registry = CanaryRegistry()
    canary = registry.mint(f"policy-comparison-{seed}")
    cases = tuple(build_seeded_set(chunks, canary=canary, seed=seed))
    engine = RecordingEngine(
        RealRiskEngine(
            policy=policy,
            calibrator=MultiDefectCalibrator.load(CALIBRATION_DIR / "calibrator_per_defect.json"),
            conformal=load_conformal(CALIBRATION_DIR / "lambda.json"),
            canary_detector=CanaryDetector(registry=registry),
            conformal_filter=False,
            calib_version="policy-comparison",
        )
    )
    ledger = Ledger(db_path=ledger_path)
    await ledger.start()
    try:
        interlock = ToolInterlock(policy=policy, ledger=ledger)
        off, on, _metrics = await run_eval(
            cases=list(cases), engine=engine, policy=policy, tool_interlock=interlock
        )
    finally:
        await ledger.stop()
    if len(engine.decisions) != len(cases) or len(engine.contexts) != len(cases):
        raise RuntimeError("baseline trace did not record exactly one decision per case")
    return BaselineTrace(
        seed=seed,
        cases=cases,
        off=off,
        on=on,
        decisions=engine.decisions,
        contexts=engine.contexts,
    )


def baseline_escapes(trace: BaselineTrace) -> int:
    by_id = {case.case_id: case for case in trace.cases}
    return sum(
        outcome.escaped
        for outcome in trace.on.outcomes
        if by_id[outcome.case_id].expected_defect in {"ungrounded", "contradicted"}
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "artifacts" / "eval" / "policy_comparison.json"
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=REPO_ROOT / "artifacts" / "eval" / "policy_comparison.md",
    )
    args = parser.parse_args()

    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    with tempfile.TemporaryDirectory(prefix="interlock-policy-comparison-") as raw_tmp:
        tmp = Path(raw_tmp)
        traces = [await build_trace(seed, ledger_path=tmp / f"seed-{seed}.db") for seed in SEEDS]

    candidates = candidate_matrix()
    for index, candidate in enumerate(candidates, start=1):
        candidate.reference_action_regressions = reference_action_regressions(
            policy=policy,
            adjustment=candidate.adjustment,
        )
        candidate.seeds = tuple(
            replay_seed_candidate(
                trace,
                policy=policy,
                adjustment=candidate.adjustment,
            )
            for trace in traces
        )
        if index % 36 == 0:
            print(f"evaluated {index}/{len(candidates)} candidates")

    escape_baseline = {trace.seed: baseline_escapes(trace) for trace in traces}
    selected = select_candidate(candidates, baseline_escape_by_seed=escape_baseline)
    payload = comparison_payload(candidates, selected=selected)
    payload["policy_version"] = policy.policy_version
    payload["seeds"] = list(SEEDS)
    payload["baseline_escape_by_seed"] = escape_baseline
    payload["candidate_count"] = len(candidates)

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(render_comparison_markdown(payload), encoding="utf-8")

    print(json.dumps(payload["selected"], indent=2, sort_keys=True))
    print(f"wrote {args.json}")
    print(f"wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
