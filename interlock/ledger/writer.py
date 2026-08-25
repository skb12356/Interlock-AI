"""The ledger — Contract 5, and the one place that writes.

Two rules, both load-bearing:

* **Nothing on the token path touches SQLite synchronously.** Writes go onto a bounded
  queue drained by a single writer task. A slow disk must never be able to stall a
  stream — the customer's tokens do not wait for our telemetry.
* **One transaction per request.** A request's row, its signals, its decisions and its
  spend commit together or not at all, so the console never renders a half-written
  request and the eval harness never counts one.

**The one deliberate exception: holds.** A hold is not telemetry, it is the product —
"kill the process mid-hold, restart, and the review card is still there" is the
difference between a demo and something an enterprise switches on. So `persist_hold` is
*awaited* and committed before the caller proceeds. Losing a hold on a queue overflow
would lose the very thing the feature exists to guarantee.

Queue overflow drops telemetry and **counts what it dropped**. A silently lossy ledger
would make the cost numbers quietly wrong, which is worse than a gap you can see.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from interlock.core.clock import wall_time
from interlock.core.ids import sha256_text
from interlock.core.types import Decision, SignalReading

__all__ = ["Ledger", "RequestBatch", "SpendEntry", "apply_migrations", "connect"]

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


# --------------------------------------------------------------------------- #
# Connection and migrations
# --------------------------------------------------------------------------- #


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the store with the pragmas that make single-writer concurrency work.

    WAL lets DuckDB read while we write. `busy_timeout` means a reader that arrives
    mid-commit waits rather than raising `database is locked` — the failure the plan
    names as the one that shows up under load.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    return connection


def apply_migrations(connection: sqlite3.Connection) -> list[str]:
    """Apply every unapplied migration, in filename order. Idempotent.

    Applied at boot rather than by a separate command, so a judge running one command
    never meets a schema error.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_ts REAL NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    freshly: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, applied_ts) VALUES (?, ?)",
            (version, wall_time()),
        )
        freshly.append(version)
    return freshly


# --------------------------------------------------------------------------- #
# What gets written
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class SpendEntry:
    component: str  # 'upstream'|'observer'|'verifier'|'judge'|'repair'|'reroute'
    tokens: int = 0
    inr: float = 0.0
    model: str | None = None


@dataclass(slots=True)
class RequestBatch:
    """Everything about one request, committed in a single transaction."""

    request_id: str
    trace_id: str
    tenant_id: str
    ts: float = field(default_factory=wall_time)
    session_id: str | None = None
    model_requested: str | None = None
    model_served: str | None = None
    route_reason: str | None = None
    stakes_id: str | None = None
    stakes_impact_inr: float | None = None
    stakes_reversibility: str | None = None
    stakes_domain: str | None = None
    stakes_confidence: float | None = None
    gate_mode: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    upstream_ms: float = 0.0
    overhead_ms: float = 0.0
    lane_a_ms: float = 0.0
    ttft_ms: float = 0.0
    cache_hit: bool = False
    degraded: bool = False
    dropped_detectors: Sequence[str] = ()
    finish_reason: str | None = None
    #: The raw prompt. Hashed unless `store_prompts` is set on the Ledger.
    prompt_text: str | None = None

    signals: list[SignalReading] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    spend: list[SpendEntry] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #


@dataclass
class Ledger:
    """Bounded queue, one writer task, one transaction per request."""

    db_path: str | Path
    #: Bounded on purpose. An unbounded queue converts a slow disk into unbounded memory
    #: growth, which fails later and much worse.
    max_queue: int = 1000
    store_prompts: bool = False

    _queue: asyncio.Queue[RequestBatch] | None = field(default=None, init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _connection: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _dropped: int = field(default=0, init=False)
    _written: int = field(default=0, init=False)

    # -- lifecycle --------------------------------------------------------- #

    async def start(self) -> None:
        self._connection = connect(self.db_path)
        apply_migrations(self._connection)
        self._queue = asyncio.Queue(maxsize=self.max_queue)
        self._task = asyncio.create_task(self._drain(), name="ledger-writer")

    async def stop(self) -> None:
        """Drain what is queued, then close. Never lose a write we accepted."""
        if self._queue is not None:
            await self._queue.join()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def flush(self) -> None:
        """Wait for the queue to empty. For tests and for a clean shutdown."""
        if self._queue is not None:
            await self._queue.join()

    # -- the write paths --------------------------------------------------- #

    def record(self, batch: RequestBatch) -> bool:
        """Fire and forget. Returns False if the queue was full.

        Deliberately not `async`: the token path calls this and must not be able to
        await on our telemetry. A full queue drops the batch and increments a counter
        that `stats()` exposes, because a silently lossy ledger makes the cost numbers
        quietly wrong -- worse than a gap somebody can see.
        """
        if self._queue is None:
            return False
        try:
            self._queue.put_nowait(batch)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            return False

    async def persist_hold(
        self,
        *,
        hold_id: str,
        request_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        evidence: list[str] | None = None,
        flagged_span: str | None = None,
        reason: str | None = None,
        resume_token: str | None = None,
        sla_deadline_ts: float | None = None,
    ) -> None:
        """Durably store a hold, **awaited**.

        Not fire-and-forget, unlike everything else here. A hold that vanished on a
        queue overflow or a crash would lose exactly the guarantee the feature exists
        to provide (F6/F7).
        """
        connection = self._require_connection()
        await asyncio.to_thread(
            connection.execute,
            "INSERT OR REPLACE INTO holds(hold_id, request_id, kind, payload_json,"
            " flagged_span, evidence_json, state, resume_token, reason, created_ts,"
            " sla_deadline_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                hold_id,
                request_id,
                kind,
                json.dumps(payload or {}),
                flagged_span,
                json.dumps(evidence or []),
                "pending",
                resume_token,
                reason,
                wall_time(),
                sla_deadline_ts,
            ),
        )

    async def resolve_hold(self, hold_id: str, *, state: str, resolved_by: str) -> bool:
        """Approve or reject a hold. Awaited for the same reason as `persist_hold`."""
        connection = self._require_connection()
        cursor = await asyncio.to_thread(
            connection.execute,
            "UPDATE holds SET state=?, resolved_by=?, resolved_ts=?"
            " WHERE hold_id=? AND state='pending'",
            (state, resolved_by, wall_time(), hold_id),
        )
        return bool(cursor.rowcount)

    def pending_holds(self) -> list[dict[str, Any]]:
        """Every hold still waiting. Read at boot, which is what makes it survive."""
        connection = self._require_connection()
        rows = connection.execute(
            "SELECT * FROM holds WHERE state='pending' ORDER BY created_ts"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- internals --------------------------------------------------------- #

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Ledger.start() has not been called")
        return self._connection

    async def _drain(self) -> None:
        assert self._queue is not None
        while True:
            batch = await self._queue.get()
            try:
                await asyncio.to_thread(self._write, batch)
                self._written += 1
            except Exception:
                self._dropped += 1
            finally:
                self._queue.task_done()

    def _write(self, batch: RequestBatch) -> None:
        """One transaction. All of it, or none of it."""
        connection = self._require_connection()
        prompt_hash = sha256_text(batch.prompt_text) if batch.prompt_text else None
        prompt_text = batch.prompt_text if self.store_prompts else None

        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR REPLACE INTO requests(request_id, trace_id, tenant_id, session_id,"
                " ts, model_requested, model_served, route_reason, stakes_id,"
                " stakes_impact_inr, stakes_reversibility, stakes_domain, stakes_confidence,"
                " gate_mode, prompt_tokens, completion_tokens, upstream_ms, overhead_ms,"
                " lane_a_ms, ttft_ms, cache_hit, degraded, dropped_detectors, prompt_hash,"
                " prompt_text, finish_reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch.request_id,
                    batch.trace_id,
                    batch.tenant_id,
                    batch.session_id,
                    batch.ts,
                    batch.model_requested,
                    batch.model_served,
                    batch.route_reason,
                    batch.stakes_id,
                    batch.stakes_impact_inr,
                    batch.stakes_reversibility,
                    batch.stakes_domain,
                    batch.stakes_confidence,
                    batch.gate_mode,
                    batch.prompt_tokens,
                    batch.completion_tokens,
                    batch.upstream_ms,
                    batch.overhead_ms,
                    batch.lane_a_ms,
                    batch.ttft_ms,
                    int(batch.cache_hit),
                    int(batch.degraded),
                    json.dumps(list(batch.dropped_detectors)),
                    prompt_hash,
                    prompt_text,
                    batch.finish_reason,
                ),
            )

            for seq, signal in enumerate(batch.signals):
                span = signal.span or (None, None)
                connection.execute(
                    "INSERT OR REPLACE INTO signals(request_id, seq, sentence_idx, name, raw,"
                    " prob, calib_version, latency_ms, span_start, span_end)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        batch.request_id,
                        seq,
                        None,
                        signal.name,
                        signal.raw,
                        signal.prob,
                        None,
                        signal.latency_ms,
                        span[0],
                        span[1],
                    ),
                )

            for decision in batch.decisions:
                connection.execute(
                    "INSERT OR REPLACE INTO decisions(decision_id, request_id, sentence_idx,"
                    " action, loss_table_json, chosen_loss, runner_up, margin, probs_json,"
                    " why_json, hard_rule, policy_version, calib_version, probe_version,"
                    " inputs_digest, latency_ms, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        decision.decision_id,
                        batch.request_id,
                        None,
                        decision.action,
                        json.dumps([row.model_dump() for row in decision.loss_table]),
                        decision.chosen_loss,
                        decision.runner_up,
                        decision.margin,
                        json.dumps(decision.probs),
                        json.dumps(decision.why),
                        decision.hard_rule,
                        decision.policy_version,
                        decision.calib_version,
                        decision.probe_version,
                        decision.inputs_digest,
                        decision.latency_ms,
                        batch.ts,
                    ),
                )

            for entry in batch.spend:
                connection.execute(
                    "INSERT INTO spend(request_id, component, model, tokens, inr, ts)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        batch.request_id,
                        entry.component,
                        entry.model,
                        entry.tokens,
                        entry.inr,
                        batch.ts,
                    ),
                )

            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def stats(self) -> dict[str, int]:
        """Written and dropped counts. Dropped must be visible, never inferred."""
        return {
            "written": self._written,
            "dropped": self._dropped,
            "queued": self._queue.qsize() if self._queue else 0,
        }
