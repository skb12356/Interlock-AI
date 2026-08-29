"""Contract smoke tests for the production observer process."""

from __future__ import annotations

from fastapi.testclient import TestClient

from interlock.observer.server import create_observer


def _payload() -> dict[str, object]:
    return {
        "request_id": "req_observer_test",
        "context_key": "sha256:observer-test",
        "context": [
            {
                "role": "retrieved",
                "text": "The branch opens at 9am.",
                "provenance": "retrieved_verified",
                "doc_id": "doc-1",
            }
        ],
        "question": "What time does the branch open?",
        "sentence": "The branch opens at 9am.",
        "sentence_idx": 0,
    }


def test_production_observer_implements_contract_without_model_weights() -> None:
    with TestClient(create_observer()) as client:
        response = client.post("/v1/observe", json=_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is False
        assert body["probe_version"] == "none"
        assert client.get("/health").json()["ok"] is True


def test_production_observer_keeps_context_warm() -> None:
    with TestClient(create_observer()) as client:
        first = client.post("/v1/observe", json=_payload()).json()
        second = client.post("/v1/observe", json={**_payload(), "context": None}).json()
        assert first["context_cached"] is False
        assert second["context_cached"] is True
