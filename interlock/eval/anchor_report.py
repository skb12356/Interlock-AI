"""Honest analysis of generated grounding anchors judged through OpenRouter.

The judge's false-positive rate is a model-agreement measurement over offline anchors.
It is deliberately kept separate from Interlock's stakes-aware action rate.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from interlock.eval.metrics import wilson_interval

__all__ = ["build_anchor_report", "render_anchor_markdown"]

_LABELS = ("clean", "ungrounded", "contradicted")
_SLICE_FIELDS = ("failure_mode", "challenge_level", "domain")
_MAX_EXAMPLES = 20


def _gold(row: Mapping[str, Any]) -> str:
    ungrounded = row.get("gold_ungrounded") == 1
    contradicted = row.get("gold_contradicted") == 1
    if ungrounded and contradicted:
        raise ValueError(f"anchor {row.get('item_id')!r} has conflicting grounding labels")
    return "ungrounded" if ungrounded else "contradicted" if contradicted else "clean"


def _item_id(row: Mapping[str, Any], *, source: str) -> str:
    value = row.get("item_id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source} row requires a nonblank item_id")
    return value


def _indexed(rows: Sequence[Mapping[str, Any]], *, source: str) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        item_id = _item_id(row, source=source)
        if item_id in output:
            raise ValueError(f"duplicate {source} item_id: {item_id}")
        output[item_id] = row
    return output


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
        "ci_95": list(wilson_interval(numerator, denominator)) if denominator else None,
    }


def _agreement(valid: Sequence[tuple[str, str]]) -> dict[str, Any]:
    strict_correct = sum(gold == predicted for gold, predicted in valid)
    binary_correct = sum((gold == "clean") == (predicted == "clean") for gold, predicted in valid)
    clean = [(gold, predicted) for gold, predicted in valid if gold == "clean"]
    defective = [(gold, predicted) for gold, predicted in valid if gold != "clean"]
    confusion = {gold: {predicted: 0 for predicted in _LABELS} for gold in _LABELS}
    for gold, predicted in valid:
        confusion[gold][predicted] += 1
    return {
        "strict_three_class": {
            "correct": strict_correct,
            "total": len(valid),
            **_rate(strict_correct, len(valid)),
            "confusion": confusion,
        },
        "binary_grounding": {
            "correct": binary_correct,
            "total": len(valid),
            **_rate(binary_correct, len(valid)),
        },
        "false_intervention_on_clean": _rate(
            sum(predicted != "clean" for _, predicted in clean), len(clean)
        ),
        "grounding_escape": _rate(
            sum(predicted == "clean" for _, predicted in defective), len(defective)
        ),
    }


def _slice_report(
    labels: Mapping[str, Mapping[str, Any]],
    judgments: Mapping[str, Mapping[str, Any]],
    *,
    field: str,
) -> dict[str, Any]:
    buckets: defaultdict[str, list[str]] = defaultdict(list)
    for item_id, label in labels.items():
        payload = label.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(payload.get(field), str):
            raise ValueError(f"anchor {item_id!r} requires payload.{field}")
        buckets[str(payload[field])].append(item_id)

    output: dict[str, Any] = {}
    for value in sorted(buckets):
        item_ids = buckets[value]
        valid = [
            (_gold(labels[item_id]), str(judgments[item_id]["judge_label"]))
            for item_id in item_ids
            if item_id in judgments and judgments[item_id].get("status") == "valid"
        ]
        output[value] = {
            "total": len(item_ids),
            "valid": len(valid),
            "agreement": _agreement(valid),
        }
    return output


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be a nonnegative decimal")
    return parsed


def _usage(judgments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    batches: dict[str, tuple[int, int, Decimal, float]] = {}
    for row in judgments:
        batch_id = row.get("batch_id")
        usage = row.get("usage")
        if not isinstance(batch_id, str) or not batch_id or not isinstance(usage, Mapping):
            raise ValueError("judgment rows require batch_id and usage")
        accounting = (
            int(usage.get("prompt_tokens", -1)),
            int(usage.get("completion_tokens", -1)),
            _decimal(row.get("accounted_cost_usd"), field="accounted_cost_usd"),
            float(row.get("latency_ms", -1)),
        )
        if (
            accounting[0] < 0
            or accounting[1] < 0
            or not math.isfinite(accounting[3])
            or accounting[3] < 0
        ):
            raise ValueError("judgment usage and latency must be nonnegative")
        previous = batches.setdefault(batch_id, accounting)
        if previous != accounting:
            raise ValueError(f"inconsistent request accounting for batch {batch_id}")

    rows = list(batches.values())
    latencies = sorted(row[3] for row in rows)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1) if latencies else 0
    return {
        "request_count": len(rows),
        "prompt_tokens": sum(row[0] for row in rows),
        "completion_tokens": sum(row[1] for row in rows),
        "accounted_cost_usd": str(sum((row[2] for row in rows), Decimal(0))),
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else None,
            "p95": latencies[p95_index] if latencies else None,
        },
    }


def build_anchor_report(
    labels: Sequence[Mapping[str, Any]],
    judgments: Sequence[Mapping[str, Any]],
    *,
    model: str,
) -> dict[str, Any]:
    """Build a complete report without conflating judge agreement and product actions."""
    by_label = _indexed(labels, source="label")
    by_judgment = _indexed(judgments, source="judgment")
    extras = set(by_judgment) - set(by_label)
    if extras:
        raise ValueError(f"judgments contain items outside the anchor: {sorted(extras)[:3]}")

    valid_pairs: list[tuple[str, str]] = []
    invalid_statuses: Counter[str] = Counter()
    invalid_examples: list[str] = []
    failed_examples: list[dict[str, str]] = []
    for item_id, label in by_label.items():
        gold = _gold(label)
        judgment = by_judgment.get(item_id)
        if judgment is None:
            invalid_statuses["missing_result"] += 1
            invalid_examples.append(item_id)
            continue
        if judgment.get("model") != model or judgment.get("gold") != gold:
            raise ValueError(f"judgment identity mismatch for {item_id}")
        status = judgment.get("status")
        predicted = judgment.get("judge_label")
        if status != "valid":
            invalid_statuses[str(status)] += 1
            invalid_examples.append(item_id)
            continue
        if predicted not in _LABELS:
            raise ValueError(f"valid judgment {item_id!r} has an invalid label")
        valid_pairs.append((gold, str(predicted)))
        if gold != predicted and len(failed_examples) < _MAX_EXAMPLES:
            payload = label.get("payload")
            mode = payload.get("failure_mode") if isinstance(payload, Mapping) else "unknown"
            failed_examples.append(
                {
                    "item_id": item_id,
                    "gold": gold,
                    "judge_label": str(predicted),
                    "failure_mode": str(mode),
                }
            )

    cluster_counts: Counter[str] = Counter()
    for item_id, label in by_label.items():
        payload = label.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("evidence_cluster_id"), str
        ):
            raise ValueError(f"anchor {item_id!r} requires payload.evidence_cluster_id")
        cluster_counts[str(payload["evidence_cluster_id"])] += 1

    return {
        "schema_version": 1,
        "model": model,
        "source": {
            "kind": "openrouter_judge_on_generated_anchor",
            "human_reviewed": False,
            "production_traffic": False,
            "taxonomy_warning": (
                "These anchors are generated and unreviewed. Judge disagreement measures "
                "offline grounding classification, not Interlock's stakes-aware intervention rate."
            ),
        },
        "validity": {
            "total": len(by_label),
            "valid": len(valid_pairs),
            **_rate(len(valid_pairs), len(by_label)),
        },
        "agreement": _agreement(valid_pairs),
        "slices": {
            field: _slice_report(by_label, by_judgment, field=field) for field in _SLICE_FIELDS
        },
        "evidence_clusters": {
            "unique": len(cluster_counts),
            "with_multiple_items": sum(count > 1 for count in cluster_counts.values()),
            "largest_cluster_items": max(cluster_counts.values(), default=0),
        },
        "usage": _usage(list(by_judgment.values())),
        "invalid_results": {
            "by_status": dict(sorted(invalid_statuses.items())),
            "examples": invalid_examples[:_MAX_EXAMPLES],
        },
        "failed_examples": failed_examples,
    }


def _percent(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("report rate must be numeric or unavailable")
    return f"{float(value):.2%}"


def render_anchor_markdown(report: Mapping[str, Any]) -> str:
    """Render the audit with its provenance warning adjacent to every headline."""
    source = report["source"]
    validity = report["validity"]
    agreement = report["agreement"]
    usage = report["usage"]
    assert isinstance(source, Mapping)
    assert isinstance(validity, Mapping)
    assert isinstance(agreement, Mapping)
    assert isinstance(usage, Mapping)
    strict = agreement["strict_three_class"]
    binary = agreement["binary_grounding"]
    false_intervention = agreement["false_intervention_on_clean"]
    escape = agreement["grounding_escape"]
    assert all(isinstance(row, Mapping) for row in (strict, binary, false_intervention, escape))
    return "\n".join(
        [
            "# OpenRouter grounding-anchor report",
            "",
            f"Model: **{report['model']}**",
            "",
            "> **NOT human-reviewed or production evidence.** " + str(source["taxonomy_warning"]),
            "",
            "| Measurement | Result |",
            "| --- | ---: |",
            f"| Valid judge results | {validity['valid']}/{validity['total']} ({_percent(validity['rate'])}) |",
            f"| Strict three-class agreement | {_percent(strict['rate'])} |",
            f"| Binary grounded/defective agreement | {_percent(binary['rate'])} |",
            f"| False intervention on clean anchors | {_percent(false_intervention['rate'])} |",
            f"| Grounding escape | {_percent(escape['rate'])} |",
            "",
            f"Request-level accounted cost: **USD {usage['accounted_cost_usd']}** over "
            f"{usage['request_count']} requests.",
            "",
        ]
    )
