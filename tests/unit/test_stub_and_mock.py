"""The stub risk engine and the mock observer — the Day 1 unblocking artefacts.

These two are what let the entire enforcement path be built with no GPU, no weights and
no detectors. Their contract conformance therefore matters more than most: if the stub
diverges from the Protocol, the D3-B4 swap stops being a one-line change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interlock.core.policy import Policy, load_policy
from interlock.core.types import Fragment, RiskContext, RiskEngine, Stakes
from interlock.observer.mock_server import MOCK_PROBE_VERSION, create_mock_observer
from interlock.risk.stub import ForceDirective, StubRiskEngine

POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "banking.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(POLICY_PATH)


@pytest.fixture
def engine(policy: Policy) -> StubRiskEngine:
    return StubRiskEngine(policy=policy)


def _ctx(
    *,
    request_id: str = "req_1",
    sentence_idx: int = 2,
    impact: float = 40_000,
    reversibility: str = "costly",
    domain: str = "loan_terms",
    already_emitted: bool = False,
) -> RiskContext:
    return RiskContext(
        request_id=request_id,
        sentence_idx=sentence_idx,
        sentence="Clause 7.4 imposes a 2% prepayment penalty.",
        answer_prefix="Under your agreement, ",
        question="Does prepaying my home loan attract a penalty?",
        retrieved=[Fragment(text="Clause 9.1 ...", provenance="retrieved_verified", doc_id="d17")],
        stakes=Stakes(
            impact_inr=impact,
            reversibility=reversibility,  # type: ignore[arg-type]
            domain=domain,
            confidence=0.9,
        ),
        already_emitted=already_emitted,
        remaining_deadline_ms=120.0,
    )


# --------------------------------------------------------------------------- #
# Header parsing -- a malformed debug header must never fail a request
# --------------------------------------------------------------------------- #


def test_parses_the_documented_form() -> None:
    directive = ForceDirective.parse("ungrounded@2")
    assert directive == ForceDirective(defect="ungrounded", sentence_idx=2, prob=0.9)


def test_parses_an_explicit_probability() -> None:
    directive = ForceDirective.parse("contradicted@1:0.55")
    assert directive is not None
    assert directive.prob == 0.55


@pytest.mark.parametrize(
    "raw",
    ["", "garbage", "ungrounded", "notadefect@2", "ungrounded@x", "ungrounded@2:9", "@2"],
)
def test_malformed_headers_are_ignored_not_raised(raw: str) -> None:
    assert ForceDirective.parse(raw) is None


# --------------------------------------------------------------------------- #
# Contract 1 conformance -- this is what makes the D3-B4 swap one line
# --------------------------------------------------------------------------- #


def test_the_stub_satisfies_the_risk_engine_protocol(engine: StubRiskEngine) -> None:
    assert isinstance(engine, RiskEngine)


async def test_an_unforced_sentence_passes(engine: StubRiskEngine) -> None:
    """The stub has no detectors, so an unforced sentence carries no defect probability
    and the cheapest action is to do nothing."""
    decision = await engine.evaluate(_ctx())
    assert decision.action == "L0_pass"
    assert decision.probs == {}


def test_a_nonzero_baseline_would_intervene_on_everything_at_high_stakes(
    policy: Policy,
) -> None:
    """Documents the sensitivity the false-intervention target (<= 2%) has to discipline.

    At Rs.40,000 impact even a 0.1% chance of being ungrounded puts Rs.100 of expected
    harm against a repair costing Rs.2.18 that removes 80% of it -- so the optimiser
    repairs *every* sentence. Arithmetically correct, operationally unusable. The real
    defences are the conformal feasibility filter (D3-B1) and measured efficacy
    (D3-B6); this test exists so that if either regresses, someone notices here first.
    """
    from interlock.risk.objective import choose_action

    choice = choose_action(
        probs={"ungrounded": 0.001},
        stakes=Stakes(
            impact_inr=40_000, reversibility="costly", domain="loan_terms", confidence=0.9
        ),
        policy=policy,
    )
    assert choice.action != "L0_pass"


async def test_a_forced_defect_produces_an_intervention(engine: StubRiskEngine) -> None:
    """The Day 1 exit criterion: X-Interlock-Force: ungrounded@2 -> a real L2 decision."""
    engine.arm("req_1", "ungrounded@2")
    decision = await engine.evaluate(_ctx(impact=200, reversibility="reversible", domain="general"))
    assert decision.action == "L2_repair"


async def test_the_force_applies_only_to_the_targeted_sentence(engine: StubRiskEngine) -> None:
    engine.arm("req_1", "ungrounded@2")
    assert (await engine.evaluate(_ctx(sentence_idx=1))).action == "L0_pass"
    assert (await engine.evaluate(_ctx(sentence_idx=2))).action != "L0_pass"


async def test_the_force_applies_only_to_the_armed_request(engine: StubRiskEngine) -> None:
    """Two concurrent streams must not contaminate each other."""
    engine.arm("req_1", "ungrounded@2")
    assert (await engine.evaluate(_ctx(request_id="req_2"))).action == "L0_pass"


async def test_disarming_clears_the_directive(engine: StubRiskEngine) -> None:
    engine.arm("req_1", "ungrounded@2")
    engine.disarm("req_1")
    assert (await engine.evaluate(_ctx())).action == "L0_pass"


# --------------------------------------------------------------------------- #
# Real loss table, scripted probabilities
# --------------------------------------------------------------------------- #


async def test_the_loss_table_is_real_and_complete(engine: StubRiskEngine) -> None:
    """The probabilities are fake; the arithmetic behind them is not."""
    engine.arm("req_1", "ungrounded@2")
    decision = await engine.evaluate(_ctx())
    assert len(decision.loss_table) == 6
    for row in decision.loss_table:
        assert row.total == pytest.approx(
            row.residual_harm + row.nuisance + row.compute + row.latency
        )


async def test_the_decision_is_stamped_with_its_policy_version(
    engine: StubRiskEngine, policy: Policy
) -> None:
    """So an auditor can ask which version priced this."""
    decision = await engine.evaluate(_ctx())
    assert decision.policy_version == policy.policy_version
    assert decision.policy_version.startswith("banking-v4@sha256:")


async def test_the_decision_is_replayable(engine: StubRiskEngine) -> None:
    """Same inputs, same digest (F9)."""
    first = await engine.evaluate(_ctx())
    second = await engine.evaluate(_ctx())
    assert first.inputs_digest == second.inputs_digest
    assert first.decision_id != second.decision_id


async def test_a_different_sentence_yields_a_different_digest(engine: StubRiskEngine) -> None:
    assert (await engine.evaluate(_ctx(sentence_idx=1))).inputs_digest != (
        await engine.evaluate(_ctx(sentence_idx=2))
    ).inputs_digest


async def test_stakes_still_drive_the_action_in_the_stub(engine: StubRiskEngine) -> None:
    """The same forced 0.9 probability produces different actions at different stakes --
    proving the stub exercises the real objective rather than a scripted action."""
    engine.arm("req_1", "ungrounded@2:0.9")
    high = await engine.evaluate(_ctx(impact=40_000, reversibility="costly"))
    engine.arm("req_2", "ungrounded@2:0.9")
    low = await engine.evaluate(
        _ctx(request_id="req_2", impact=200, reversibility="reversible", domain="general")
    )
    assert high.action != low.action


# --------------------------------------------------------------------------- #
# Hard rules and degradation
# --------------------------------------------------------------------------- #


async def test_a_canary_leak_is_a_deterministic_block(engine: StubRiskEngine) -> None:
    """No model in the loop, and it fires even at trivial stakes (invariant 6)."""
    engine.arm("req_1", "canary_leak@2")
    decision = await engine.evaluate(
        _ctx(impact=50, reversibility="reversible", domain="branch_info")
    )
    assert decision.action == "L5_block"
    assert decision.hard_rule == "canary_leak"


async def test_an_unsafe_action_holds_for_a_human(engine: StubRiskEngine) -> None:
    engine.arm("req_1", "unsafe_action@2")
    decision = await engine.evaluate(_ctx())
    assert decision.action == "L4_hold"
    assert decision.hard_rule == "untrusted_irreversible_tool"


async def test_the_engine_never_raises(policy: Policy) -> None:
    """Contract 1's central guarantee. A broken policy reference must degrade to a pass,
    not propagate an exception onto the token path."""
    broken = StubRiskEngine(policy=policy)
    broken.policy = None  # type: ignore[assignment]
    decision = await broken.evaluate(_ctx())
    assert decision.action == "L0_pass"
    assert decision.why and decision.why[0].startswith("degraded:")


async def test_an_already_emitted_sentence_cannot_be_repaired(engine: StubRiskEngine) -> None:
    engine.arm("req_1", "ungrounded@2")
    decision = await engine.evaluate(_ctx(already_emitted=True))
    assert decision.action not in {"L2_repair", "L3_reroute", "L5_block"}


async def test_prefetch_and_health(engine: StubRiskEngine) -> None:
    await engine.prefetch("req_1", "q", [])
    health = engine.health()
    assert health["ok"] is True
    assert health["engine"] == "stub"


# --------------------------------------------------------------------------- #
# The mock observer -- Contract 2
# --------------------------------------------------------------------------- #


@pytest.fixture
def observer() -> TestClient:
    return TestClient(create_mock_observer())


def _observe_payload(sentence: str, *, context_key: str = "sha256:ctx1") -> dict[str, object]:
    return {
        "request_id": "req_1",
        "context_key": context_key,
        "question": "Does prepaying my home loan attract a penalty?",
        "sentence": sentence,
        "sentence_idx": 2,
        "want": ["probe", "verbal_uncertainty", "claims"],
        "deadline_ms": 120,
    }


def test_mock_observer_is_healthy(observer: TestClient) -> None:
    body = observer.get("/health").json()
    assert body["ok"] is True
    assert body["gpu"] is False
    assert body["probe_version"] == MOCK_PROBE_VERSION


def test_a_clean_sentence_scores_low(observer: TestClient) -> None:
    body = observer.post("/v1/observe", json=_observe_payload("The branch opens at 9am.")).json()
    signals = {s["name"]: s["raw"] for s in body["signals"]}
    assert signals["probe_semantic_entropy"] < 0.2
    assert signals["minicheck_support"] > 0.8
    assert body["claims"][0]["label"] == "supported"


def test_a_scripted_hallucination_scores_high(observer: TestClient) -> None:
    body = observer.post(
        "/v1/observe", json=_observe_payload("Clause 7.4 [[HALLUCINATE]] imposes 2%.")
    ).json()
    signals = {s["name"]: s["raw"] for s in body["signals"]}
    assert signals["probe_semantic_entropy"] > 0.5
    assert body["claims"][0]["label"] == "contradicted"


def test_the_contradicted_claim_carries_a_span_and_evidence(observer: TestClient) -> None:
    """Without the span, L2 repair has nothing to aim at."""
    body = observer.post("/v1/observe", json=_observe_payload("[[HALLUCINATE]]")).json()
    assert body["claims"][0]["span"] is not None
    minicheck = next(s for s in body["signals"] if s["name"] == "minicheck_support")
    assert minicheck["evidence"]


def test_want_narrows_the_response(observer: TestClient) -> None:
    """The governor asks for less under load by narrowing `want`, not by switching
    endpoints."""
    payload = _observe_payload("[[HALLUCINATE]]")
    payload["want"] = ["probe"]
    body = observer.post("/v1/observe", json=payload).json()
    assert [s["name"] for s in body["signals"]] == ["probe_semantic_entropy"]
    assert body["claims"] == []


def test_the_prefix_cache_reports_hits(observer: TestClient) -> None:
    first = observer.post("/v1/observe", json=_observe_payload("one")).json()
    second = observer.post("/v1/observe", json=_observe_payload("two")).json()
    assert first["context_cached"] is False
    assert second["context_cached"] is True


def test_a_different_context_misses_the_cache(observer: TestClient) -> None:
    observer.post("/v1/observe", json=_observe_payload("one"))
    other = observer.post(
        "/v1/observe", json=_observe_payload("two", context_key="sha256:other")
    ).json()
    assert other["context_cached"] is False


def test_scripted_degradation_is_still_a_200(observer: TestClient) -> None:
    """The gateway must never have to distinguish a broken observer from a broken
    network on the token path."""
    response = observer.post("/v1/observe", json=_observe_payload("[[DEGRADE]]"))
    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.json()["signals"] == []


def test_a_malformed_request_is_rejected(observer: TestClient) -> None:
    """The one case where a non-200 is correct."""
    assert observer.post("/v1/observe", json={"request_id": "x"}).status_code == 422


def test_scripted_slowness_is_observable(observer: TestClient) -> None:
    """So the deadline path and the circuit breaker can be tested deterministically. A
    circuit breaker only ever tested against a healthy dependency is not tested."""
    import time

    start = time.monotonic()
    observer.post("/v1/observe", json=_observe_payload("[[SLOW:120]] hello"))
    assert (time.monotonic() - start) >= 0.10
