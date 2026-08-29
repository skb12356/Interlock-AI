"""Read-only console projections — owned by Person 2.

The console observes decisions that have already been made. Its websocket is push-only;
hold resolution remains on the existing audited REST routes in ``gateway.app``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import statistics
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

__all__ = ["ConsoleHub", "ConsoleSource", "LiveConsoleSource", "router"]

REPLAY_BUFFER = 200
ARTIFACTS_ROOT = Path(__file__).resolve().parents[2] / "artifacts"
ALLOWED_ARTIFACTS = frozenset(
    {
        "action_latency.json",
        "calibration/report.json",
        "calibration/lambda.json",
        "eval/report.json",
        "eval/report-guaranteed.json",
        "eval/sensitivity.json",
        "probes/curve.json",
    }
)


def _without_secrets(value: Any) -> Any:
    """Copy JSON-like data while removing resume tokens at every nesting level."""
    if isinstance(value, dict):
        return {key: _without_secrets(item) for key, item in value.items() if key != "resume_token"}
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


@dataclass
class ConsoleHub:
    """Non-blocking fan-out with a bounded, secret-free replay buffer."""

    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _clients: set[WebSocket] = field(default_factory=set, init=False)
    _recent: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=REPLAY_BUFFER), init=False
    )
    _seq: int = field(default=0, init=False)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        for event in self.recent():
            with contextlib.suppress(Exception):
                await websocket.send_json(event)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    def publish(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Append and schedule broadcast without awaiting on the request path."""
        self._seq += 1
        event: dict[str, Any] = {
            "stream_id": self.stream_id,
            "seq": self._seq,
            "event": event_name,
            "data": _without_secrets(payload),
            "ts": time.time(),
            "replayed": False,
        }
        if request_id is not None:
            event["request_id"] = request_id
        self._recent.append(event)
        if self._clients:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(self._broadcast(event))
        return event

    def recent(self, *, after: int = 0, stream_id: str | None = None) -> list[dict[str, Any]]:
        cursor = after if stream_id in (None, self.stream_id) else 0
        return [
            {**event, "replayed": True}
            for event in self._recent
            if cast(int, event["seq"]) > cursor
        ]

    def snapshot(self, *, after: int = 0, stream_id: str | None = None) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "latest_seq": self._seq,
            "events": self.recent(after=after, stream_id=stream_id),
        }

    async def _broadcast(self, event: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_json(event)
            except Exception:
                dead.append(client)
        for client in dead:
            self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)


class ConsoleSource(Protocol):
    def status(self) -> dict[str, Any]: ...

    def decision(self, decision_id: str) -> dict[str, Any]: ...

    def holds(self) -> list[dict[str, Any]]: ...

    def ledger_summary(self) -> dict[str, Any]: ...

    def lane_c(self) -> dict[str, Any]: ...

    def artifact(self, name: str) -> Any: ...


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, list) else []


@dataclass
class LiveConsoleSource:
    """Read-only projection over the already-mounted live gateway state."""

    app: Any
    artifacts_root: Path = ARTIFACTS_ROOT

    def _connection(self) -> Any:
        return self.app.state.ledger._require_connection()

    def status(self) -> dict[str, Any]:
        artifact_status = {
            name: (self.artifacts_root / name).is_file() for name in sorted(ALLOWED_ARTIFACTS)
        }
        ledger_stats = self.app.state.ledger.stats()
        publishers_integrated = bool(
            getattr(self.app.state, "console_publishers_integrated", False)
        )
        return {
            "source": "live",
            "replay": False,
            "health": {"ok": True, "ledger": ledger_stats},
            "capabilities": {
                "direct_stream": {"available": True},
                "recent_events": {
                    "available": publishers_integrated,
                    **(
                        {}
                        if publishers_integrated
                        else {
                            "reason": "Person 1 has not integrated live ConsoleHub publishers yet"
                        }
                    ),
                },
                "decision_details": {"available": True, "eventually_consistent": True},
                "holds": {"available": True, "approval_requires_token": True},
                "artifacts": artifact_status,
                "economics": {
                    "available": True,
                },
                "lane_c": {"available": True},
            },
        }

    def decision(self, decision_id: str) -> dict[str, Any]:
        row = (
            self._connection()
            .execute(
                "SELECT decision_id, request_id, sentence_idx, action, loss_table_json,"
                " chosen_loss, runner_up, margin, probs_json, why_json, hard_rule,"
                " policy_version, calib_version, probe_version, inputs_digest, latency_ms"
                " FROM decisions WHERE decision_id=?",
                (decision_id,),
            )
            .fetchone()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="decision is not available yet")
        record = dict(row)
        return {
            "decision_id": record["decision_id"],
            "request_id": record["request_id"],
            "sentence_idx": record["sentence_idx"],
            "action": record["action"],
            "loss_table": _json_list(record["loss_table_json"]),
            "chosen_loss": record["chosen_loss"],
            "runner_up": record["runner_up"],
            "margin": record["margin"],
            "probs": _json_object(record["probs_json"]),
            "why": _json_list(record["why_json"]),
            "hard_rule": record["hard_rule"],
            "policy_version": record["policy_version"] or "",
            "calib_version": record["calib_version"] or "",
            "probe_version": record["probe_version"] or "",
            "inputs_digest": record["inputs_digest"] or "",
            "latency_ms": record["latency_ms"] or 0.0,
        }

    def holds(self) -> list[dict[str, Any]]:
        now = time.time()
        cards: list[dict[str, Any]] = []
        for raw in self.app.state.ledger.pending_holds():
            payload = _json_object(raw.get("payload_json"))
            deadline = raw.get("sla_deadline_ts")
            cards.append(
                {
                    "hold_id": raw["hold_id"],
                    "request_id": raw["request_id"],
                    "kind": raw["kind"],
                    "reason": raw.get("reason") or "review required",
                    "tool": payload.get("name") or payload.get("tool"),
                    "sentence_idx": payload.get("sentence_idx"),
                    "payload": _without_secrets(payload),
                    "evidence": _json_list(raw.get("evidence_json")),
                    "flagged_span": raw.get("flagged_span"),
                    "state": "pending",
                    "created_ts": raw.get("created_ts"),
                    "sla_deadline_ts": deadline,
                    "expired": deadline is not None and float(deadline) <= now,
                }
            )
        return cards

    def ledger_summary(self) -> dict[str, Any]:
        connection = self._connection()
        request_count = int(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        spend = float(connection.execute("SELECT COALESCE(SUM(inr), 0) FROM spend").fetchone()[0])
        action_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT action, COUNT(*) FROM decisions GROUP BY action ORDER BY action"
            )
        }
        overheads = sorted(
            float(row[0])
            for row in connection.execute(
                "SELECT overhead_ms FROM requests WHERE overhead_ms IS NOT NULL"
            )
        )
        p95_index = max(0, math.ceil(0.95 * len(overheads)) - 1)
        overhead_summary = {
            "mean": statistics.fmean(overheads) if overheads else None,
            "p95": overheads[p95_index] if overheads else None,
        }
        return {
            "request_count": request_count,
            "spend_inr": spend,
            "action_counts": action_counts,
            "overhead_ms": overhead_summary,
            "economics": {
                "available": True,
                **self.app.state.ledger.economics_snapshot(),
            },
        }

    def lane_c(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.app.state.ledger.lane_c_snapshot())

    def artifact(self, name: str) -> Any:
        if name not in ALLOWED_ARTIFACTS:
            raise HTTPException(status_code=404, detail="artifact is not allowlisted")
        path = (self.artifacts_root / name).resolve()
        root = self.artifacts_root.resolve()
        if not path.is_file() or root not in path.parents or path.suffix != ".json":
            raise HTTPException(status_code=404, detail="artifact is unavailable")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail="artifact could not be read") from exc


router = APIRouter(prefix="/console", tags=["console"])


def _source(request: Request) -> ConsoleSource:
    configured = getattr(request.app.state, "console_source", None)
    return (
        cast(ConsoleSource, configured)
        if configured is not None
        else LiveConsoleSource(request.app)
    )


@router.websocket("/ws")
async def console_ws(websocket: WebSocket) -> None:
    hub: ConsoleHub = websocket.app.state.console_hub
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(websocket)


@router.get("/recent")
async def recent_events(
    request: Request,
    after: int = Query(default=0, ge=0),
    stream_id: str | None = None,
) -> dict[str, Any]:
    hub: ConsoleHub = request.app.state.console_hub
    return hub.snapshot(after=after, stream_id=stream_id)


@router.get("/status")
async def console_status(request: Request) -> dict[str, Any]:
    return _source(request).status()


@router.get("/decisions/{decision_id}")
async def decision_detail(decision_id: str, request: Request) -> dict[str, Any]:
    return _source(request).decision(decision_id)


@router.get("/holds")
async def console_holds(request: Request) -> dict[str, Any]:
    return {"holds": _source(request).holds()}


@router.get("/ledger/summary")
async def ledger_summary(request: Request) -> dict[str, Any]:
    return _source(request).ledger_summary()


@router.get("/lanec")
async def lane_c(request: Request) -> dict[str, Any]:
    return _source(request).lane_c()


@router.get("/artifacts/{name:path}")
async def console_artifact(name: str, request: Request) -> Any:
    return _source(request).artifact(name)
