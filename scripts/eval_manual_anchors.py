"""Run the offline grounding anchor through an explicitly approved OpenRouter judge.

This paid command never changes a shipped answer, policy threshold, or calibration fit.
Without ``--allow-external-context`` it prints a costed, content-free plan and makes no
network call. Results are resumable JSONL with exact dataset, prompt, and model identity.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from interlock.eval.judge_run import (  # noqa: E402
    JUDGE_PROMPT_VERSION,
    MODEL_PRICES,
    PRICE_SOURCE,
    PRICING_AS_OF,
    ModelPrice,
    RunConfig,
    RunSummary,
    dataset_digest,
    estimate_maximum_cost,
    load_run_cost,
    run_judgments,
    stratified_prefix,
)
from interlock.eval.openrouter_judge import OpenRouterJudge  # noqa: E402

LABELS = REPO_ROOT / "data/labels/manual_anchor_300.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/eval/manual_anchor_judgments.jsonl"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return parsed


def load_items(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid label JSON at line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"label row {line_number} must be an object")
        rows.append(row)
    return rows


def _explicit_price(args: argparse.Namespace, model: str) -> ModelPrice:
    input_price = args.input_price_per_million
    output_price = args.output_price_per_million
    if (input_price is None) != (output_price is None):
        raise ValueError(
            "both --input-price-per-million and --output-price-per-million are required"
        )
    if input_price is not None and output_price is not None:
        return ModelPrice(input_price, output_price)
    try:
        return MODEL_PRICES[model]
    except KeyError as exc:
        raise ValueError(
            f"unknown model {model!r}; supply explicit input and output prices"
        ) from exc


def _model_output(base: Path, model: str, *, multiple: bool) -> Path:
    if not multiple:
        return base
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    suffix = base.suffix or ".jsonl"
    stem = base.name[: -len(base.suffix)] if base.suffix else base.name
    return base.with_name(f"{stem}-{safe_model}{suffix}")


def _models(values: list[str] | None) -> list[str]:
    configured = os.getenv("INTERLOCK_STRONG_MODEL") or "openai/gpt-5-mini"
    candidates = values or [configured]
    return list(dict.fromkeys(candidates))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", help="repeat for separately identified runs")
    parser.add_argument("--labels", type=Path, default=LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-cost-usd", type=_decimal, default=Decimal("1.50"))
    parser.add_argument("--input-price-per-million", type=_decimal)
    parser.add_argument("--output-price-per-million", type=_decimal)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-external-context", action="store_true")
    return parser


def _print_json(payload: object, *, stream: TextIO | None = None) -> None:
    print(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        file=stream or sys.stdout,
    )


def summary_payload(summary: RunSummary) -> dict[str, object]:
    """Convert Decimal fields explicitly so final paid-run output is valid JSON."""
    return {
        **asdict(summary),
        "cost_usd": str(summary.cost_usd),
        "max_cost_usd": str(summary.max_cost_usd),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")

    try:
        rows = load_items(args.labels)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    digest = dataset_digest(rows)
    selected = stratified_prefix(rows, min(args.limit, len(rows)))
    models = _models(args.model)
    plans: list[tuple[str, Path, ModelPrice, Decimal]] = []
    try:
        for model in models:
            price = _explicit_price(args, model)
            estimated = estimate_maximum_cost(
                selected,
                batch_size=args.batch_size,
                price=price,
                max_attempts=OpenRouterJudge.max_attempts,
            )
            output = _model_output(args.output, model, multiple=len(models) > 1)
            plans.append((model, output, price, estimated))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    combined_estimated = sum((plan[3] for plan in plans), Decimal(0))
    _print_json(
        {
            "case_count": len(selected),
            "dataset_digest": digest,
            "prompt_version": JUDGE_PROMPT_VERSION,
            "total_configured_cost_cap_usd": str(args.max_cost_usd),
            "combined_estimated_max_cost_usd": str(combined_estimated),
            "estimated_max_cost_usd": str(combined_estimated),
            "models": [
                {
                    "model": model,
                    "output": str(output),
                    "estimated_max_cost_usd": str(estimated),
                    "input_price_per_million": str(price.input_per_million),
                    "output_price_per_million": str(price.output_per_million),
                }
                for model, output, price, estimated in plans
            ],
            "pricing_as_of": PRICING_AS_OF,
            "pricing_source": PRICE_SOURCE,
            "network_calls": 0,
        }
    )

    if not args.allow_external_context:
        print(
            "error: --allow-external-context is required before anchor text leaves this host",
            file=sys.stderr,
        )
        return 2

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("error: OPENAI_API_KEY is required", file=sys.stderr)
        return 2

    for _, output, _, _ in plans:
        metadata = Path(f"{output}.meta.json")
        if not args.resume and (output.exists() or metadata.exists()):
            print(
                f"error: output already exists; pass --resume to continue: {output}",
                file=sys.stderr,
            )
            return 2

    try:
        existing_costs = {
            model: load_run_cost(output, model=model, rows=rows) for model, output, _, _ in plans
        }
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    existing_total = sum(existing_costs.values(), Decimal(0))
    if existing_total > args.max_cost_usd:
        print(
            "error: resumed runs already exceed the shared configured cost cap",
            file=sys.stderr,
        )
        return 1

    with httpx.Client(timeout=180) as client:
        judge = OpenRouterJudge(
            client,
            base_url=args.base_url,
            api_key=api_key,
            sleep=time.sleep,
        )
        remaining_new_budget = args.max_cost_usd - existing_total
        for model, output, price, _ in plans:
            existing_cost = existing_costs[model]
            config = RunConfig(
                model=model,
                limit=min(args.limit, len(rows)),
                batch_size=args.batch_size,
                max_cost_usd=existing_cost + remaining_new_budget,
                allow_external_context=True,
                price=price,
            )
            try:
                summary = run_judgments(config, rows, judge, output)
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            _print_json(summary_payload(summary))
            new_cost = max(Decimal(0), summary.cost_usd - existing_cost)
            remaining_new_budget = max(Decimal(0), remaining_new_budget - new_cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
