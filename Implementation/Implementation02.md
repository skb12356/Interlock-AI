# Interlock — System Design

**Status:** design frozen for the 5-day build
**Team:** 2 engineers (Member A = Stream & Enforcement, Member B = Signals & Decisions)
**Constraint that shapes every choice below:** 120 person-hours, one GPU, must run on a judge's laptop with `docker compose up`, must also be deployable to a public URL.

---

## 0. The honest scope call

The pitch describes ~15 mechanisms. In 120 person-hours you can build **six of them properly** and **stub four convincingly**. A half-built version of all fifteen loses to a complete version of six, because the panel probes depth, not breadth.

| Build for real (non-negotiable) | Build thin (demo-grade, honestly labelled) | Do not build |
|---|---|---|
| Streaming proxy + sentence-commit gate | Semantic cache (exact + embedding, no eviction policy) | Your own foundation model |
| Calibration (isotonic + conformal risk control) | Shadow replay (batch, offline, 5% sample) | Multi-tenant billing |
| Observer probe + claim-level verifier | Fairness twins (pre-recorded run is fine, say so) | K8s / Postgres / Redis |
| Expected-loss optimiser + intervention ladder | Drift / meta-monitor (weekly job, one chart) | Five verticals |
| Tool-call interlock with provenance | Rework attribution (heuristic retry detection) | SSO, RBAC beyond API keys |
| The ROI ledger + seeded eval harness | | Fine-tuning anything |

The ordering rule: **anything on the user's critical path is built for real; anything off it may be thin.** A judge can forgive a pre-recorded fairness run. A judge cannot forgive a gate that drops tokens.

---

## 1. Requirements

### 1.1 Functional

| # | Requirement | Verified by |
|---|---|---|
| F1 | Accept any OpenAI-compatible chat completion request (stream + non-stream) and proxy it to GPT / Claude / Gemini / local, unmodified | `tests/contract/test_openai_compat.py` replays 30 recorded SDK calls |
| F2 | Emit a *stakes estimate* (₹ impact + reversibility class) before the upstream model is called | Lane A trace span present on 100% of requests |
| F3 | Route to a model tier using that same stakes estimate | Router decision logged with the stakes id it consumed |
| F4 | Produce calibrated probabilities (not raw scores) for 7 defect classes | Reliability diagram, ECE < 0.05 on held-out |
| F5 | Choose exactly one action from L0–L5 by minimising expected loss in ₹ | Decision record contains the full loss table |
| F6 | Repair or hold a sentence **before** the user reads it | Gate never emits an uncommitted sentence; property test |
| F7 | Freeze an irreversible tool call whose triggering instruction has untrusted provenance | AgentDojo-style scenarios in the seeded set |
| F8 | Report waste (regret + rework), not just spend | Ledger endpoint returns regret with a bootstrap CI |
| F9 | Every decision is replayable from stored inputs | `interlock replay <decision_id>` reproduces the same action bit-for-bit |
| F10 | Degrade in a defined order under load, fail-open low-stakes / fail-closed high-stakes | Chaos test kills the observer mid-stream |

### 1.2 Non-functional

| Dimension | Target | Why this number |
|---|---|---|
| Added p95 latency (low stakes) | ≤ 40 ms | Lane A only; nothing else touches L0 traffic |
| Added p95 latency (high stakes) | ≤ 120 ms + one sentence | The buffer is the cost of being able to un-say |
| TTFT delta, p50 | ≤ 5 ms | Achieved by **not buffering L0 traffic at all** (§4.3) |
| Verification cost | ≤ 5% of model spend | Anthropic's shipped cascade runs ~1%; we budget 5× headroom |
| Throughput (demo) | 20 concurrent streams on 1×A10G | Enough for the traffic-burst scene |
| Availability posture | Gateway is on the request path → must fail open | Governor §4.6 |
| Cold start | `docker compose up` → healthy in < 90 s CPU profile | Judge runs it |

### 1.3 Constraints

- 5 days, 2 people, one GPU (A10G 24 GB or 4090). CPU-only fallback profile is mandatory.
- No component may require an account a judge doesn't have. One `.env` with provider keys, everything else local.
- Python 3.12 / FastAPI / SQLite+DuckDB / React+Vite. **One process per service, one file-backed store.**

---

## 2. High-level design

```
                 ┌────────────────────────── CONTROL PLANE (Lane C, async) ─────────────────────────┐
                 │  calibrator · shadow replay · fairness twins · deep-judge anchor · drift monitor   │
                 └───────▲──────────────────────────────────────────────────────────┬────────────────┘
                         │ new thresholds, new efficacy matrix, new regression tests │ sampled traces
   client                │                                                          ▼
 (OpenAI SDK) ──► GATEWAY ──► LANE A (pre-flight, 25 ms) ──► UPSTREAM MODEL ──► COMMIT GATE ──► client
   base_url=us            │      injection · pii · canary                │  ▲        │
                          │      stakes · cache · route                  │  │        └──► TOOL INTERLOCK ──► tool
                          │                                              │  │              (provenance + reversibility)
                          └──────────► LANE B (in-flight, concurrent) ───┘  │
                                        observer probe · claim verifier ────┘
                                        overconfidence index
                                                 │
                                                 ▼
                                          RISK ENGINE  →  Decision(action, loss_table, why)
                                          calibrate → price → argmin
```

**Three lanes, one controller.** Lane A is synchronous and cheap. Lane B runs *concurrently with token generation*, so its latency is hidden. Lane C never touches a live request; it exists to keep A and B honest and is the only place a generative judge is allowed to run.

### 2.1 Service topology

| Service | Language | Port | Owner | GPU | Why separate |
|---|---|---|---|---|---|
| `gateway` | Python / FastAPI | 8080 | A | no | The only thing on the critical path; must stay deployable on CPU |
| `observer` | Python / FastAPI + transformers | 8081 | B | yes | Model weights + probes; swappable, restartable, and mockable |
| `console` | React / Vite | 5173 | A (shell) / B (charts) | no | Static build, served by Caddy in prod |
| `caddy` | — | 80/443 | A | no | TLS, static, reverse proxy — one binary, no config server |

`gateway` and `observer` talk over HTTP/1.1 with a 150 ms hard timeout and a circuit breaker. **The gateway must be fully functional with the observer down** (§4.6). This boundary is what lets two people work in parallel without blocking each other.

### 2.2 Request lifecycle (happy path, streaming)

1. `POST /v1/chat/completions` with `stream=true`. Gateway assigns `trace_id`, `request_id`.
2. **Lane A (budget 25 ms, hard deadline 40 ms)** — run concurrently with `asyncio.gather`:
   - prompt-injection classifier on the last user turn *and* on every retrieved chunk
   - PII scan (Presidio) on the outbound prompt
   - canary registry check (tenant canaries present in system prompt / corpus?)
   - **stakes estimation** → `Stakes(impact_inr, reversibility, domain, confidence)`
   - semantic cache lookup (gated on stakes)
   - **router** → model tier, consuming the *same* `Stakes` object
3. Deterministic pre-block rules (canary in prompt echo, hard-blocked tool, tenant kill switch).
4. Upstream call opened. First token arrives → **Lane B starts**, running while the model writes:
   - every time a sentence boundary is detected, the observer gets `(context_hash, question, sentence, prefix)` and returns probe scores from one forward pass using a **cached KV prefix** (so per-sentence cost ≈ 30 tokens of prefill, not the whole context)
   - the claim verifier (MiniCheck-class) runs on sentences that carry a factual claim
   - the overconfidence index is computed from the verbal-uncertainty probe minus calibrated semantic uncertainty
5. **Commit gate** holds at most one sentence. When Lane B returns for sentence *n*, the risk engine prices the six actions and the gate executes the argmin: emit (L0/L1), regenerate the sentence with evidence injected (L2), restart with a stronger model (L3), park the response (L4), or terminate (L5).
6. On `finish_reason=tool_calls`, the **tool interlock** runs before anything executes: compute taint of the influencing context, look up the tool's reversibility class, apply the policy. Irreversible × untrusted → durable pending hold + review card.
7. Everything above is written to `decisions`, `signals`, `spend` in one transaction. 5% of requests are sampled into Lane C queues.

---

## 3. Data model

SQLite (WAL mode) is the system of record. DuckDB attaches the same file read-only for analytics so the console's ledger queries never block writers.

```sql
-- immutable append-only fact tables ------------------------------------------
CREATE TABLE requests (
  request_id     TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,
  tenant_id      TEXT NOT NULL,
  session_id     TEXT,
  ts             REAL NOT NULL,
  model_requested TEXT, model_served TEXT,
  route_reason   TEXT,               -- 'stakes_high' | 'router_mf' | 'cache_hit' | 'pinned'
  stakes_impact_inr REAL, stakes_reversibility TEXT, stakes_domain TEXT,
  prompt_tokens INT, completion_tokens INT, upstream_ms INT,
  overhead_ms   INT,                 -- our added latency, measured not estimated
  cache_hit     INT DEFAULT 0
);

CREATE TABLE signals (                -- one row per (request, sentence, signal)
  request_id TEXT, seq INT, sentence_idx INT,
  name       TEXT,                    -- 'probe_semantic_entropy' | 'minicheck' | 'verbal_uncertainty' | ...
  raw        REAL,                    -- detector output
  prob       REAL,                    -- calibrated P(defect)
  calib_version TEXT,
  latency_ms REAL,
  PRIMARY KEY (request_id, seq, name)
);

CREATE TABLE decisions (
  decision_id TEXT PRIMARY KEY, request_id TEXT, sentence_idx INT,
  action TEXT,                        -- L0..L5
  loss_table_json TEXT,               -- full 6-row expected-loss table, in ₹
  chosen_loss REAL, runner_up TEXT, margin REAL,
  policy_version TEXT, calib_version TEXT, probe_version TEXT,
  inputs_digest TEXT,                 -- sha256 of the exact inputs → replayability (F9)
  ts REAL
);

CREATE TABLE spend (
  request_id TEXT, component TEXT,    -- 'upstream' | 'observer' | 'verifier' | 'judge' | 'repair' | 'reroute'
  tokens INT, inr REAL
);

CREATE TABLE tool_calls (
  tool_call_id TEXT PRIMARY KEY, request_id TEXT,
  tool_name TEXT, args_json TEXT,
  reversibility TEXT,                 -- 'reversible' | 'costly' | 'irreversible'
  taint TEXT,                         -- max provenance label of influencing context
  verdict TEXT,                       -- 'allow' | 'hold' | 'block'
  hold_id TEXT, resolved_by TEXT, resolved_ts REAL
);

CREATE TABLE holds (                  -- durable, survives restart (F6/F7)
  hold_id TEXT PRIMARY KEY, request_id TEXT, kind TEXT,  -- 'response' | 'tool_call'
  payload_json TEXT, flagged_span TEXT, evidence_json TEXT,
  state TEXT,                         -- 'pending' | 'approved' | 'rejected' | 'expired'
  created_ts REAL, sla_deadline_ts REAL
);

CREATE TABLE rework_edges (           -- attribution graph
  child_request_id TEXT, parent_request_id TEXT,
  kind TEXT,                          -- 'retry' | 'regenerate' | 'human_escalation'
  confidence REAL, inr_charged REAL
);

CREATE TABLE shadow_runs (
  request_id TEXT, cheaper_model TEXT, verdict TEXT,  -- 'parity' | 'worse' | 'better'
  judged_by TEXT, inr_saved_if_switched REAL
);

CREATE TABLE fairness_pairs (
  pair_id TEXT, base_request_id TEXT, twin_request_id TEXT,
  attribute TEXT, decision_field TEXT, base_value TEXT, twin_value TEXT, delta REAL, ts REAL
);

CREATE TABLE labels (                 -- the human-labelled anchor set; drives calibration + meta-monitor
  item_id TEXT PRIMARY KEY, source TEXT, payload_json TEXT,
  gold_ungrounded INT, gold_contradicted INT, gold_unsafe INT, labeller TEXT, ts REAL
);
```

**Indexes:** `signals(request_id)`, `decisions(request_id)`, `spend(request_id)`, `rework_edges(parent_request_id)`, `requests(tenant_id, ts)`.

**Retention:** raw prompts are stored **hashed by default**; full text only when `INTERLOCK_STORE_PROMPTS=1` (demo mode). This is a five-line change that buys you a whole answer to the enterprise-privacy question.

---

## 4. Deep dives

### 4.1 Signal fusion and calibration (owner: B)

A raw detector score is not a probability. Pipeline per signal:

1. **Collect** ~1,500 (context, question, answer, sentence) items from the demo corpus with induced failures.
2. **Label** offline: 10-sample semantic entropy with bidirectional-NLI meaning clustering as the weak label; MiniCheck claim labels as a second view; **300 human-labelled** items as the anchor set (this is the step everyone skips and the one judges probe).
3. **Calibrate**: 5-fold cross-fitted `IsotonicRegression(out_of_bounds="clip")` per signal → `P(defect_d)`. Report ECE, Brier, reliability diagram.
4. **Fuse**: logistic regression over calibrated signals (not a hand-tuned weighted sum) → one `P(d)` per defect class, refit on the same folds.
5. **Threshold by conformal risk control, not by eye.** Choose the threshold vector λ by Learn-then-Test: keep λ only if the Hoeffding–Bentkus upper confidence bound on escape risk R(λ) is ≤ α at confidence 1−δ. With α = 0.01, δ = 0.10, n = 300 you can defend *"≤1% ungrounded escapes at 90% confidence"* as a statement about a bound, not a hope.

Artefacts written to `artifacts/calib/<version>/`: `isotonic_<signal>.joblib`, `fusion.joblib`, `lambda.json`, `reliability.png`, `metrics.json`. `calib_version` is stamped on every decision.

### 4.2 The objective (owner: B, consumed by A)

```
E[L(a)] =  Σ_d  P(d)·Impact_d·(1 − eff[a][d])      # ① residual harm
        +  (1 − P(any))·Nuisance(a)                 # ② false-alarm cost
        +  tokens(a)·price                          # ③ compute
        +  λ_time·Δlatency_ms(a)/1000               # ④ the user's time
```

Three details that make this real rather than decorative:

- **`Impact_d` is derived, not typed in.** `Impact_d = stakes.impact_inr × class_multiplier[d] × reversibility_multiplier[stakes.reversibility]`, all three from the versioned policy file. A reviewer diffs a YAML file, not a threshold buried in code.
- **`eff[a][d]` is measured, not guessed.** The efficacy matrix (how much of defect *d* does action *a* actually remove?) is estimated on the seeded eval set with a Wilson interval, and re-estimated nightly by Lane C. Day 1 ships a prior; Day 5 ships measurements. Say that on stage.
- **Hard constraints run before the argmin.** Deterministic rules (canary hit, blocked tool, taint × irreversible) short-circuit to L5/L4 with no model in the loop. The optimiser only chooses among actions that satisfy the conformal risk constraint. This is the difference between "a number picked the action" and "a number picked the *cheapest action that still meets the guarantee*".

Reference implementation: `starter/objective.py`.

### 4.3 The commit gate (owner: A) — and the TTFT problem nobody mentions

Naïve one-sentence buffering delays **every** first token by a full sentence. That destroys the p50 TTFT claim. The fix:

- **L0 traffic is not buffered at all.** If Lane A says stakes are low and pre-flight is clean, tokens pass through as they arrive; Lane B still runs and still records signals, but its only available action is post-hoc annotation. ~80% of traffic, TTFT delta ≈ 0.
- **Buffered mode engages on stakes ≥ threshold** (or when Lane A raises any flag). Then the gate holds exactly one sentence: sentence *n* streams to the user while sentence *n+1* is being generated and verified.
- **Mode can escalate mid-stream but never de-escalate.** If sentence 2 trips a signal in an unbuffered stream, the gate switches to buffered for the remainder. You cannot un-say sentence 1 — so the ladder for already-emitted text is capped at annotate/notify, and the console shows that honestly.

State machine: `starter/sentence_gate.py`, diagram in `diagrams/commit_gate_state_machine.mermaid`.

Sentence segmentation must be streaming-safe: a regex on `[.!?]` breaks on `Rs. 40,000`, `Clause 7.4`, `e.g.`. Use `pysbd` in incremental mode with a hard flush at 240 characters and at any `\n\n`. Write the abbreviation edge cases as unit tests on **day 1** — this is the single most common source of "the demo froze" on stage.

**Repair (L2) mechanics:** truncate the buffered sentence, re-prompt the *same* model with `{context, question, answer_prefix, the specific unsupported claim, the retrieved evidence}` and `max_tokens≈80`, `stop=["\n"]`; verify the replacement with the same verifier; if it fails twice, escalate to L3. Budget 150–400 ms, which is exactly what term ④ is charged for.

### 4.4 Tool interlock and provenance (owner: A)

Content filters gate on *words*. We gate on **what the action does, whether it can be undone, and where the instruction came from.**

Provenance is a lattice, propagated as taint over every context fragment:

```
TRUSTED_SYSTEM  <  USER  <  RETRIEVED_VERIFIED  <  RETRIEVED_UNTRUSTED  <  TOOL_OUTPUT_EXTERNAL
```

Every message chunk entering the context carries a label. When the model emits a tool call, we compute `taint = max(label of fragments that plausibly influenced this call)`. "Plausibly influenced" is approximated in tier order:

1. **Exact-argument provenance** (cheap, precise, catches the demo case): if any tool argument string appears verbatim — or with ≥0.9 token overlap — in an untrusted fragment, that fragment influenced the call. The `audit@external.com` in the poisoned PDF is caught here with a string search.
2. **Attention-free fallback:** if no argument match, take `max` over all untrusted fragments retrieved in this turn. Conservative by design.

Policy is then a lookup, not a model call:

| taint \ reversibility | reversible | costly | irreversible |
|---|---|---|---|
| SYSTEM / USER | allow | allow | allow if within user's limit, else hold |
| RETRIEVED_VERIFIED | allow | hold | hold |
| RETRIEVED_UNTRUSTED / TOOL_EXTERNAL | allow | hold | **freeze + review card** |

Reversibility is declared per tool in the policy file (`send_email`, `transfer_funds`, `delete_record`, `http_post_external` → irreversible). Unknown tools default to `costly`. Holds are durable rows: kill the process mid-hold, restart, and the review card is still there — that's the difference between a demo and a product, and it takes 40 minutes to implement.

### 4.5 The ledger (owner: A, math from B)

Three numbers, each with a defensible derivation:

- **Spend** — token counts × the price table, per component. Trivially correct.
- **Regret** = Σ over shadow-sampled requests of `price(model_served) − price(cheapest model that would have been acceptable)`, extrapolated to the population with a bootstrap CI. "Acceptable" is adjudicated by the Lane C deep judge on a 5% sample; the point estimate is `Σ p̂(acceptable | features) · Δprice` where `p̂` comes from a logistic model fit on shadow verdicts. Report the interval, always.
- **Rework** — a session graph. Edge `child → parent` when a user's next turn is within 120 s and has cosine ≥ 0.90 to their previous turn (retry), or the client sends `regenerate`, or a hold is escalated to a human. Charge the child's cost — and, for human escalation, the human's cost from the policy file — back to the parent `request_id`. Confidence is stored on the edge so you can show it as a range.

Net = savings(routing + cache + loop breaks) − verification cost − rework. When that number is negative, the oversight funds itself, which is the whole claim.

### 4.6 Load governor and degradation (owner: A)

Every request carries a deadline. The governor watches `p95 overhead_ms`, observer queue depth, and error rate over a 10 s sliding window, and moves through states monotonically down, with hysteresis on the way back up (30 s cooldown, so it doesn't flap on stage):

| State | Trigger | What turns off |
|---|---|---|
| `NORMAL` | — | nothing |
| `THIN` | p95 > 80 ms | Lane C sampling, shadow replay |
| `SHALLOW` | p95 > 120 ms | claim verifier; probe only |
| `PROBE_ONLY` | p95 > 200 ms or observer errors > 5% | repair (L2) disabled; ladder collapses to annotate/hold/block |
| `BYPASS` | observer circuit open | **fail open below the stakes threshold, fail closed above it** |

The last row is the sentence that separates a hackathon demo from something an enterprise would switch on — and it is one `if` statement plus a circuit breaker. Build it on day 3, demo it on day 5 by `docker kill interlock-observer` live.

### 4.7 What the console is for

It explains decisions that were already made. Four views, no configuration widgets:

1. **Live risk trail** — split screen, per-sentence signals, the loss table for the sentence that got intervened on, the counterfactual ("what would have shipped").
2. **Ledger** — spend vs regret vs rework, running net.
3. **Review queue** — pending holds (response + tool call), evidence, approve/reject.
4. **Evidence pack** — one click → a zip with every decision, its inputs, its loss table, its policy/calib versions and the reviewer. This is the EU AI Act Art. 12/14 artefact and it is generated from data you already store.

---

## 5. Scale and reliability

**Load estimate (demo):** 20 concurrent streams, ~500 output tokens each, ~8 sentences → 160 observer calls in flight. With KV-prefix caching, each is ~30 tokens of prefill ≈ 8–15 ms on an A10G at batch 8. Observer throughput ≈ 500 calls/s theoretical, ≈ 120/s with batching overhead. Comfortable margin; the bottleneck is upstream provider latency, not us.

**Where it breaks first (be able to say this out loud):**
1. Observer GPU saturation → governor drops to `SHALLOW`. Horizontal fix: N observer replicas behind the same HTTP contract; the gateway already load-balances via `httpx` transport pool.
2. SQLite write contention above ~2k writes/s → batch the `signals` insert per request (already done: one transaction per request) then migrate to Postgres. Migration is one SQLAlchemy URL.
3. Sentence-repair storms if a document corpus is broken → the governor's repair budget per minute caps it.

**Failure modes and behaviour**

| Failure | Behaviour | Test |
|---|---|---|
| Observer down | Circuit opens after 5 failures/10 s. Low-stakes → pass with `degraded: true` in the trace; high-stakes → hold | `tests/chaos/test_observer_kill.py` |
| Upstream provider 429/5xx | Exponential backoff with jitter, one retry, then a typed error to the client; retry cost charged to the ledger | contract test |
| Gate deadlock (model stops mid-sentence) | 8 s watchdog per sentence → flush buffer with an annotation | property test |
| SQLite locked | WAL + `busy_timeout=5000`; writes are fire-and-forget on a bounded queue, never on the token path | load test |
| Poisoned policy file | Policy loads through a Pydantic schema at boot; invalid policy → refuse to start, previous version stays live | unit test |

**SLOs, measured from day 1 (not day 5):** the six numbers on the pitch's §10 are Prometheus gauges from the first commit. `make eval` prints them. If you cannot print them on day 2 with garbage values, you will not have them on day 5 with real ones.

---

## 6. Trade-offs made explicit

| Decision | Chosen | Rejected | Cost of the choice |
|---|---|---|---|
| Observer topology | Small open model beside any API | Probe the generator's internals | ~2× the "obvious" latency; buys full model-agnosticism, which is the moat |
| Buffering | Stakes-gated (off for L0) | Buffer everything | Sentence 1 of a low-stakes answer can only be annotated, not repaired. Acceptable: low stakes is why it's low stakes |
| Store | SQLite + DuckDB | Postgres + Redis + ClickHouse | Single-node only. Correct for 5 days; migration is a URL |
| Thresholds | Conformal risk control | Tuned constants | Needs 300 labels (≈4 person-hours). This is the differentiator; do not cut it |
| Fusion | Logistic regression over calibrated signals | LLM judge on the hot path | Weaker on the tail; 500× cheaper and cannot fabricate |
| Provenance | String/overlap match + conservative fallback | Full dataflow taint (CaMeL-complete) | Over-blocks on paraphrased injections. Honest limitation; say it before the panel finds it |
| Repair | Same model, evidence-injected, verified twice | Rewrite with a stronger model always | Occasionally two round trips. Term ④ prices that correctly |

## 7. What I'd revisit as this grows

- Postgres + pgvector once there is more than one tenant writing concurrently.
- Replace the exact-argument provenance heuristic with a proper capability/dataflow interpreter (the real CaMeL design) once tool schemas stabilise.
- Train the router on your own regret ledger instead of a public checkpoint — the flywheel is that shadow verdicts are exactly router training data.
- Per-tenant efficacy matrices: `eff[a][d]` is domain-dependent and currently global.