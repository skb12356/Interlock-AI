"""Resumable, stratified, budget-capped execution for offline judge audits.

The runner deliberately knows nothing about routing or shipped decisions. It sends the
approved offline anchor to exactly one configured judge model, records every normalized
outcome, and stops before a worst-case retry reservation could cross the paid-run cap.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from interlock.eval.openrouter_judge import (
    JudgeItem,
    JudgeResult,
    JudgeUsage,
    gold_label_from_anchor,
)

__all__ = [
    "JUDGE_PROMPT_VERSION",
    "MODEL_PRICES",
    "ModelPrice",
    "RunConfig",
    "RunSummary",
    "dataset_digest",
    "estimate_maximum_cost",
    "load_completed",
    "run_judgments",
    "stratified_prefix",
]

JUDGE_PROMPT_VERSION = "openrouter-grounding-judge-v1"
PRICING_AS_OF = "2026-08-30"
PRICE_SOURCE = "OpenRouter published per-token prices; run-control estimate"
_OUTPUT_TOKENS_PER_ITEM = 256
_PROMPT_OVERHEAD_TOKENS = 512
_MODE_ORDER = (
    "clean",
    "retrieval_dropped",
    "number_corrupted",
    "clause_swapped",
    "unanswerable",
    "contradiction",
)
_LEVEL_ORDER = ("L1_direct", "L2_distractor", "L3_conflict")
_API_KEY_PATTERN = re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{8,}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{8,}")
_CANARY_PATTERN = re.compile(r"\bIL-CANARY-[A-Za-z0-9_-]+\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD prices per one million input and output tokens."""

    input_per_million: Decimal
    output_per_million: Decimal

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(prompt_tokens) * self.input_per_million
            + Decimal(completion_tokens) * self.output_per_million
        ) / million


MODEL_PRICES: dict[str, ModelPrice] = {
    "openai/gpt-5-nano": ModelPrice(Decimal("0.05"), Decimal("0.40")),
    "openai/gpt-5-mini": ModelPrice(Decimal("0.25"), Decimal("2.00")),
}


@dataclass(frozen=True, slots=True)
class RunConfig:
    """One-model paid-run controls; a price is mandatory for unknown model IDs."""

    model: str
    limit: int
    batch_size: int
    max_cost_usd: Decimal
    allow_external_context: bool
    price: ModelPrice | None = None


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Auditable completion, usage, and termination state for one model run."""

    model: str
    dataset_digest: str
    prompt_version: str
    selected: int
    completed: int
    resumed: int
    batches: int
    network_calls: int
    actual_attempts: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    max_cost_usd: Decimal
    termination_reason: str


class Judge(Protocol):
    max_attempts: int

    def judge(self, model: str, items: Sequence[JudgeItem]) -> list[JudgeResult]: ...


@dataclass(slots=True)
class _RunState:
    completed: set[str]
    batches: set[str]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    actual_attempts: int = 0


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def dataset_digest(
    rows: Sequence[Mapping[str, Any]], *, prompt_version: str = JUDGE_PROMPT_VERSION
) -> str:
    """Hash the ordered canonical dataset and prompt contract as one run identity."""
    encoded = _canonical_json({"prompt_version": prompt_version, "rows": list(rows)}).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _slice_value(row: Mapping[str, Any], key: str) -> str:
    payload = row.get("payload")
    if not isinstance(payload, Mapping) or not isinstance(payload.get(key), str):
        raise ValueError(f"anchor row requires payload.{key}")
    return str(payload[key])


def _ordered_values(values: set[str], preferred: Sequence[str]) -> tuple[str, ...]:
    return (*[value for value in preferred if value in values], *sorted(values - set(preferred)))


def stratified_prefix(rows: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Select every prefix by cycling modes, then levels inside each mode."""
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    if limit == 0 or not rows:
        return []

    buckets: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    modes: set[str] = set()
    levels: set[str] = set()
    for row in rows:
        mode = _slice_value(row, "failure_mode")
        level = _slice_value(row, "challenge_level")
        buckets[(mode, level)].append(row)
        modes.add(mode)
        levels.add(level)

    mode_order = _ordered_values(modes, _MODE_ORDER)
    level_order = _ordered_values(levels, _LEVEL_ORDER)
    offsets: defaultdict[tuple[str, str], int] = defaultdict(int)
    selected: list[dict[str, Any]] = []
    while len(selected) < min(limit, len(rows)):
        made_progress = False
        for level in level_order:
            for mode in mode_order:
                key = (mode, level)
                offset = offsets[key]
                if offset >= len(buckets[key]):
                    continue
                selected.append(buckets[key][offset])
                offsets[key] += 1
                made_progress = True
                if len(selected) == min(limit, len(rows)):
                    return selected
        if not made_progress:
            break
    return selected


def _run_identity(record: Mapping[str, Any]) -> tuple[object, object, object]:
    return (
        record.get("model"),
        record.get("dataset_digest"),
        record.get("prompt_version"),
    )


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL record at line {line_number}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"invalid JSONL record at line {line_number}")
        records.append(raw)
    return records


def load_completed(path: Path, *, model: str, dataset_digest: str) -> set[str]:
    """Load exact-identity results, refusing corrupt, duplicate, or mixed runs."""
    expected_identity = (model, dataset_digest, JUDGE_PROMPT_VERSION)
    completed: set[str] = set()
    for record in _read_records(path):
        if _run_identity(record) != expected_identity:
            raise ValueError("output contains an incompatible run identity")
        item_id = record.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("output record requires a nonblank item_id")
        if item_id in completed:
            raise ValueError(f"duplicate completed item: {item_id}")
        completed.add(item_id)
    return completed


def _as_nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _as_nonnegative_decimal(value: object) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return Decimal(0)
    return normalized if normalized.is_finite() and normalized >= 0 else Decimal(0)


def _load_state(path: Path, *, model: str, digest: str, price: ModelPrice) -> _RunState:
    records = _read_records(path)
    completed = load_completed(path, model=model, dataset_digest=digest)
    state = _RunState(completed=completed, batches=set())
    for record in records:
        item_id = str(record["item_id"])
        batch_id = record.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id:
            batch_id = f"legacy-item:{item_id}"
        if batch_id in state.batches:
            continue
        state.batches.add(batch_id)
        usage = record.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        prompt_tokens = _as_nonnegative_int(usage.get("prompt_tokens"))
        completion_tokens = _as_nonnegative_int(usage.get("completion_tokens"))
        attempts = max(1, _as_nonnegative_int(record.get("attempts")))
        state.prompt_tokens += prompt_tokens
        state.completion_tokens += completion_tokens
        state.actual_attempts += attempts
        if "accounted_cost_usd" in record:
            state.cost_usd += _as_nonnegative_decimal(record["accounted_cost_usd"])
        else:
            raw_cost = usage.get("cost_usd")
            state.cost_usd += (
                _as_nonnegative_decimal(raw_cost)
                if raw_cost is not None
                else price.cost(prompt_tokens, completion_tokens) * attempts
            )
    return state


def _price(config: RunConfig) -> ModelPrice:
    price = config.price or MODEL_PRICES.get(config.model)
    if price is None:
        raise ValueError(
            f"unknown model {config.model!r}; explicit input and output prices are required"
        )
    if price.input_per_million < 0 or price.output_per_million < 0:
        raise ValueError("model prices must be nonnegative")
    return price


def _judge_item(row: Mapping[str, Any]) -> JudgeItem:
    item_id = row.get("item_id")
    payload = row.get("payload")
    if not isinstance(item_id, str) or not item_id or not isinstance(payload, Mapping):
        raise ValueError("anchor row requires item_id and payload")
    question = payload.get("question")
    answer = payload.get("answer")
    raw_context = payload.get("context")
    if (
        not isinstance(question, str)
        or not isinstance(answer, str)
        or not isinstance(raw_context, list)
    ):
        raise ValueError(f"anchor row {item_id} has an invalid judge payload")
    context: list[str] = []
    for fragment in raw_context:
        if not isinstance(fragment, Mapping) or not isinstance(fragment.get("text"), str):
            raise ValueError(f"anchor row {item_id} has an invalid context fragment")
        context.append(str(fragment["text"]))
    return JudgeItem(item_id=item_id, question=question, context=tuple(context), answer=answer)


def _estimate_prompt_tokens(items: Sequence[JudgeItem]) -> int:
    # UTF-8 bytes are a deliberately conservative token ceiling for this English corpus.
    body = _canonical_json([asdict(item) for item in items]).encode("utf-8")
    return _PROMPT_OVERHEAD_TOKENS + len(body)


def _projected_batch_cost(items: Sequence[JudgeItem], price: ModelPrice) -> Decimal:
    return price.cost(
        _estimate_prompt_tokens(items),
        _OUTPUT_TOKENS_PER_ITEM * len(items),
    )


def estimate_maximum_cost(
    rows: Sequence[dict[str, Any]],
    *,
    batch_size: int,
    price: ModelPrice,
    max_attempts: int,
) -> Decimal:
    """Estimate the maximum run spend using every batch's full retry ceiling."""
    if batch_size <= 0 or max_attempts <= 0:
        raise ValueError("batch_size and max_attempts must be positive")
    items = [_judge_item(row) for row in rows]
    return sum(
        (
            _projected_batch_cost(items[start : start + batch_size], price) * max_attempts
            for start in range(0, len(items), batch_size)
        ),
        Decimal(0),
    )


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = _API_KEY_PATTERN.sub("[REDACTED-API-KEY]", value)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    return _CANARY_PATTERN.sub("[REDACTED-CANARY]", redacted)


def _batch_usage(results: Sequence[JudgeResult]) -> JudgeUsage:
    if not results:
        raise ValueError("judge returned no results for a nonempty batch")
    usage = results[0].usage
    if any(result.usage != usage for result in results[1:]):
        raise ValueError("judge returned inconsistent request-level usage within a batch")
    return usage


def _batch_attempts(results: Sequence[JudgeResult], maximum: int) -> int:
    attempts = {result.attempts for result in results}
    if len(attempts) != 1:
        raise ValueError("judge returned inconsistent attempts within a batch")
    value = attempts.pop()
    if value < 1 or value > maximum:
        raise ValueError("judge returned attempts outside its configured retry ceiling")
    return value


def _accounted_cost(
    usage: JudgeUsage,
    *,
    price: ModelPrice,
    attempts: int,
    projected_single_attempt: Decimal,
) -> Decimal:
    if usage.cost_usd is not None:
        return _as_nonnegative_decimal(usage.cost_usd)
    estimated_from_usage = price.cost(usage.prompt_tokens, usage.completion_tokens)
    if estimated_from_usage > 0:
        return estimated_from_usage * attempts
    return projected_single_attempt * attempts


def _metadata_path(output: Path) -> Path:
    return Path(f"{output}.meta.json")


def _validate_metadata(path: Path, *, model: str, digest: str) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("run metadata is not valid JSON") from exc
    if not isinstance(metadata, Mapping) or _run_identity(metadata) != (
        model,
        digest,
        JUDGE_PROMPT_VERSION,
    ):
        raise ValueError("metadata contains an incompatible run identity")
    if metadata.get("in_flight"):
        raise ValueError(
            "metadata contains an unresolved in-flight batch; reconcile it before resuming"
        )
    return metadata


def _summary_metadata(
    summary: RunSummary,
    *,
    price: ModelPrice,
    started_at: str,
    ended_at: str | None,
    in_flight: Mapping[str, object] | None,
) -> dict[str, object]:
    return {
        "model": summary.model,
        "dataset_digest": summary.dataset_digest,
        "prompt_version": summary.prompt_version,
        "selected": summary.selected,
        "completed": summary.completed,
        "resumed": summary.resumed,
        "batches": summary.batches,
        "network_calls": summary.network_calls,
        "actual_attempts": summary.actual_attempts,
        "request_count": summary.actual_attempts,
        "prompt_tokens": summary.prompt_tokens,
        "completion_tokens": summary.completion_tokens,
        "cost_usd": str(summary.cost_usd),
        "max_cost_usd": str(summary.max_cost_usd),
        "termination_reason": summary.termination_reason,
        "price_input_per_million": str(price.input_per_million),
        "price_output_per_million": str(price.output_per_million),
        "pricing_as_of": PRICING_AS_OF,
        "pricing_source": PRICE_SOURCE,
        "started_at": started_at,
        "ended_at": ended_at,
        "in_flight": in_flight,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _write_metadata(path: Path, metadata: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _make_summary(
    *,
    config: RunConfig,
    digest: str,
    selected_ids: set[str],
    resumed: int,
    state: _RunState,
    new_attempts: int,
    termination_reason: str,
) -> RunSummary:
    return RunSummary(
        model=config.model,
        dataset_digest=digest,
        prompt_version=JUDGE_PROMPT_VERSION,
        selected=len(selected_ids),
        completed=len(selected_ids & state.completed),
        resumed=resumed,
        batches=len(state.batches),
        network_calls=new_attempts,
        actual_attempts=state.actual_attempts,
        prompt_tokens=state.prompt_tokens,
        completion_tokens=state.completion_tokens,
        cost_usd=state.cost_usd,
        max_cost_usd=config.max_cost_usd,
        termination_reason=termination_reason,
    )


def run_judgments(
    config: RunConfig,
    rows: Sequence[dict[str, Any]],
    judge: Judge,
    output: Path,
) -> RunSummary:
    """Run one exact-model audit, resuming safely and stopping before its cost cap."""
    if not config.allow_external_context:
        raise PermissionError("allow_external_context is required for paid judge dispatch")
    if config.limit < 0 or config.batch_size <= 0 or config.max_cost_usd < 0:
        raise ValueError("limit and cost must be nonnegative; batch_size must be positive")
    if judge.max_attempts <= 0:
        raise ValueError("judge max_attempts must be positive")

    price = _price(config)
    digest = dataset_digest(rows)
    selected = stratified_prefix(rows, min(config.limit, len(rows)))
    selected_ids = {str(row["item_id"]) for row in selected}
    if len(selected_ids) != len(selected):
        raise ValueError("selected anchor rows require unique item IDs")

    existing_metadata = _validate_metadata(
        _metadata_path(output), model=config.model, digest=digest
    )
    started_at = (
        str(existing_metadata.get("started_at"))
        if existing_metadata and existing_metadata.get("started_at")
        else datetime.now(UTC).isoformat()
    )
    state = _load_state(output, model=config.model, digest=digest, price=price)
    resumed = len(selected_ids & state.completed)
    pending = [row for row in selected if str(row["item_id"]) not in state.completed]
    new_attempts = 0
    termination_reason = "complete"
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("a", encoding="utf-8", newline="\n") as stream:
        for start in range(0, len(pending), config.batch_size):
            batch_rows = pending[start : start + config.batch_size]
            items = [_judge_item(row) for row in batch_rows]
            projected_single = _projected_batch_cost(items, price)
            retry_reservation = projected_single * judge.max_attempts
            if state.cost_usd + retry_reservation > config.max_cost_usd:
                termination_reason = "budget_cap"
                break

            expected_ids = [item.item_id for item in items]
            batch_id_seed = "|".join((config.model, digest, *expected_ids, str(len(state.batches))))
            batch_id = f"batch-{hashlib.sha256(batch_id_seed.encode()).hexdigest()[:20]}"
            before_dispatch = _make_summary(
                config=config,
                digest=digest,
                selected_ids=selected_ids,
                resumed=resumed,
                state=state,
                new_attempts=new_attempts,
                termination_reason="in_progress",
            )
            _write_metadata(
                _metadata_path(output),
                _summary_metadata(
                    before_dispatch,
                    price=price,
                    started_at=started_at,
                    ended_at=None,
                    in_flight={
                        "batch_id": batch_id,
                        "item_ids": expected_ids,
                        "reserved_cost_usd": str(retry_reservation),
                        "max_attempts": judge.max_attempts,
                    },
                ),
            )
            results = judge.judge(config.model, items)
            result_ids = [result.item_id for result in results]
            if result_ids != expected_ids:
                raise ValueError(
                    "judge results must preserve exactly one result per requested item"
                )
            usage = _batch_usage(results)
            attempts = _batch_attempts(results, judge.max_attempts)
            accounted_cost = _accounted_cost(
                usage,
                price=price,
                attempts=attempts,
                projected_single_attempt=projected_single,
            )
            provider_cost_overrun = state.cost_usd + accounted_cost > config.max_cost_usd
            gold_by_id = {str(row["item_id"]): gold_label_from_anchor(row) for row in batch_rows}
            for result in results:
                record = {
                    "item_id": result.item_id,
                    "model": config.model,
                    "dataset_digest": digest,
                    "prompt_version": JUDGE_PROMPT_VERSION,
                    "batch_id": batch_id,
                    "gold": gold_by_id[result.item_id],
                    "status": result.status,
                    "judge_label": result.label,
                    "confidence": result.confidence,
                    "rationale": _safe_text(result.rationale),
                    "usage": {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "cost_usd": usage.cost_usd,
                    },
                    "latency_ms": result.latency_ms,
                    "error": _safe_text(result.error),
                    "attempts": attempts,
                    "accounted_cost_usd": str(accounted_cost),
                }
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                state.completed.add(result.item_id)

            state.batches.add(batch_id)
            state.prompt_tokens += usage.prompt_tokens
            state.completion_tokens += usage.completion_tokens
            state.cost_usd += accounted_cost
            state.actual_attempts += attempts
            new_attempts += attempts
            progress = _make_summary(
                config=config,
                digest=digest,
                selected_ids=selected_ids,
                resumed=resumed,
                state=state,
                new_attempts=new_attempts,
                termination_reason=(
                    "provider_cost_overrun" if provider_cost_overrun else "in_progress"
                ),
            )
            _write_metadata(
                _metadata_path(output),
                _summary_metadata(
                    progress,
                    price=price,
                    started_at=started_at,
                    ended_at=(datetime.now(UTC).isoformat() if provider_cost_overrun else None),
                    in_flight=None,
                ),
            )
            if provider_cost_overrun:
                termination_reason = "provider_cost_overrun"
                break

    summary = _make_summary(
        config=config,
        digest=digest,
        selected_ids=selected_ids,
        resumed=resumed,
        state=state,
        new_attempts=new_attempts,
        termination_reason=termination_reason,
    )
    _write_metadata(
        _metadata_path(output),
        _summary_metadata(
            summary,
            price=price,
            started_at=started_at,
            ended_at=datetime.now(UTC).isoformat(),
            in_flight=None,
        ),
    )
    return summary
