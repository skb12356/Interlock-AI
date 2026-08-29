"""Failure-injection checks for the deployment-critical degradation paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from interlock.core.errors import PolicyError
from interlock.core.policy import load_policy
from interlock.gateway.governor import Governor, GovernorState
from interlock.observer.server import create_observer
from interlock.signals.probe_signal import ProbeSignal


def test_observer_inference_failure_is_an_in_band_degraded_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenProbe:
        available = True
        version = "probe@test"
        encoder = SimpleNamespace(loaded=True, model_name="test")

        def score(self, *_args: object, **_kwargs: object) -> float:
            raise RuntimeError("injected observer failure")

    monkeypatch.setattr(ProbeSignal, "load", lambda *_args, **_kwargs: BrokenProbe())
    with TestClient(create_observer()) as client:
        response = client.post(
            "/v1/observe",
            json={
                "request_id": "req-chaos",
                "context_key": "sha256:chaos",
                "context": [],
                "question": "question",
                "sentence": "answer",
                "sentence_idx": 0,
            },
        )

    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.json()["signals"] == []


def test_observer_breaker_failure_degrades_before_bypass() -> None:
    governor = Governor()
    for _ in range(governor.breaker.failure_threshold):
        governor.breaker.record_failure()

    assert governor.observe(1.0) is GovernorState.SHALLOW
    assert not governor.allows("claims")


def test_malformed_policy_is_rejected_before_startup(tmp_path: Path) -> None:
    bad_policy = tmp_path / "malformed.yaml"
    bad_policy.write_text("version: [unclosed\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="not valid YAML"):
        load_policy(bad_policy)
