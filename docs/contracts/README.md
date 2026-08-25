# The five frozen contracts

Frozen at Day 1. Additive changes (a new **optional** field) are fine. Breaking changes are
not made casually — every module in the system compiles against these.

| # | Contract | Direction | Lives in | Pinned by |
|---|---|---|---|---|
| 1 | `RiskEngine` (in-process Python Protocol) | Stream & Enforcement calls, Signals & Decisions implements | `interlock/core/types.py` | `tests/contract/test_contract1_types.py` |
| 2 | Observer HTTP (JSON over HTTP) | Stream & Enforcement calls, Signals & Decisions implements | `interlock/core/observer_api.py` | `tests/contract/test_contract2_observer.py` |
| 3 | SSE event spec (wire format) | Stream & Enforcement implements, console consumes | `interlock/core/sse.py` | `tests/contract/test_contract3_sse.py` |
| 4 | Policy YAML (versioned file) | Signals & Decisions defines schema, Stream & Enforcement loads | `interlock/core/policy.py`, `policies/*.yaml` | *(D1-B4)* |
| 5 | DB schema (SQL) | Stream & Enforcement writes, Signals & Decisions reads | `migrations/NNN_*.sql` | *(D1-A4)* |

---

## Contract 1 — `RiskEngine`

```python
class RiskEngine(Protocol):
    async def evaluate(self, ctx: RiskContext) -> Decision: ...
    async def prefetch(self, request_id: str, question: str,
                       retrieved: list[Fragment]) -> None: ...
    def health(self) -> dict[str, object]: ...
```

**Signals & Decisions guarantees:** `evaluate` **never raises** — on any internal failure it
returns a `Decision` with `action="L0_pass"` and `why=["degraded: <reason>"]` — and never
exceeds `ctx.remaining_deadline_ms`.

**Stream & Enforcement guarantees:** `ctx.already_emitted` is accurate, and whatever action
comes back is honoured, including doing nothing.

Two implementations satisfy this structurally and interchangeably: `StubRiskEngine`
(header-driven, ships first so the enforcement path can be built with no GPU and no model)
and `RealRiskEngine`. Swapping them at D3-B4 is a one-line change to the dependency wiring.

### Why the loss table is always complete

`Decision.loss_table` carries a row for **every** action, including unavailable ones, each
with an `unavailable_reason`. The table *is* the explanation. An action that could not be
taken must still show why it could not — otherwise the console is asserting rather than
explaining.

---

## Contract 2 — Observer HTTP

### `POST /v1/observe` — one forward pass, no generation

Request and response models: `ObserveRequest` / `ObserveResponse`.

Three rules the gateway depends on:

1. **200 always**, unless the request itself is malformed. An internal failure is reported
   in-band as `degraded=true` with an empty `signals` list — never as a 5xx. The gateway
   must not have to distinguish "the observer is broken" from "the network is broken" on
   the token path.
2. The caller's hard timeout is `deadline_ms + 30` (`OBSERVE_TIMEOUT_MARGIN_MS`).
3. `context` is sent **only** when `context_key` misses the observer's KV-prefix cache. On
   a hit, the sentence costs ~30 tokens of prefill instead of the whole context — the
   difference between ~200 ms and ~12 ms per sentence.

The observer emits **raw** scores (`RawSignal`), never probabilities. Calibration happens
on the risk-engine side, where the isotonic artefacts and their version live (ADR-002). If
`RawSignal` ever grows a `prob` field, calibration has been bypassed somewhere.

`want` lets the governor ask for less under load — `SHALLOW` drops `claims`, `PROBE_ONLY`
keeps only `probe` — without needing a second endpoint.

### `GET /health`

Returns `ObserverHealth` (`model`, `probe_version`, `gpu`, `queue_depth`, `p95_ms`). The
load governor polls this every 2 s to decide its degradation state.

### `POST /v1/judge` — Lane C only

Generative adjudication for calibration anchoring and shadow verdicts. It is a **separate
route specifically so that `grep` proves no hot path touches it** (invariant 8).

---

## Contract 3 — SSE wire format

The `data:` channel stays 100% OpenAI-compatible so any SDK works untouched. Interlock
metadata rides on four **named** events: `interlock.stakes`, `interlock.signal`,
`interlock.decision`, `interlock.hold`.

```
event: interlock.stakes
data: {"impact_inr":40000,"reversibility":"costly","domain":"loan_terms","mode":"buffered"}

data: {"id":"chatcmpl-...","choices":[{"delta":{"content":"Under your agreement, "}}]}

event: interlock.decision
data: {"decision_id":"dec_...","sentence_idx":2,"action":"L2_repair","chosen_loss":2491.0,
       "runner_up":"L3_reroute","counterfactual":"Clause 7.4 imposes a 2% prepayment penalty."}

data: [DONE]
```

`counterfactual` is what makes the demo land: the console renders "what would have shipped"
in red beside what actually did.

Passthrough chunks are forwarded **exactly as the upstream serialised them**. Re-encoding a
provider's JSON is a needless way to break a client's parser.

### One caveat we do not paper over

The frozen contract asserts that standard clients ignore named events. That holds for the
EventSource spec and for raw SSE readers, but it is **not** universally true of SDK stream
decoders — some cast every `data:` payload to a chunk type regardless of the event name.

So emission is gated by `StreamOptions` (default: on, per the contract), and any client may
opt out with the `X-Interlock-Events: off` request header, receiving a pure OpenAI stream;
the console then reads the same decisions over its websocket. This is an **additive escape
hatch, not a change to the wire format**, and it never changes what the gate does — only
what the client sees. `tests/contract/test_openai_compat.py` (D1-A1) verifies the
compatibility claim against a real SDK rather than trusting it.
