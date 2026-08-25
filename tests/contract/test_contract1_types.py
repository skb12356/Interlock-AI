"""Contract 1 — the frozen RiskEngine seam (Implementation03 §2).

These tests exist to fail loudly if the contract drifts. Two people (or one person
across five days) can only work against this seam if it does not move underneath them.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest
from pydantic import ValidationError

from interlock.core.types import (
    ACTIONS,
    DEFECTS,
    PROVENANCE_ORDER,
    Action,
    Decision,
    Defect,
    Fragment,
    LossRow,
    Provenance,
    RepairHint,
    Reversibility,
    RiskContext,
    RiskEngine,
    SignalReading,
    Stakes,
    max_provenance,
)

# --------------------------------------------------------------------------- #
# The enumerations are load-bearing: the ladder, the defect classes, the lattice
# --------------------------------------------------------------------------- #


def test_intervention_ladder_is_exactly_six_rungs_in_order() -> None:
    """L0..L5 from Interlock-v2.pdf §06. The optimiser prices every one of these."""
    assert get_args(Action) == (
        "L0_pass",
        "L1_annotate",
        "L2_repair",
        "L3_reroute",
        "L4_hold",
        "L5_block",
    )
    assert get_args(Action) == ACTIONS


def test_defect_classes_are_exactly_the_seven_we_price() -> None:
    assert get_args(Defect) == (
        "ungrounded",
        "contradicted",
        "overconfident",
        "unsafe_action",
        "pii_leak",
        "canary_leak",
        "biased",
    )
    assert get_args(Defect) == DEFECTS


def test_reversibility_classes() -> None:
    assert get_args(Reversibility) == ("reversible", "costly", "irreversible")


def test_provenance_lattice_is_ordered_least_to_most_tainted() -> None:
    """system < user < retrieved_verified < retrieved_untrusted < tool_external."""
    assert get_args(Provenance) == PROVENANCE_ORDER
    assert PROVENANCE_ORDER.index("system") < PROVENANCE_ORDER.index("user")
    assert PROVENANCE_ORDER.index("user") < PROVENANCE_ORDER.index("retrieved_verified")
    assert PROVENANCE_ORDER.index("retrieved_verified") < PROVENANCE_ORDER.index(
        "retrieved_untrusted"
    )
    assert PROVENANCE_ORDER.index("retrieved_untrusted") < PROVENANCE_ORDER.index("tool_external")


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ((), "system"),
        (("system", "user"), "user"),
        (("user", "retrieved_untrusted", "system"), "retrieved_untrusted"),
        (("retrieved_verified", "tool_external"), "tool_external"),
        (("system",), "system"),
    ],
)
def test_max_provenance_is_the_lattice_join(
    labels: tuple[Provenance, ...], expected: Provenance
) -> None:
    assert max_provenance(labels) == expected


def test_max_provenance_is_conservative_on_the_poisoned_pdf_case() -> None:
    """One untrusted fragment taints the join, regardless of how much trusted context surrounds it."""
    labels: list[Provenance] = ["system", "user", "user", "retrieved_untrusted"]
    assert max_provenance(labels) == "retrieved_untrusted"


# --------------------------------------------------------------------------- #
# Round-trip serialisation: every model crosses a process boundary or a DB row
# --------------------------------------------------------------------------- #


def _stakes() -> Stakes:
    return Stakes(
        impact_inr=40000.0,
        reversibility="costly",
        domain="loan_terms",
        confidence=0.82,
        rationale=["retrieved domain=loan_terms", "monetary amount 40,000 detected"],
        features={"monetary_magnitude": 40000.0, "conversation_depth": 2.0},
    )


def _loss_table() -> list[LossRow]:
    return [
        LossRow(
            action=action,
            residual_harm=1.0,
            nuisance=0.5,
            compute=0.1,
            latency=0.2,
            total=1.8,
        )
        for action in ACTIONS
    ]


def test_stakes_round_trips() -> None:
    original = _stakes()
    assert Stakes.model_validate_json(original.model_dump_json()) == original


def test_signal_reading_round_trips_with_span() -> None:
    reading = SignalReading(
        name="minicheck_support",
        raw=0.13,
        prob=0.87,
        latency_ms=22.0,
        span=(0, 44),
        evidence=["Clause 9.1 states no prepayment charge applies to floating-rate loans."],
    )
    restored = SignalReading.model_validate_json(reading.model_dump_json())
    assert restored == reading
    assert restored.span == (0, 44)


def test_risk_context_round_trips() -> None:
    ctx = RiskContext(
        request_id="req_01H",
        sentence_idx=2,
        sentence="Clause 7.4 imposes a 2% prepayment penalty.",
        answer_prefix="Under your agreement, ",
        question="Does prepaying my home loan attract a penalty?",
        retrieved=[
            Fragment(text="Clause 9.1 ...", provenance="retrieved_verified", doc_id="d17"),
        ],
        stakes=_stakes(),
        already_emitted=False,
        remaining_deadline_ms=120.0,
    )
    assert RiskContext.model_validate_json(ctx.model_dump_json()) == ctx


def test_decision_round_trips_with_full_loss_table() -> None:
    decision = Decision(
        decision_id="dec_1",
        action="L2_repair",
        loss_table=_loss_table(),
        chosen_loss=2491.0,
        runner_up="L3_reroute",
        margin=616.0,
        probs={"ungrounded": 0.31, "contradicted": 0.12},
        why=["P(ungrounded)=0.31 at impact Rs.40,000", "repair removes 80% of ungrounded"],
        repair_hint=RepairHint(
            span=(0, 44),
            unsupported_claim="Clause 7.4 imposes a 2% prepayment penalty",
            evidence=["Clause 9.1 ..."],
        ),
        policy_version="banking-v3",
        calib_version="calib_2026_01",
        probe_version="p_2026_qwen3_1v7b_l18",
        inputs_digest="sha256:ab12",
        latency_ms=18.4,
    )
    restored = Decision.model_validate_json(decision.model_dump_json())
    assert restored == decision


# --------------------------------------------------------------------------- #
# Invariants the rest of the system relies on
# --------------------------------------------------------------------------- #


def test_loss_table_covers_every_action() -> None:
    """The table *is* the explanation, so every rung must be priced -- including
    unavailable ones, which must say why they were unavailable."""
    table = _loss_table()
    assert {row.action for row in table} == set(ACTIONS)


def test_unavailable_action_can_carry_its_reason() -> None:
    """Once a sentence is already emitted you cannot un-say it, so L2/L3/L5 drop out
    of the feasible set -- and the console shows that honestly (ADR-003)."""
    row = LossRow(
        action="L2_repair",
        residual_harm=0.0,
        nuisance=0.0,
        compute=0.0,
        latency=0.0,
        total=float("inf"),
        available=False,
        unavailable_reason="already_emitted",
    )
    assert row.available is False
    assert row.unavailable_reason == "already_emitted"


def test_stakes_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Stakes(impact_inr=1.0, reversibility="reversible", domain="x", confidence=1.5)
    with pytest.raises(ValidationError):
        Stakes(impact_inr=1.0, reversibility="reversible", domain="x", confidence=-0.1)


def test_contract_models_reject_unknown_fields() -> None:
    """Drift must fail loudly rather than being silently dropped on the wire."""
    with pytest.raises(ValidationError):
        Stakes(
            impact_inr=1.0,
            reversibility="reversible",
            domain="x",
            confidence=0.5,
            impact_usd=12.0,  # type: ignore[call-arg]
        )


def test_uncalibrated_signal_has_prob_none() -> None:
    """A raw score is not a probability (ADR-002). A detector dropped for missing its
    Lane A deadline is recorded with prob=None rather than being silently omitted."""
    assert SignalReading(name="injection", raw=0.4).prob is None


def test_mutable_defaults_are_not_shared_between_instances() -> None:
    a, b = _stakes(), _stakes()
    a.rationale.append("mutated")
    assert "mutated" not in b.rationale


# --------------------------------------------------------------------------- #
# The Protocol itself
# --------------------------------------------------------------------------- #


def test_risk_engine_protocol_shape() -> None:
    """Both the stub and the real engine must satisfy this, so swapping them at
    D3-B4 is a one-line change to the dependency wiring."""
    assert inspect.iscoroutinefunction(RiskEngine.evaluate)
    assert inspect.iscoroutinefunction(RiskEngine.prefetch)
    assert not inspect.iscoroutinefunction(RiskEngine.health)


def test_a_conforming_implementation_is_recognised() -> None:
    class Conforming:
        async def evaluate(self, ctx: RiskContext) -> Decision:
            return Decision(decision_id="d", action="L0_pass", loss_table=[], chosen_loss=0.0)

        async def prefetch(self, request_id: str, question: str, retrieved: list[Fragment]) -> None:
            return None

        def health(self) -> dict[str, object]:
            return {"ok": True}

    assert isinstance(Conforming(), RiskEngine)


def test_a_non_conforming_implementation_is_rejected() -> None:
    class MissingPrefetch:
        async def evaluate(self, ctx: RiskContext) -> Decision:  # pragma: no cover
            raise NotImplementedError

        def health(self) -> dict[str, object]:  # pragma: no cover
            return {}

    assert not isinstance(MissingPrefetch(), RiskEngine)
