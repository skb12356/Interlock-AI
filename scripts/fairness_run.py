"""Run the seeded demographic-twin fairness check and write a chart-ready report.

This is Lane C by design: twin requests are generated offline and never sent to
customers. The report is suitable for the console and can be imported into the live
ledger with ``scripts/import_fairness_pairs.py`` only when request ids are real.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.policy import load_policy  # noqa: E402
from interlock.eval.harness import run_eval  # noqa: E402
from interlock.eval.seeded import build_seeded_set  # noqa: E402
from interlock.interlock_tools.holds import ToolInterlock  # noqa: E402
from interlock.lanec.evalues import EValueMonitor  # noqa: E402
from interlock.ledger.writer import Ledger  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.risk.calibration import MultiDefectCalibrator  # noqa: E402
from interlock.risk.engine import RealRiskEngine, load_conformal  # noqa: E402
from interlock.signals.canary import CanaryDetector, CanaryRegistry  # noqa: E402


async def run(seed: int, output: Path) -> dict[str, Any]:
    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    registry = CanaryRegistry()
    cases = build_seeded_set(corpus_chunks(documents), canary=registry.mint("fairness"), seed=seed)
    engine = RealRiskEngine(
        policy=policy,
        calibrator=MultiDefectCalibrator.load(
            REPO_ROOT / "artifacts" / "calibration" / "calibrator_per_defect.json"
        ),
        conformal=load_conformal(REPO_ROOT / "artifacts" / "calibration" / "lambda.json"),
        canary_detector=CanaryDetector(registry=registry),
        calib_version="fairness-run",
    )
    ledger = Ledger(output.with_suffix(".db"))
    await ledger.start()
    try:
        _, on, _ = await run_eval(
            cases=cases,
            engine=engine,
            policy=policy,
            tool_interlock=ToolInterlock(policy=policy, ledger=ledger),
        )
    finally:
        await ledger.stop()

    outcomes = {item.case_id: item for item in on.outcomes}
    pairs: list[dict[str, Any]] = []
    monitor = EValueMonitor()
    for case in cases:
        if not case.twin_id or not case.case_id.endswith("a"):
            continue
        twin_id = f"{case.case_id[:-1]}b"
        left, right = outcomes[case.case_id], outcomes[twin_id]
        disparate = left.action != right.action
        monitor.update(float(disparate))
        pairs.append(
            {
                "pair_id": case.twin_id,
                "axis": "demographic_marker",
                "base_request_id": f"eval_{case.case_id}",
                "twin_request_id": f"eval_{twin_id}",
                "attribute": case.note,
                "decision_field": "action",
                "base_value": left.action,
                "twin_value": right.action,
                "delta": 1.0 if disparate else 0.0,
                "disparate": disparate,
            }
        )
    payload = {
        "seed": seed,
        "policy_version": policy.policy_version,
        "offline": True,
        "n_pairs": len(pairs),
        "pairs": pairs,
        "e_value": monitor.report(),
        "series": monitor.chart_series(),
        "notes": ["Generated offline; no customer traffic was twinned or double-billed."],
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(payload, indent=2), encoding="utf-8")
    chart = "".join(
        f"<tr><td>{item['pair_id']}</td><td>{'disparate' if item['disparate'] else 'alike'}</td>"
        f"<td>{html.escape(item['base_value'])}</td><td>{html.escape(item['twin_value'])}</td></tr>"
        for item in pairs
    )
    chart_path = output.with_suffix(".html")
    chart_markup = (
        "<!doctype html><meta charset='utf-8'><title>Lane C fairness e-value</title>"
        "<h1>Lane C fairness e-value</h1>"
        f"<p>Offline demographic twins: {len(pairs)} pairs. E-value: "
        f"{monitor.e_value:.4f}; alert threshold: {monitor.threshold:.1f}</p>"
        "<table><thead><tr><th>Pair</th><th>Result</th><th>Base</th><th>Twin</th></tr></thead>"
        f"<tbody>{chart}</tbody></table>"
    )
    await asyncio.to_thread(chart_path.write_text, chart_markup, encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "artifacts" / "eval" / "fairness_run.json"
    )
    args = parser.parse_args()
    payload = asyncio.run(run(args.seed, args.json))
    print(json.dumps({"n_pairs": payload["n_pairs"], "json": str(args.json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
