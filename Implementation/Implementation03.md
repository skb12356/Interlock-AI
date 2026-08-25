# Interface Contracts — frozen at Day 1, 11:00

Two people can only work simultaneously if the seam between them is nailed down before either starts. **These contracts are frozen for the whole sprint.** Additive changes (a new optional field) need a Slack message. Breaking changes need both people at the same keyboard.

Everything here lives in `interlock/core/` and is owned jointly. `starter/core_types.py` is the runnable version — copy it in as the first commit.

---

## 1. The seam

```
        Member A owns                          Member B owns
  ┌──────────────────────────┐         ┌──────────────────────────────┐
  │ gateway/  gate/          │         │ observer/  signals/          │
  │ interlock/ ledger/       │◄───────►│ risk/  lanec/  eval/         │
  │ console/  deploy/        │         │ artifacts/                   │
  └──────────────────────────┘         └──────────────────────────────┘
              ▲                                       ▲
              └──────────── core/ (shared) ───────────┘
                  types.py · policy.py · errors.py

  Contract 1: RiskEngine     (in-process Python protocol)   A calls, B implements
  Contract 2: Observer HTTP  (JSON over HTTP)               A calls, B implements
  Contract 3: SSE event spec (wire format)                  A implements, console consumes
  Contract 4: Policy YAML    (versioned file)               B defines schema, A loads
  Contract 5: DB schema      (SQL)                          A writes, B reads
```

**The unblocking trick:** B ships `StubRiskEngine` and a `mock-observer` FastAPI app on Day 1 before lunch. It returns scripted decisions driven by a header (`X-Interlock-Force: ungrounded@2`) so A can build and test the *entire* streaming, gate, repair, hold and interlock path with no GPU, no model, and no dependency on B's progress. B likewise gets recorded SSE fixtures from A on Day 1 so B can develop the risk engine offline. Neither person is ever blocked on the other after 11:00 on Day 1.

---

## 2. Contract 1 — `RiskEngine`

```python
# interlock/core/types.py   (see starter/core_types.py for the full file)
from typing import Protocol, Literal
from pydantic import BaseModel, Field

Action = Literal["L0_pass", "L1_annotate", "L2_repair", "L3_reroute", "L4_hold", "L5_block"]
Defect = Literal["ungrounded", "contradicted", "overconfident",
                 "unsafe_action", "pii_leak", "canary_leak", "biased"]
Reversibility = Literal["reversible", "costly", "irreversible"]
Provenance = Literal["system", "user", "retrieved_verified", "retrieved_untrusted", "tool_external"]

class Stakes(BaseModel):
    impact_inr: float                    # what it costs if this is wrong
    reversibility: Reversibility
    domain: str                          # 'loan_terms' | 'branch_info' | 'claims' | ...
    confidence: float = Field(ge=0, le=1) # how sure the stakes model is
    rationale: list[str] = []            # human-readable, shown in the console
    features: dict[str, float] = {}      # for replay + audit

class SignalReading(BaseModel):
    name: str
    raw: float
    prob: float | None = None            # calibrated; None until Day 2 PM
    latency_ms: float = 0.0
    span: tuple[int, int] | None = None   # char offsets into the sentence
    evidence: list[str] = []             # retrieved chunks that support/contradict

class RiskContext(BaseModel):
    request_id: str
    sentence_idx: int
    sentence: str
    answer_prefix: str
    question: str
    retrieved: list["Fragment"]
    stakes: Stakes
    already_emitted: bool                # True ⇒ L2/L3/L5 are not available
    remaining_deadline_ms: float

class LossRow(BaseModel):
    action: Action
    residual_harm: float; nuisance: float; compute: float; latency: float
    total: float
    available: bool = True
    unavailable_reason: str | None = None

class Decision(BaseModel):
    decision_id: str
    action: Action
    loss_table: list[LossRow]
    chosen_loss: float
    runner_up: Action | None
    margin: float                        # how close the call was — shown in the console
    probs: dict[Defect, float]
    why: list[str]                       # ordered, human-readable
    hard_rule: str | None = None         # set when a deterministic rule fired
    repair_hint: "RepairHint | None" = None
    policy_version: str; calib_version: str; probe_version: str
    inputs_digest: str
    latency_ms: float

class RepairHint(BaseModel):
    span: tuple[int, int]
    unsupported_claim: str
    evidence: list[str]
    suggested_max_tokens: int = 80

class RiskEngine(Protocol):
    async def evaluate(self, ctx: RiskContext) -> Decision: ...
    async def prefetch(self, request_id: str, question: str,
                       retrieved: list["Fragment"]) -> None: ...
    def health(self) -> dict: ...
```

**Guarantees B makes:** `evaluate` never raises (it returns a `Decision` with `action="L0_pass"` and `why=["degraded: <reason>"]` on any internal failure) and never exceeds `remaining_deadline_ms`. **Guarantees A makes:** `already_emitted` is accurate, and A honours whatever action comes back, including doing nothing.

## 3. Contract 2 — Observer HTTP

`POST /v1/observe` — one forward pass, no generation.

```jsonc
// request
{
  "request_id": "req_01H...",
  "context_key": "sha256:ab12...",   // gateway computes; observer caches the KV prefix under it
  "context": [                        // sent ONLY when context_key is a cache miss
    {"role": "system", "text": "...", "provenance": "system"},
    {"role": "retrieved", "text": "...", "provenance": "retrieved_untrusted", "doc_id": "d17"}
  ],
  "question": "Does prepaying my home loan attract a penalty?",
  "answer_prefix": "Under your agreement, ",
  "sentence": "Clause 7.4 imposes a 2% prepayment penalty.",
  "sentence_idx": 2,
  "want": ["probe", "verbal_uncertainty", "claims"],
  "deadline_ms": 120
}
// response
{
  "signals": [
    {"name": "probe_semantic_entropy", "raw": 0.71, "latency_ms": 11.4},
    {"name": "verbal_uncertainty",     "raw": 0.08, "latency_ms": 0.2},
    {"name": "minicheck_support",      "raw": 0.13, "latency_ms": 22.0,
     "span": [0, 44], "evidence": ["Clause 9.1 states no prepayment charge applies to floating-rate loans."]}
  ],
  "claims": [{"text": "Clause 7.4 imposes a 2% prepayment penalty", "label": "contradicted", "span": [0, 44]}],
  "probe_version": "p_2026_qwen3_1v7b_l18",
  "context_cached": true,
  "degraded": false
}
```

Rules: `200` always unless the request is malformed; internal failure → `200` with `"degraded": true` and an empty `signals` list. Hard timeout on A's side is `deadline_ms + 30`. `GET /health` returns `{model, probe_version, gpu, queue_depth, p95_ms}` — the governor polls it every 2 s.

`POST /v1/judge` (Lane C only, never called from the hot path) — generative adjudication for calibration anchoring and shadow verdicts. It is a separate route specifically so that a `grep` proves no hot path touches it.

## 4. Contract 3 — SSE wire format

We stay 100% OpenAI-compatible on the `data:` channel so any SDK works untouched. Interlock metadata rides on **named SSE events** that standard clients ignore and our console consumes.

```
event: interlock.stakes
data: {"impact_inr":40000,"reversibility":"costly","domain":"loan_terms","mode":"buffered"}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":"Under your agreement, "}}]}

event: interlock.signal
data: {"sentence_idx":2,"name":"minicheck_support","prob":0.87}

event: interlock.decision
data: {"decision_id":"dec_...","sentence_idx":2,"action":"L2_repair","chosen_loss":2491.0,
       "runner_up":"L3_reroute","counterfactual":"Clause 7.4 imposes a 2% prepayment penalty."}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":"your loan is on a floating rate, so no prepayment charge applies (Clause 9.1)."}}]}

event: interlock.hold
data: {"hold_id":"hld_...","kind":"tool_call","tool":"send_email","reason":"irreversible × untrusted_provenance"}

data: [DONE]
```

The `counterfactual` field is what makes the demo land: the console renders "what would have shipped" in red beside what did.

## 5. Contract 4 — Policy as code

One YAML file per tenant per industry, Pydantic-validated at boot, `policy_version` = sha256 of the file. Refuse to start on an invalid policy; keep the previous version live on hot-reload failure. Full example: `starter/policy_banking.yaml`.

```yaml
version: "banking-v3"
currency: INR
lambda_time_inr_per_second: 0.40        # what one second of a customer's wait is worth

stakes:
  default_impact_inr: 200
  domains:
    loan_terms:   {impact_inr: 40000, reversibility: costly}
    branch_info:  {impact_inr: 50,    reversibility: reversible}
    claims:       {impact_inr: 12000, reversibility: costly}
  multipliers:
    reversibility: {reversible: 1.0, costly: 2.5, irreversible: 8.0}
    defect: {ungrounded: 1.0, contradicted: 1.4, overconfident: 1.2,
             unsafe_action: 3.0, pii_leak: 6.0, canary_leak: 10.0, biased: 4.0}
    monetary_amount_over_inr: {10000: 1.5, 100000: 3.0}

nuisance_inr: {L0_pass: 0, L1_annotate: 0.5, L2_repair: 2.0,
               L3_reroute: 4.0, L4_hold: 220.0, L5_block: 900.0}

latency_ms: {L0_pass: 0, L1_annotate: 5, L2_repair: 280, L3_reroute: 1400,
             L4_hold: 240000, L5_block: 0}

efficacy:            # eff[action][defect] — PRIOR on day 1, MEASURED from the seeded set by day 5
  L2_repair:  {ungrounded: 0.80, contradicted: 0.85, overconfident: 0.60}
  L3_reroute: {ungrounded: 0.70, contradicted: 0.75, overconfident: 0.55}
  L4_hold:    {ungrounded: 0.98, contradicted: 0.98, unsafe_action: 0.99}

tools:
  send_email:      {reversibility: irreversible}
  transfer_funds:  {reversibility: irreversible, max_auto_inr: 0}
  lookup_balance:  {reversibility: reversible}
  default:         {reversibility: costly}

human_review: {cost_inr: 220, sla_minutes: 15}
guarantees:  {max_ungrounded_escape_rate: 0.01, confidence: 0.90}
```

**Why this file is a feature, not config:** every competitor encodes the same judgement inside an opaque `0.7`. This one is diffable, reviewable by risk and compliance, and stamped on every decision so an auditor can ask "which version priced this?" and get an answer.

## 6. Contract 5 — DB access rules

- **A owns writes.** B reads through DuckDB (`ATTACH 'interlock.db' (TYPE sqlite, READ_ONLY)`).
- All writes go through `ledger.record(...)` on a bounded `asyncio.Queue` drained by a single writer task. Nothing on the token path ever calls `sqlite3` directly.
- Schema changes: one Alembic-style migration file per change in `migrations/`, `NNN_description.sql`, applied at boot, idempotent. Two people, one file per change, no conflicts.

## 7. Definition of Done (applies to every task in the plan)

A task is done when **all five** hold:
1. Code merged to `main`, CI green (`ruff`, `mypy --strict` on `core/`, `pytest`).
2. At least one test that would fail if the feature regressed.
3. A trace/metric or console surface proves it works at runtime, not just in a test.
4. `make demo` still runs end to end.
5. One line in `CHANGELOG.md` written for the *other person*.

## 8. Working agreement

- **Trunk-based.** Branch names `a/<topic>`, `b/<topic>`. Nothing lives longer than 6 hours. `main` is always demo-able.
- **Two integration checkpoints daily: 13:30 and 21:30.** 15 minutes, screen-shared, run `make demo` together. Anything red is the only thing either of you works on until it's green.
- **Never edit the other person's directory.** Need a change there? Message them; if it's under 10 lines, they do it immediately.
- **The `core/` file is edited only during a checkpoint.** This one rule prevents ~90% of the merge pain in a 2-person sprint.
- **Timebox rule:** if you are 2× over an estimate, stop, take the documented fallback, and tell the other person. Fallbacks are pre-written in the plan for exactly this reason.