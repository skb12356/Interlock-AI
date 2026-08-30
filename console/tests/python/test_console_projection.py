from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from interlock.gateway.console_ws import (
    ConsoleHub,
    LiveConsoleSource,
    router,
    websocket_origin_allowed,
)


def test_websocket_origin_rejects_cross_site_browsers_and_allows_non_browser_clients() -> None:
    assert websocket_origin_allowed(None, "bank.example")
    assert websocket_origin_allowed("https://bank.example", "bank.example", scheme="wss")
    assert not websocket_origin_allowed("http://bank.example", "bank.example", scheme="wss")
    assert not websocket_origin_allowed("https://evil.example", "bank.example")
    assert not websocket_origin_allowed("http://localhost:5173", "127.0.0.1:8080")
    assert websocket_origin_allowed(
        "http://localhost:5173",
        "127.0.0.1:8080",
        configured="http://localhost:5173",
    )


class StaticSource:
    def status(self) -> dict[str, Any]:
        return {"source": "replay", "capabilities": {"decisions": True}}

    def decision(self, decision_id: str) -> dict[str, Any]:
        return {"decision_id": decision_id, "loss_table": []}

    def holds(self) -> list[dict[str, Any]]:
        return []

    def ledger_summary(self) -> dict[str, Any]:
        return {"request_count": 0, "economics": {"available": False}}

    def lane_c(self) -> dict[str, Any]:
        return {"n_pairs": 0, "by_axis": {}, "series": []}

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


@pytest.mark.asyncio
async def test_hub_delivers_live_events_in_sequence_to_a_slow_client() -> None:
    """A slow send must not let a later console event overtake the first one."""

    class SlowWebSocket:
        def __init__(self) -> None:
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()
            self.sequences: list[int] = []

        async def accept(self) -> None:
            return None

        async def send_json(self, event: dict[str, Any]) -> None:
            if event["seq"] == 1:
                self.first_started.set()
                await self.release_first.wait()
            self.sequences.append(event["seq"])

    hub = ConsoleHub(stream_id="stream-test")
    websocket = SlowWebSocket()
    await hub.connect(websocket)  # type: ignore[arg-type]

    hub.publish("interlock.stakes", {"impact_inr": 100})
    await websocket.first_started.wait()
    hub.publish("interlock.decision", {"decision_id": "dec_1"})
    await asyncio.sleep(0)
    websocket.release_first.set()

    for _ in range(10):
        if websocket.sequences == [1, 2]:
            break
        await asyncio.sleep(0)
    hub.disconnect(websocket)  # type: ignore[arg-type]

    assert websocket.sequences == [1, 2]


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

    def economics_snapshot(self) -> dict[str, Any]:
        return {
            "requests": 2,
            "regret_inr": 1.5,
            "rework_inr": 0.5,
            "net_value_inr": 12.0,
            "net_value_ci_inr": [9.0, 15.0],
            "net_value_samples": 2,
        }

    def lane_c_snapshot(self) -> dict[str, Any]:
        return {
            "n_pairs": 3,
            "by_axis": {"language": {"n": 3, "disparate": 1, "rate": 1 / 3}},
            "e_value": {"e_value": 1.25, "threshold": 20.0},
            "series": [{"index": 1, "e_value": 1.25}],
            "notes": ["observational projection"],
        }


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
        CREATE TABLE requests (request_id TEXT, overhead_ms REAL, session_id TEXT);
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
    connection.executemany(
        "INSERT INTO requests VALUES (?,?,?)",
        [("req_1", 10.0, "session_1"), ("req_2", 30.0, None)],
    )
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
    app = SimpleNamespace(
        state=SimpleNamespace(
            ledger=FakeLedger(connection, holds), console_publishers_integrated=True
        )
    )
    return LiveConsoleSource(app, artifacts_root=tmp_path)


def test_live_source_returns_complete_decisions_holds_and_ledger_summary(tmp_path: Path) -> None:
    source = live_source(tmp_path)

    status = source.status()
    assert status["capabilities"]["recent_events"]["available"] is True
    assert status["capabilities"]["economics"]["available"] is True
    assert status["capabilities"]["lane_c"]["available"] is True

    decision = source.decision("dec_1")
    assert decision["request_id"] == "req_1"
    assert decision["why"] == ["evidence conflicts"]
    assert len(decision["loss_table"]) == 6

    holds = source.holds()
    assert holds[0]["tool"] == "send_email"
    assert holds[0]["session_id"] == "session_1"
    assert holds[0]["evidence"] == ["untrusted source"]
    assert holds[0]["expired"] is True
    assert "resume_token" not in json.dumps(holds)

    summary = source.ledger_summary()
    assert summary["request_count"] == 2
    assert summary["spend_inr"] == 2.0
    assert summary["overhead_ms"] == {"mean": 20.0, "p95": 30.0}
    assert summary["action_counts"] == {"L2_repair": 1}
    assert summary["economics"] == {
        "available": True,
        "requests": 2,
        "regret_inr": 1.5,
        "rework_inr": 0.5,
        "net_value_inr": 12.0,
        "net_value_ci_inr": [9.0, 15.0],
        "net_value_samples": 2,
    }

    lane_c = source.lane_c()
    assert lane_c["n_pairs"] == 3
    assert lane_c["by_axis"]["language"]["rate"] == pytest.approx(1 / 3)


def test_live_source_marks_empty_economics_unavailable(tmp_path: Path) -> None:
    source = live_source(tmp_path)
    source.app.state.ledger.economics_snapshot = lambda: {
        "requests": 0,
        "net_value_inr": None,
        "net_value_ci_inr": None,
        "net_value_samples": 0,
        "upstream_spend_basis": "unmeasured",
    }

    assert source.status()["capabilities"]["economics"] == {
        "available": False,
        "reason": "no request-level net-value samples are available",
    }
    economics = source.ledger_summary()["economics"]
    assert economics["available"] is False
    assert economics["net_value_inr"] is None


def test_live_source_marks_partial_economics_unavailable(tmp_path: Path) -> None:
    source = live_source(tmp_path)
    source.app.state.ledger.economics_snapshot = lambda: {
        "requests": 2,
        "routing_savings_inr": 4.0,
        "regret_inr": None,
        "regret_samples": 0,
        "rework_inr": None,
        "rework_samples": 0,
        "net_value_inr": None,
        "net_value_ci_inr": None,
        "net_value_samples": 0,
        "upstream_spend_basis": "recorded",
    }

    status = source.status()["capabilities"]["economics"]
    assert status["available"] is False
    assert "regret" in status["reason"]
    assert "rework" in status["reason"]
    assert source.ledger_summary()["economics"]["available"] is False


def test_live_source_serves_only_allowlisted_json_artifacts(tmp_path: Path) -> None:
    source = live_source(tmp_path)
    (tmp_path / "calibration").mkdir()
    (tmp_path / "eval").mkdir()
    (tmp_path / "calibration" / "report.json").write_text('{"ece":0.1}', encoding="utf-8")
    (tmp_path / "eval" / "sensitivity.json").write_text(
        '{"detectors":[{"recall":0.9}]}', encoding="utf-8"
    )
    (tmp_path / "secret.json").write_text('{"secret":true}', encoding="utf-8")

    assert source.artifact("calibration/report.json") == {"ece": 0.1}
    assert source.artifact("eval/sensitivity.json") == {"detectors": [{"recall": 0.9}]}

    for name in ("secret.json", "../secret.json", "calibration/report.png"):
        with pytest.raises(HTTPException):
            source.artifact(name)


def test_lane_c_route_uses_the_projection_source() -> None:
    response = TestClient(projection_app()).get("/console/lanec")

    assert response.status_code == 200
    assert response.json() == {"n_pairs": 0, "by_axis": {}, "series": []}
