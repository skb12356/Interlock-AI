"""Resumable and budget-safe OpenRouter evaluation runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from scripts.eval_manual_anchors import _print_json, summary_payload

from interlock.eval.judge_run import (
    JUDGE_PROMPT_VERSION,
    MODEL_PRICES,
    ModelPrice,
    RunConfig,
    dataset_digest,
    load_completed,
    run_judgments,
    stratified_prefix,
)
from interlock.eval.openrouter_judge import JudgeItem, JudgeResult, JudgeUsage

REPO_ROOT = Path(__file__).resolve().parents[2]
MODES = (
    "clean",
    "retrieval_dropped",
    "number_corrupted",
    "clause_swapped",
    "unanswerable",
    "contradiction",
)
LEVELS = ("L1_direct", "L2_distractor", "L3_conflict")


def _row(index: int, mode: str, level: str) -> dict[str, object]:
    contradicted = int(mode == "contradiction")
    ungrounded = int(mode not in {"clean", "contradiction"})
    return {
        "item_id": f"item-{index:02d}",
        "gold_ungrounded": ungrounded,
        "gold_contradicted": contradicted,
        "gold_unsafe": 0,
        "payload": {
            "failure_mode": mode,
            "challenge_level": level,
            "question": f"Question {index}?",
            "context": [
                {
                    "text": f"Trusted banking policy evidence {index}.",
                    "doc_id": f"doc-{index}",
                    "provenance": "retrieved_verified",
                }
            ],
            "answer": f"Answer {index}.",
        },
    }


ROWS = tuple(
    _row(index, MODES[index % len(MODES)], LEVELS[(index // len(MODES)) % len(LEVELS)])
    for index in range(12)
)


class RecordingJudge:
    max_attempts = 3

    def __init__(
        self,
        *,
        usage: JudgeUsage = JudgeUsage(100, 20, None),
        attempts: int = 1,
    ) -> None:
        self.usage = usage
        self.attempts = attempts
        self.calls: list[tuple[str, ...]] = []
        self.models: list[str] = []

    def judge(self, model: str, items: Sequence[JudgeItem]) -> list[JudgeResult]:
        self.models.append(model)
        self.calls.append(tuple(item.item_id for item in items))
        return [
            JudgeResult(
                item_id=item.item_id,
                status="valid",
                label="clean",
                confidence=0.9,
                rationale="Supported by the supplied evidence.",
                usage=self.usage,
                latency_ms=5.0,
                error=None,
                attempts=self.attempts,
            )
            for item in items
        ]


def _config(**changes: Any) -> RunConfig:
    config = RunConfig(
        model="openai/gpt-5-nano",
        limit=12,
        batch_size=3,
        max_cost_usd=Decimal("1.50"),
        allow_external_context=True,
    )
    return replace(config, **changes)


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_stratified_prefix_starts_with_every_mode_instead_of_file_order() -> None:
    ordered = [
        _row(index, mode, "L1_direct") for index, mode in enumerate(("clean",) * 7 + MODES[1:])
    ]

    selected = stratified_prefix(ordered, 6)

    assert {row["payload"]["failure_mode"] for row in selected} == set(MODES)


def test_stratified_prefix_round_robins_levels_within_each_mode() -> None:
    ordered = [
        _row(index, mode, level)
        for index, (mode, level) in enumerate((mode, level) for level in LEVELS for mode in MODES)
    ]

    selected = stratified_prefix(ordered, 12)

    assert [row["payload"]["challenge_level"] for row in selected[:6]] == ["L1_direct"] * 6
    assert [row["payload"]["challenge_level"] for row in selected[6:]] == ["L2_distractor"] * 6


def test_dataset_digest_is_canonical_and_includes_prompt_version() -> None:
    reordered = [{key: row[key] for key in reversed(tuple(row))} for row in ROWS]

    assert dataset_digest(ROWS) == dataset_digest(reordered)
    assert dataset_digest(ROWS, prompt_version="different") != dataset_digest(ROWS)


def test_resume_dispatches_only_unfinished_ids_and_never_duplicates_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run.jsonl"
    first_judge = RecordingJudge()
    first = run_judgments(_config(limit=2, batch_size=2), ROWS, first_judge, output)
    assert first.completed == 2

    second_judge = RecordingJudge()
    summary = run_judgments(_config(limit=6, batch_size=2), ROWS, second_judge, output)

    completed_before = {item for call in first_judge.calls for item in call}
    dispatched_after = {item for call in second_judge.calls for item in call}
    assert not completed_before & dispatched_after
    records = _jsonl(output)
    identities = {(record["model"], record["item_id"]) for record in records}
    assert len(identities) == len(records) == 6
    assert summary.completed == 6
    assert summary.resumed == 2


def test_resume_refuses_model_dataset_or_prompt_identity_mismatch(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    digest = dataset_digest(ROWS)
    base = {
        "item_id": "item-00",
        "model": "openai/gpt-5-nano",
        "dataset_digest": digest,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "batch_id": "batch-1",
    }
    for field, value in (
        ("model", "openai/gpt-5-mini"),
        ("dataset_digest", "wrong"),
        ("prompt_version", "wrong"),
    ):
        output.write_text(json.dumps({**base, field: value}) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="incompatible run identity"):
            load_completed(output, model="openai/gpt-5-nano", dataset_digest=digest)


def test_resume_refuses_duplicate_existing_item_identity(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    digest = dataset_digest(ROWS)
    record = {
        "item_id": "item-00",
        "model": "openai/gpt-5-nano",
        "dataset_digest": digest,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "batch_id": "batch-1",
    }
    output.write_text(f"{json.dumps(record)}\n{json.dumps(record)}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate completed item"):
        load_completed(output, model="openai/gpt-5-nano", dataset_digest=digest)


def test_run_stops_before_dispatching_a_batch_that_crosses_the_cap(tmp_path: Path) -> None:
    config = RunConfig(
        model="openai/gpt-5-mini",
        limit=10,
        batch_size=5,
        max_cost_usd=Decimal("0.001"),
        allow_external_context=True,
    )
    judge = RecordingJudge()

    summary = run_judgments(config, ROWS, judge, tmp_path / "run.jsonl")

    assert summary.termination_reason == "budget_cap"
    assert summary.cost_usd <= Decimal("0.001")
    assert summary.completed < 10
    assert judge.calls == []


def test_runner_reserves_the_full_retry_ceiling_before_dispatch(tmp_path: Path) -> None:
    judge = RecordingJudge()
    judge.max_attempts = 3
    output = tmp_path / "run.jsonl"

    summary = run_judgments(
        _config(limit=1, batch_size=1, max_cost_usd=Decimal("0.0003")),
        ROWS,
        judge,
        output,
    )

    assert summary.termination_reason == "budget_cap"
    assert summary.network_calls == 0


def test_batch_usage_is_counted_once_and_attempts_are_tracked(tmp_path: Path) -> None:
    judge = RecordingJudge(
        usage=JudgeUsage(1_000, 500, 0.0003),
        attempts=2,
    )

    summary = run_judgments(_config(limit=3, batch_size=3), ROWS, judge, tmp_path / "run.jsonl")

    assert summary.prompt_tokens == 1_000
    assert summary.completion_tokens == 500
    assert summary.cost_usd == Decimal("0.0003")
    assert summary.actual_attempts == 2
    assert summary.network_calls == 2


def test_unknown_model_requires_explicit_prices_before_dispatch(tmp_path: Path) -> None:
    judge = RecordingJudge()
    config = _config(model="openai/unknown-old-model", limit=1)

    with pytest.raises(ValueError, match="explicit input and output prices"):
        run_judgments(config, ROWS, judge, tmp_path / "run.jsonl")

    assert judge.calls == []


def test_unknown_model_runs_only_with_both_explicit_prices(tmp_path: Path) -> None:
    judge = RecordingJudge()
    price = ModelPrice(Decimal("0.01"), Decimal("0.02"))
    config = _config(model="openai/unknown-old-model", limit=1, price=price)

    summary = run_judgments(config, ROWS, judge, tmp_path / "run.jsonl")

    assert summary.completed == 1
    assert judge.models == ["openai/unknown-old-model"]


def test_direct_runner_requires_external_context_opt_in(tmp_path: Path) -> None:
    judge = RecordingJudge()

    with pytest.raises(PermissionError, match="allow_external_context"):
        run_judgments(
            _config(limit=1, allow_external_context=False),
            ROWS,
            judge,
            tmp_path / "run.jsonl",
        )

    assert judge.calls == []


def test_metadata_is_replaced_atomically_without_temporary_debris(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"

    run_judgments(_config(limit=3), ROWS, RecordingJudge(), output)

    metadata_path = Path(f"{output}.meta.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["model"] == "openai/gpt-5-nano"
    assert metadata["dataset_digest"] == dataset_digest(ROWS)
    assert metadata["prompt_version"] == JUDGE_PROMPT_VERSION
    assert metadata["pricing_as_of"]
    assert metadata["completed"] == 3
    assert list(tmp_path.glob("*.tmp")) == []


def test_resume_refuses_incompatible_metadata_even_when_jsonl_is_absent(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    Path(f"{output}.meta.json").write_text(
        json.dumps(
            {
                "model": "openai/gpt-5-mini",
                "dataset_digest": dataset_digest(ROWS),
                "prompt_version": JUDGE_PROMPT_VERSION,
            }
        ),
        encoding="utf-8",
    )
    judge = RecordingJudge()

    with pytest.raises(ValueError, match="incompatible run identity"):
        run_judgments(_config(limit=1), ROWS, judge, output)

    assert judge.calls == []


def test_resume_refuses_an_unresolved_in_flight_paid_batch(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    Path(f"{output}.meta.json").write_text(
        json.dumps(
            {
                "model": "openai/gpt-5-nano",
                "dataset_digest": dataset_digest(ROWS),
                "prompt_version": JUDGE_PROMPT_VERSION,
                "in_flight": {
                    "item_ids": ["item-00"],
                    "reserved_cost_usd": "0.001",
                },
            }
        ),
        encoding="utf-8",
    )
    judge = RecordingJudge()

    with pytest.raises(ValueError, match="unresolved in-flight batch"):
        run_judgments(_config(limit=1), ROWS, judge, output)

    assert judge.calls == []


def test_runner_journals_the_retry_reservation_before_dispatch(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"

    class JournalCheckingJudge(RecordingJudge):
        def judge(self, model: str, items: Sequence[JudgeItem]) -> list[JudgeResult]:
            metadata = json.loads(Path(f"{output}.meta.json").read_text(encoding="utf-8"))
            assert metadata["in_flight"]["item_ids"] == [item.item_id for item in items]
            assert Decimal(metadata["in_flight"]["reserved_cost_usd"]) > 0
            return super().judge(model, items)

    summary = run_judgments(_config(limit=1), ROWS, JournalCheckingJudge(), output)

    assert summary.completed == 1
    assert json.loads(Path(f"{output}.meta.json").read_text())["in_flight"] is None


def test_provider_cost_overrun_is_persisted_and_stops_without_redispatch(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"
    judge = RecordingJudge(usage=JudgeUsage(100, 20, 0.01))

    summary = run_judgments(
        _config(limit=2, batch_size=1, max_cost_usd=Decimal("0.001")),
        ROWS,
        judge,
        output,
    )

    assert summary.termination_reason == "provider_cost_overrun"
    assert summary.completed == 1
    assert summary.cost_usd == Decimal("0.01")
    assert len(_jsonl(output)) == 1
    metadata = json.loads(Path(f"{output}.meta.json").read_text(encoding="utf-8"))
    assert metadata["cost_usd"] == "0.01"
    assert metadata["in_flight"] is None


def test_metadata_records_run_bounds_and_cumulative_requests(tmp_path: Path) -> None:
    output = tmp_path / "run.jsonl"

    run_judgments(_config(limit=1), ROWS, RecordingJudge(attempts=2), output)

    metadata = json.loads(Path(f"{output}.meta.json").read_text(encoding="utf-8"))
    assert metadata["started_at"]
    assert metadata["ended_at"]
    assert metadata["request_count"] == 2


def test_cli_summary_payload_serializes_every_decimal(tmp_path: Path) -> None:
    summary = run_judgments(_config(limit=1), ROWS, RecordingJudge(), tmp_path / "run.jsonl")
    stream = StringIO()

    _print_json(summary_payload(summary), stream=stream)

    payload = json.loads(stream.getvalue())
    assert payload["cost_usd"] == str(summary.cost_usd)
    assert payload["max_cost_usd"] == str(summary.max_cost_usd)


def test_authorized_run_artifacts_redact_provider_echoes_of_secret_shapes(tmp_path: Path) -> None:
    sentinel = "sk-or-v1-SENTINEL-DO-NOT-LEAK"
    canary = "IL-CANARY-TENANT-AABBCCDDEEFF0011"
    output = tmp_path / "run.jsonl"

    class SecretEchoJudge(RecordingJudge):
        def judge(self, model: str, items: Sequence[JudgeItem]) -> list[JudgeResult]:
            results = super().judge(model, items)
            return [
                replace(result, rationale=f"Provider echoed {sentinel} and {canary}.")
                for result in results
            ]

    run_judgments(_config(limit=1), ROWS, SecretEchoJudge(), output)

    persisted = output.read_text(encoding="utf-8") + Path(f"{output}.meta.json").read_text(
        encoding="utf-8"
    )
    assert sentinel not in persisted
    assert canary not in persisted
    assert "[REDACTED-API-KEY]" in persisted
    assert "[REDACTED-CANARY]" in persisted


def test_cli_without_opt_in_is_a_safe_plan_and_redacts_the_key(tmp_path: Path) -> None:
    sentinel = "sk-or-v1-SENTINEL-DO-NOT-LEAK"
    output = tmp_path / "judgments.jsonl"
    environment = {**os.environ, "OPENAI_API_KEY": sentinel}
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts/eval_manual_anchors.py"),
        "--model",
        "openai/gpt-5-nano",
        "--limit",
        "6",
        "--output",
        str(output),
    ]

    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    combined = completed.stdout + completed.stderr
    assert '"case_count": 6' in combined
    assert '"network_calls": 0' in combined
    assert '"dataset_digest":' in combined
    assert '"estimated_max_cost_usd":' in combined
    assert sentinel not in combined
    assert not output.exists()
    assert not Path(f"{output}.meta.json").exists()


def test_approved_model_prices_are_the_reviewed_openrouter_estimates() -> None:
    assert {
        "openai/gpt-5-nano": ModelPrice(Decimal("0.05"), Decimal("0.40")),
        "openai/gpt-5-mini": ModelPrice(Decimal("0.25"), Decimal("2.00")),
    } == MODEL_PRICES
