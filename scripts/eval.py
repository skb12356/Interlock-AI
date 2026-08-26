"""`make eval` — run the seeded set off vs on and print all six metrics.

    uv run python scripts/eval.py
    uv run python scripts/eval.py --conformal-filter   # guaranteed mode
    uv run python scripts/eval.py --json artifacts/eval/report.json

Prints six numbers **even when they are bad**. That is the Day-3 exit criterion and it
is the point: a harness that only runs once everything passes is a harness that never
found anything. Exit code is 0 when the run completes, regardless of whether the targets
were met — the numbers are the output, not the verdict.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.policy import load_policy  # noqa: E402
from interlock.eval.harness import run_eval  # noqa: E402
from interlock.eval.seeded import CASE_COUNTS, build_seeded_set  # noqa: E402
from interlock.interlock_tools.holds import ToolInterlock  # noqa: E402
from interlock.ledger.writer import Ledger  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.risk.calibration import MultiDefectCalibrator  # noqa: E402
from interlock.risk.engine import RealRiskEngine, load_conformal  # noqa: E402
from interlock.signals.canary import CanaryDetector, CanaryRegistry  # noqa: E402

CALIBRATION_DIR = REPO_ROOT / "artifacts" / "calibration"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument(
        "--conformal-filter",
        action="store_true",
        help="guaranteed mode: strike L0_pass above the certified threshold",
    )
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "artifacts" / "eval" / "report.json"
    )
    parser.add_argument("--show-failures", type=int, default=8)
    args = parser.parse_args()

    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    chunks = corpus_chunks(documents)

    registry = CanaryRegistry()
    canary = registry.mint("eval")

    cases = build_seeded_set(chunks, canary=canary, seed=args.seed)
    print(f"seeded set: {len(cases)} conversations")
    counts = Counter(case.category for case in cases)
    print("  " + "  ".join(f"{name}={counts[name]}" for name in CASE_COUNTS))
    defective = sum(1 for case in cases if case.is_defective)
    print(f"  {defective} defective, {len(cases) - defective} clean\n")

    calibrator_path = CALIBRATION_DIR / "calibrator_per_defect.json"
    if not calibrator_path.exists():
        print(
            "  ! no calibrator -- run scripts/calibrate.py first; every decision "
            "would be degraded and the numbers meaningless"
        )
        return 1
    calibrator = MultiDefectCalibrator.load(calibrator_path)
    conformal = load_conformal(CALIBRATION_DIR / "lambda.json")

    engine = RealRiskEngine(
        policy=policy,
        calibrator=calibrator,
        conformal=conformal,
        canary_detector=CanaryDetector(registry=registry),
        conformal_filter=args.conformal_filter,
        calib_version="eval",
    )

    # The interlock needs a ledger for durable holds. The eval writes to a scratch file
    # rather than the real one: a measurement run must not leave review cards behind
    # that an operator would later find and wonder about.
    ledger = Ledger(db_path=REPO_ROOT / "artifacts" / "eval" / "eval.db")
    await ledger.start()
    try:
        interlock = ToolInterlock(policy=policy, ledger=ledger)
        mode = "GUARANTEED (conformal filter ON)" if args.conformal_filter else "operating"
        print(f"running both arms -- {mode} mode\n")
        _off, on, metrics = await run_eval(
            cases=cases, engine=engine, policy=policy, tool_interlock=interlock
        )
    finally:
        await ledger.stop()

    print(metrics.render())

    print("\n  actions chosen, with Interlock on:")
    for action, count in sorted(Counter(o.action for o in on.outcomes).items()):
        print(f"    {action:14} {count:4}")

    print("\n  per-category catch rate:")
    by_id = {case.case_id: case for case in cases}
    for category in CASE_COUNTS:
        outcomes = [o for o in on.outcomes if o.category == category]
        if not outcomes or category in {"clean", "demographic_twin"}:
            continue
        caught = sum(1 for o in outcomes if o.caught_pre_action)
        flag = "" if caught == len(outcomes) else "   <-- misses here"
        print(f"    {category:22} {caught:3}/{len(outcomes):<3}{flag}")

    misses = [o for o in on.outcomes if by_id[o.case_id].is_defective and not o.caught_pre_action]
    if misses and args.show_failures:
        print(f"\n  what got through ({len(misses)} total, showing {args.show_failures}):")
        for outcome in misses[: args.show_failures]:
            case = by_id[outcome.case_id]
            print(f"    {outcome.case_id:26} {outcome.action:12} {case.note[:70]}")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(
            {
                "seed": args.seed,
                "conformal_filter": args.conformal_filter,
                "n_cases": len(cases),
                "n_defective": defective,
                "policy_version": policy.policy_version,
                "metrics": metrics.to_dict(),
                "actions": dict(Counter(o.action for o in on.outcomes)),
                "misses": [
                    {"case_id": o.case_id, "category": o.category, "action": o.action}
                    for o in misses
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  wrote {args.json}")

    # Always 0. The numbers are the output; a red build on a missed target would make
    # the honest move -- committing a bad measurement -- impossible.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
