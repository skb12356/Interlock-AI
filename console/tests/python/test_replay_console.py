from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_console import build_app, loss_table


def events(body: str) -> list[tuple[str | None, Any]]:
    parsed: list[tuple[str | None, Any]] = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        event_name: str | None = None
        data = ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        parsed.append((event_name, data if data == "[DONE]" else json.loads(data)))
    return parsed


def test_replay_stream_and_projection_share_one_secret_safe_contract() -> None:
    with TestClient(build_app(token_delay_s=0)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"scenario": "held", "messages": [], "stream": True},
        )

        assert response.headers["x-interlock-request-id"] == "req_replay_0001"
        trace = events(response.text)
        named = [(name, data) for name, data in trace if name]
        assert [name for name, _ in named] == [
            "interlock.stakes",
            "interlock.signal",
            "interlock.signal",
            "interlock.decision",
            "interlock.hold",
        ]
        decision_event = next(data for name, data in named if name == "interlock.decision")
        assert "loss_table" not in decision_event
        hold_event = next(data for name, data in named if name == "interlock.hold")
        assert hold_event["resume_token"] == "replay-token-0001"

        decision = client.get(f"/console/decisions/{decision_event['decision_id']}")
        assert decision.status_code == 200
        assert len(decision.json()["loss_table"]) == 6
        assert decision.json()["request_id"] == "req_replay_0001"

        queue = client.get("/console/holds")
        recent = client.get("/console/recent")
        assert queue.status_code == recent.status_code == 200
        assert "replay-token" not in queue.text
        assert "replay-token" not in recent.text
        assert queue.json()["holds"][0]["evidence"] == ["retrieved_untrusted content"]

        approved = client.post(
            f"/v1/holds/{hold_event['hold_id']}/approve",
            json={"resume_token": hold_event["resume_token"]},
        )
        assert approved.json()["state"] == "approved"
        assert client.get("/console/holds").json()["holds"] == []


@pytest.mark.parametrize(
    ("scenario", "action", "has_content"),
    [
        ("clean", "L0_pass", True),
        ("scene1", "L2_repair", True),
        ("held", "L4_hold", True),
        ("blocked", "L5_block", False),
    ],
)
def test_all_replay_scenarios_are_deterministic(
    scenario: str, action: str, has_content: bool
) -> None:
    with TestClient(build_app(token_delay_s=0)) as client:
        first = client.post(
            "/v1/chat/completions",
            json={"scenario": scenario, "messages": [], "stream": True},
        )
        second = client.post(
            "/v1/chat/completions",
            json={"scenario": scenario, "messages": [], "stream": True},
        )

        first_events = events(first.text)
        decision = next(data for name, data in first_events if name == "interlock.decision")
        content = [
            data
            for name, data in first_events
            if name is None and isinstance(data, dict) and data.get("choices")
        ]
        assert decision["action"] == action
        assert bool(content) is has_content
        assert first.headers["x-interlock-request-id"] == "req_replay_0001"
        assert second.headers["x-interlock-request-id"] == "req_replay_0002"

        detail = client.get(f"/console/decisions/{decision['decision_id']}").json()
        assert detail["loss_table"] == loss_table(action, decision["chosen_loss"])


def test_replay_status_ledger_and_artifacts_are_projection_routes() -> None:
    with TestClient(build_app(token_delay_s=0)) as client:
        client.post(
            "/v1/chat/completions",
            json={"scenario": "clean", "messages": [], "stream": True},
        )

        status = client.get("/console/status").json()
        assert status["source"] == "replay"
        assert status["replay"] is True
        assert status["capabilities"]["economics"]["available"] is False

        ledger = client.get("/console/ledger/summary").json()
        assert ledger["request_count"] == 1
        assert ledger["action_counts"] == {"L0_pass": 1}

        artifact = client.get("/console/artifacts/calibration/lambda.json")
        assert artifact.status_code == 200
        assert artifact.json()["intervention_rate"] == 1.0
        assert client.get("/console/artifacts/calibration/dataset.json").status_code == 404


def test_loss_table_is_repeatable_and_uses_the_frozen_action_names() -> None:
    first = loss_table("L2_repair", 10.0)
    assert first == loss_table("L2_repair", 10.0)
    assert [row["action"] for row in first] == [
        "L0_pass",
        "L1_annotate",
        "L2_repair",
        "L3_reroute",
        "L4_hold",
        "L5_block",
    ]
