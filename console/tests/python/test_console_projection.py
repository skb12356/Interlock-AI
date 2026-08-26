from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from interlock.gateway.console_ws import ConsoleHub, LiveConsoleSource, router


class StaticSource:
    def status(self) -> dict[str, Any]:
        return {"source": "replay", "capabilities": {"decisions": True}}

    def decision(self, decision_id: str) -> dict[str, Any]:
        return {"decision_id": decision_id, "loss_table": []}

    def holds(self) -> list[dict[str, Any]]:
        return []

    def ledger_summary(self) -> dict[str, Any]:
        return {"request_count": 0, "economics": {"available": False}}

    def artifact(self, name: str) -> Any:
        return {"name": name}


def projection_app(*, hub: ConsoleHub | None = None, source: Any | None = None) -> FastAPI:
    app = FastAPI()
    app.state.console_hub = hub or ConsoleHub(stream_id="stream-test")
    app.state.console_source = source or StaticSource()
    app.include_router(router)
    return app


def test_hub_sequences_events_and_redacts_resume_tokens_before_buffering() -> None:
    hub = ConsoleHub(stream_id="stream-test")
    first = hub.publish(
        "interlock.hold",
        {
            "hold_id": "hld_1",
            "resume_token": "top-secret",
            "payload": {"resume_token": "nested-secret", "amount": 100},
        },
        request_id="req_1",
    )
    second = hub.publish("interlock.decision", {"decision_id": "dec_1"})

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert first["stream_id"] == "stream-test"
    assert first["request_id"] == "req_1"
    assert "secret" not in json.dumps(hub.recent())


def test_recent_cursor_and_websocket_replay_use_the_same_envelope() -> None:
    hub = ConsoleHub(stream_id="stream-test")
    hub.publish("interlock.stakes", {"impact_inr": 100}, request_id="req_1")
    hub.publish("interlock.signal", {"name": "grounding", "prob": 0.7}, request_id="req_1")
    client = TestClient(projection_app(hub=hub))

    recent = client.get("/console/recent", params={"after": 1, "stream_id": "stream-test"})
    assert recent.status_code == 200
    assert recent.json() == {
        "stream_id": "stream-test",
        "latest_seq": 2,
        "events": [
            {
                "stream_id": "stream-test",
                "seq": 2,
                "event": "interlock.signal",
                "data": {"name": "grounding", "prob": 0.7},
                "ts": recent.json()["events"][0]["ts"],
                "request_id": "req_1",
                "replayed": True,
            }
        ],
    }

    reset = client.get("/console/recent", params={"after": 99, "stream_id": "old-stream"})
    assert [event["seq"] for event in reset.json()["events"]] == [1, 2]

    with client.websocket_connect("/console/ws") as websocket:
        envelope = websocket.receive_json()
        assert envelope["seq"] == 1
        assert envelope["replayed"] is True


class FakeLedger:
    def __init__(self, connection: sqlite3.Connection, holds: list[dict[str, Any]]) -> None:
        self.connection = connection
        self._holds = holds

    def _require_connection(self) -> sqlite3.Connection:
        return self.connection

    def pending_holds(self) -> list[dict[str, Any]]:
        return self._holds

    def stats(self) -> dict[str, Any]:
        return {"written": 2, "dropped": 0}


def live_source(tmp_path: Path) -> LiveConsoleSource:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE decisions (
            decision_id TEXT, request_id TEXT, sentence_idx INTEGER, action TEXT,
            loss_table_json TEXT, chosen_loss REAL, runner_up TEXT, margin REAL,
            probs_json TEXT, why_json TEXT, hard_rule TEXT, policy_version TEXT,
            calib_version TEXT, probe_version TEXT, inputs_digest TEXT, latency_ms REAL
        );
        CREATE TABLE requests (request_id TEXT, overhead_ms REAL);
        CREATE TABLE spend (request_id TEXT, inr REAL);
        """
    )
    loss_rows = [
        {
            "action": f"L{index}_{name}",
            "residual_harm": float(index),
            "nuisance": 0.0,
            "compute": 0.0,
            "latency": 0.0,
            "total": float(index),
            "available": True,
            "unavailable_reason": None,
        }
        for index, name in enumerate(("pass", "annotate", "repair", "regenerate", "hold", "block"))
    ]
    connection.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "dec_1",
            "req_1",
            2,
            "L2_repair",
            json.dumps(loss_rows),
            2.0,
            "L1_annotate",
            0.5,
            json.dumps({"ungrounded": 0.7}),
            json.dumps(["evidence conflicts"]),
            None,
            "policy-v1",
            "calib-v1",
            "probe-v1",
            "abc123",
            14.0,
        ),
    )
    connection.executemany("INSERT INTO requests VALUES (?,?)", [("req_1", 10.0), ("req_2", 30.0)])
    connection.executemany("INSERT INTO spend VALUES (?,?)", [("req_1", 1.25), ("req_2", 0.75)])
    holds = [
        {
            "hold_id": "hld_1",
            "request_id": "req_1",
            "kind": "tool_call",
            "payload_json": json.dumps({"name": "send_email", "amount": 100}),
            "flagged_span": "recipient",
            "evidence_json": json.dumps(["untrusted source"]),
            "state": "pending",
            "resume_token": "must-not-leak",
            "reason": "external side effect",
            "created_ts": 10.0,
            "sla_deadline_ts": 20.0,
        }
    ]
    app = SimpleNamespace(state=SimpleNamespace(ledger=FakeLedger(connection, holds)))
    return LiveConsoleSource(app, artifacts_root=tmp_path)


def test_live_source_returns_complete_decisions_holds_and_ledger_summary(tmp_path: Path) -> None:
    source = live_source(tmp_path)

    decision = source.decision("dec_1")
    assert decision["request_id"] == "req_1"
    assert decision["why"] == ["evidence conflicts"]
    assert len(decision["loss_table"]) == 6

    holds = source.holds()
    assert holds[0]["tool"] == "send_email"
    assert holds[0]["evidence"] == ["untrusted source"]
    assert holds[0]["expired"] is True
    assert "resume_token" not in json.dumps(holds)

    summary = source.ledger_summary()
    assert summary["request_count"] == 2
    assert summary["spend_inr"] == 2.0
    assert summary["overhead_ms"] == {"mean": 20.0, "p95": 30.0}
    assert summary["action_counts"] == {"L2_repair": 1}
    assert summary["economics"]["available"] is False


def test_live_source_serves_only_allowlisted_json_artifacts(tmp_path: Path) -> None:
    source = live_source(tmp_path)
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "report.json").write_text('{"ece":0.1}', encoding="utf-8")
    (tmp_path / "secret.json").write_text('{"secret":true}', encoding="utf-8")

    assert source.artifact("calibration/report.json") == {"ece": 0.1}

    for name in ("secret.json", "../secret.json", "calibration/report.png"):
        with pytest.raises(HTTPException):
            source.artifact(name)
