"""Run the seeded evaluation across multiple seeds and write a compact HTML scorecard."""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.core.policy import load_policy  # noqa: E402
from interlock.eval.harness import run_eval  # noqa: E402
from interlock.eval.seeded import build_seeded_set  # noqa: E402
from interlock.interlock_tools.holds import ToolInterlock  # noqa: E402
from interlock.ledger.writer import Ledger  # noqa: E402
from interlock.retrieval import corpus_chunks, load_corpus  # noqa: E402
from interlock.risk.calibration import MultiDefectCalibrator  # noqa: E402
from interlock.risk.engine import RealRiskEngine, load_conformal  # noqa: E402
from interlock.signals.canary import CanaryDetector, CanaryRegistry  # noqa: E402


async def run(seed: int, output: Path) -> dict:
    policy = load_policy(REPO_ROOT / "policies" / "banking.yaml")
    documents = load_corpus(REPO_ROOT / "corpus" / "manifest.json", root=REPO_ROOT)
    registry = CanaryRegistry()
    canary = registry.mint("eval")
    cases = build_seeded_set(corpus_chunks(documents), canary=canary, seed=seed)
    engine = RealRiskEngine(
        policy=policy,
        calibrator=MultiDefectCalibrator.load(
            REPO_ROOT / "artifacts" / "calibration" / "calibrator_per_defect.json"
        ),
        conformal=load_conformal(REPO_ROOT / "artifacts" / "calibration" / "lambda.json"),
        canary_detector=CanaryDetector(registry=registry),
        calib_version="eval-matrix",
    )
    ledger = Ledger(db_path=output.with_suffix(".db"))
    await ledger.start()
    try:
        _, _, metrics = await run_eval(
            cases=cases,
            engine=engine,
            policy=policy,
            tool_interlock=ToolInterlock(policy=policy, ledger=ledger),
        )
    finally:
        await ledger.stop()
    payload = {
        "seed": seed,
        "policy_version": policy.policy_version,
        "n_cases": len(cases),
        "metrics": [asdict(metric) for metric in metrics.metrics],
        "notes": metrics.notes,
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def render(reports: list[dict], destination: Path) -> None:
    names = [metric["name"] for metric in reports[0]["metrics"]]
    rows = []
    for name in names:
        cells = []
        for report in reports:
            metric = next(item for item in report["metrics"] if item["name"] == name)
            value = metric["value"]
            if metric["unit"] == "%":
                body = f"{value * 100:.2f}%"
                if metric.get("ci"):
                    body += f" [{metric['ci'][0] * 100:.1f}, {metric['ci'][1] * 100:.1f}]"
            else:
                body = f"{value:.2f} {metric['unit']}"
            flag = "PASS" if metric.get("met") is True else "MISS" if metric.get("met") is False else ""
            cells.append(f"<td>{html.escape(body)} {flag}</td>")
        rows.append(f"<tr><th>{html.escape(name)}</th>{''.join(cells)}</tr>")
    policy = html.escape(reports[0]["policy_version"])
    page = f"""<!doctype html>
<meta charset='utf-8'><title>Interlock evaluation matrix</title>
<style>body{{font:15px system-ui;margin:2rem;color:#17202a}}table{{border-collapse:collapse}}th,td{{border:1px solid #ccd3da;padding:.55rem;text-align:left}}th{{background:#eef2f5}}small{{color:#58636e}}</style>
<h1>Interlock seeded evaluation</h1><p>Policy: <code>{policy}</code></p>
<table><thead><tr><th>Metric</th>{''.join(f'<th>Seed {r["seed"]}</th>' for r in reports)}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p><small>Rate intervals are 95% Wilson intervals. Model spend and latency remain modelled/measured as described in LIMITATIONS.md.</small></p>
"""
    destination.write_text(page, encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", nargs="+", type=int, default=[20260826, 20260827, 20260828])
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "artifacts" / "eval")
    args = parser.parse_args()
    reports = [
        await run(seed, args.output / f"report-seed-{seed}.json") for seed in args.seeds
    ]
    render(reports, args.output / "report.html")
    print(f"wrote {args.output / 'report.html'}")


if __name__ == "__main__":
    asyncio.run(main())
