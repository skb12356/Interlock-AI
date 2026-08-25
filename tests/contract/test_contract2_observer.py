"""Contract 2 — Observer HTTP (Implementation03 §3).

The observer is the one component that may need a GPU, so the gateway must be able to
call it, mock it, and survive it being dead. These tests pin the shape that makes that
possible.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from interlock.core.observer_api import (
    OBSERVE_TIMEOUT_MARGIN_MS,
    ClaimVerdict,
    ContextFragment,
    ObserveRequest,
    ObserveResponse,
    ObserverHealth,
    RawSignal,
)


def _request(**overrides: object) -> ObserveRequest:
    payload: dict[str, object] = {
        "request_id": "req_01H",
        "context_key": "sha256:ab12",
        "question": "Does prepaying my home loan attract a penalty?",
        "answer_prefix": "Under your agreement, ",
        "sentence": "Clause 7.4 imposes a 2% prepayment penalty.",
        "sentence_idx": 2,
        "want": ["probe", "verbal_uncertainty", "claims"],
        "deadline_ms": 120,
    }
    payload.update(overrides)
    return ObserveRequest.model_validate(payload)


def test_observe_request_round_trips_the_documented_example() -> None:
    request = _request(
        context=[
            {"role": "system", "text": "...", "provenance": "system"},
            {
                "role": "retrieved",
                "text": "...",
                "provenance": "retrieved_untrusted",
                "doc_id": "d17",
            },
        ]
    )
    assert ObserveRequest.model_validate_json(request.model_dump_json()) == request


def test_context_is_omitted_on_a_cache_hit() -> None:
    """This omission is the whole point of the KV-prefix cache: the first sentence pays
    full prefill, every later sentence pays ~30 tokens."""
    request = _request()
    assert request.context is None
    assert "context" not in request.model_dump(exclude_none=True)


def test_observe_response_round_trips_the_documented_example() -> None:
    response = ObserveResponse(
        signals=[
            RawSignal(name="probe_semantic_entropy", raw=0.71, latency_ms=11.4),
            RawSignal(name="verbal_uncertainty", raw=0.08, latency_ms=0.2),
            RawSignal(
                name="minicheck_support",
                raw=0.13,
                latency_ms=22.0,
                span=(0, 44),
                evidence=["Clause 9.1 states no prepayment charge applies."],
            ),
        ],
        claims=[
            ClaimVerdict(
                text="Clause 7.4 imposes a 2% prepayment penalty",
                label="contradicted",
                span=(0, 44),
            )
        ],
        probe_version="p_2026_qwen3_1v7b_l18",
        context_cached=True,
        degraded=False,
    )
    assert ObserveResponse.model_validate_json(response.model_dump_json()) == response


def test_internal_failure_is_reported_in_band_not_as_an_error() -> None:
    """A 5xx would force the gateway to distinguish 'observer broken' from 'network
    broken' on the token path. It must never have to."""
    response = ObserveResponse.degraded_response("probe weights not loaded")
    assert response.degraded is True
    assert response.signals == []
    assert response.degraded_reason == "probe weights not loaded"


def test_observer_never_emits_a_calibrated_probability() -> None:
    """ADR-002: the observer emits raw scores. Calibration lives on the risk-engine side
    where the isotonic artefacts and their version live. If RawSignal ever grows a
    'prob' field, calibration has been bypassed somewhere."""
    assert "prob" not in RawSignal.model_fields


def test_claim_verdict_carries_the_span_that_repair_aims_at() -> None:
    verdict = ClaimVerdict(text="...", label="contradicted", span=(0, 44))
    assert verdict.span == (0, 44)


@pytest.mark.parametrize("label", ["supported", "contradicted", "unfindable"])
def test_claim_labels(label: str) -> None:
    assert ClaimVerdict(text="x", label=label).label == label  # type: ignore[arg-type]


def test_unknown_claim_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ClaimVerdict(text="x", label="probably_fine")  # type: ignore[arg-type]


def test_want_lets_the_governor_ask_for_less_under_load() -> None:
    """SHALLOW drops the claim verifier; PROBE_ONLY keeps only the probe. Both are
    expressed by narrowing `want`, not by a second endpoint."""
    assert _request(want=["probe"]).want == ["probe"]


def test_health_carries_what_the_governor_needs() -> None:
    health = ObserverHealth(
        model="deberta-v3-base", probe_version="p_cpu_v1", gpu=False, queue_depth=3, p95_ms=18.2
    )
    restored = ObserverHealth.model_validate_json(health.model_dump_json())
    assert restored == health
    assert restored.gpu is False


def test_caller_timeout_margin_is_fixed_by_the_contract() -> None:
    assert OBSERVE_TIMEOUT_MARGIN_MS == 30


def test_wire_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ContextFragment(role="system", text="x", provenance="system", trust_me=True)  # type: ignore[call-arg]
