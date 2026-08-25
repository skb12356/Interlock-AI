"""Contract 1 — the frozen seam between Stream & Enforcement and Signals & Decisions.

FROZEN. Field names and types here are a contract (Implementation03 §2). Additive
changes (a new *optional* field) are allowed; breaking changes are not made casually,
because every other module in the system compiles against this file.

The two guarantees that make the seam work:

* **Signals & Decisions guarantees** ``RiskEngine.evaluate`` never raises — on any internal
  failure it returns a ``Decision`` with ``action="L0_pass"`` and ``why=["degraded: ..."]`` —
  and never exceeds ``RiskContext.remaining_deadline_ms``.
* **Stream & Enforcement guarantees** ``RiskContext.already_emitted`` is accurate, and
  whatever action comes back is honoured, including doing nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ACTIONS",
    "DEFECTS",
    "PROVENANCE_ORDER",
    "Action",
    "Decision",
    "Defect",
    "Fragment",
    "GateMode",
    "LossRow",
    "Provenance",
    "RepairHint",
    "Reversibility",
    "RiskContext",
    "RiskEngine",
    "SignalReading",
    "Stakes",
    "max_provenance",
]

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #

#: The intervention ladder. Ordered cheapest-and-weakest to most-drastic; the
#: optimiser picks exactly one by minimising expected loss.
Action = Literal[
    "L0_pass",
    "L1_annotate",
    "L2_repair",
    "L3_reroute",
    "L4_hold",
    "L5_block",
]

#: The seven defect classes we price. Each carries a multiplier in the policy file.
Defect = Literal[
    "ungrounded",
    "contradicted",
    "overconfident",
    "unsafe_action",
    "pii_leak",
    "canary_leak",
    "biased",
]

#: Whether the consequence of being wrong can be undone. Multiplies Impact.
Reversibility = Literal["reversible", "costly", "irreversible"]

#: The provenance lattice, ordered least- to most-tainted. A tool call inherits the
#: maximum label over the fragments that plausibly influenced it (ADR-007).
Provenance = Literal[
    "system",
    "user",
    "retrieved_verified",
    "retrieved_untrusted",
    "tool_external",
]

#: Whether the commit gate is holding a sentence back. Escalates, never de-escalates
#: (ADR-003): low-stakes traffic streams unbuffered so TTFT is unchanged.
GateMode = Literal["unbuffered", "buffered"]

ACTIONS: tuple[Action, ...] = (
    "L0_pass",
    "L1_annotate",
    "L2_repair",
    "L3_reroute",
    "L4_hold",
    "L5_block",
)

DEFECTS: tuple[Defect, ...] = (
    "ungrounded",
    "contradicted",
    "overconfident",
    "unsafe_action",
    "pii_leak",
    "canary_leak",
    "biased",
)

#: Ascending taint order. Index into this to compare two provenance labels.
PROVENANCE_ORDER: tuple[Provenance, ...] = (
    "system",
    "user",
    "retrieved_verified",
    "retrieved_untrusted",
    "tool_external",
)


def max_provenance(labels: Iterable[Provenance]) -> Provenance:
    """Return the most-tainted label in ``labels``, or ``"system"`` if empty.

    The join operation of the provenance lattice. An empty set yields the bottom
    element: it is only ever called with fragments that were actually in context, so
    "nothing influenced this" means "nothing untrusted influenced this".
    """
    ranked = [PROVENANCE_ORDER.index(label) for label in labels]
    return PROVENANCE_ORDER[max(ranked)] if ranked else "system"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class _Frozen(BaseModel):
    """Base for contract models: extra fields rejected so drift fails loudly."""

    model_config = ConfigDict(extra="forbid")


class Fragment(_Frozen):
    """One labelled piece of context.

    Every chunk entering the context carries a provenance label at ingestion, which
    is what the tool interlock later joins over. ``doc_id`` is what a citation points
    at, so L1 annotate and L2 repair both need it populated for retrieved fragments.
    """

    text: str
    provenance: Provenance
    role: str = "retrieved"
    doc_id: str | None = None
    score: float | None = None  # retrieval similarity, when it came from the index
    #: The policy domain this document belongs to, labelled at ingestion alongside
    #: provenance. The stakes model reads it, so what was actually *retrieved* prices
    #: the request rather than what the question claimed to be about.
    #: (Additive optional field, permitted by the Contract 1 change rule.)
    domain: str | None = None


class Stakes(_Frozen):
    """What it costs if this particular request is wrong.

    The single estimate that drives *both* budgets — the router's spend and the
    guardrail's scrutiny (Contribution 1). Produced deterministically from the policy
    file, never by an LLM (ADR-005), which is why ``rationale`` can always be shown.
    """

    impact_inr: float  # what it costs if this is wrong
    reversibility: Reversibility
    domain: str  # 'loan_terms' | 'branch_info' | 'claims' | ...
    confidence: float = Field(ge=0, le=1)  # how sure the stakes model is
    rationale: list[str] = Field(default_factory=list)  # human-readable, for the console
    features: dict[str, float] = Field(default_factory=dict)  # for replay + audit


class SignalReading(_Frozen):
    """One detector's output for one sentence.

    ``raw`` is whatever the detector emitted; ``prob`` is that value after isotonic
    calibration. **Only ``prob`` may be used in expected-loss arithmetic** — a raw
    score has no units (ADR-002). ``prob`` is None when the signal is uncalibrated or
    when the detector was dropped for missing its deadline.
    """

    name: str
    raw: float
    prob: float | None = None  # calibrated
    latency_ms: float = 0.0
    span: tuple[int, int] | None = None  # char offsets into the sentence
    evidence: list[str] = Field(default_factory=list)  # chunks that support/contradict


class RiskContext(_Frozen):
    """Everything the risk engine is given about one sentence."""

    request_id: str
    sentence_idx: int
    sentence: str
    answer_prefix: str
    question: str
    retrieved: list[Fragment]
    stakes: Stakes
    already_emitted: bool  # True => L2/L3/L5 are not available
    remaining_deadline_ms: float


class LossRow(_Frozen):
    """One row of the expected-loss table, in rupees.

    The four terms of the objective (Implementation02 §4.2):
    ``residual_harm`` = Σ P(d)·Impact_d·(1 − eff[a][d]) — the harm that survives;
    ``nuisance`` = (1 − P(any))·Nuisance(a) — the cost of a false alarm;
    ``compute`` = tokens(a)·price; ``latency`` = λ_time·Δlatency_ms(a)/1000.

    Rows are returned for *every* action, including unavailable ones — the table is
    the explanation, so an action that could not be taken must show why.
    """

    action: Action
    residual_harm: float
    nuisance: float
    compute: float
    latency: float
    total: float
    available: bool = True
    unavailable_reason: str | None = None


class RepairHint(_Frozen):
    """What L2 repair aims at.

    Without a span there is nothing to repair, which is why the claim verifier must
    return the offending character offsets and not merely a label.
    """

    span: tuple[int, int]
    unsupported_claim: str
    evidence: list[str] = Field(default_factory=list)
    suggested_max_tokens: int = 80


class Decision(_Frozen):
    """One chosen action, with the whole table that justified it.

    ``hard_rule`` is set when a deterministic rule short-circuited the optimiser
    (canary hit, blocked tool, irreversible × untrusted). When it is set, no model was
    in the loop — that is the point of ADR-008.

    ``inputs_digest`` is the sha256 over the exact inputs, which is what makes a
    decision replayable bit-for-bit (F9).
    """

    decision_id: str
    action: Action
    loss_table: list[LossRow]
    chosen_loss: float
    runner_up: Action | None = None
    margin: float = 0.0  # how close the call was — shown in the console
    probs: dict[Defect, float] = Field(default_factory=dict)
    why: list[str] = Field(default_factory=list)  # ordered, human-readable
    hard_rule: str | None = None  # set when a deterministic rule fired
    repair_hint: RepairHint | None = None
    policy_version: str = ""
    calib_version: str = ""
    probe_version: str = ""
    inputs_digest: str = ""
    latency_ms: float = 0.0


# --------------------------------------------------------------------------- #
# The Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class RiskEngine(Protocol):
    """The seam itself. Stream & Enforcement calls it; Signals & Decisions implements it.

    Two implementations satisfy this, structurally and interchangeably:
    ``StubRiskEngine`` (header-driven, ships first so the enforcement path can be built
    with no GPU and no model) and ``RealRiskEngine``. Swapping them is a one-line
    change to the dependency wiring — that is the whole point of freezing this.
    """

    async def evaluate(self, ctx: RiskContext) -> Decision:
        """Price every action and return the argmin. Never raises; never overruns."""
        ...

    async def prefetch(
        self,
        request_id: str,
        question: str,
        retrieved: list[Fragment],
    ) -> None:
        """Warm the observer's KV prefix before the first sentence arrives."""
        ...

    def health(self) -> dict[str, object]:
        """Liveness plus the versions stamped on decisions."""
        ...
