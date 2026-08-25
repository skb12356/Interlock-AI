# Interlock V2 — Master Implementation TODO

**Source of truth:** `Implementation/Implementation01.md` (5-day plan) · `Implementation02.md` (system design) · `Implementation03.md` (frozen contracts) · `Implementation04.md` (ADR-001…009) · `CLAUDE.md` (engineering principles) · `Interlock-v2.pdf` (design intent, target numbers).

**North-star metric:** Pre-Action Catch Rate ≥ 90%. **Six targets, measured not aspirational:** catch ≥ 90% · added p95 ≤ 120 ms (TTFT unchanged) · verification cost ≤ 5% of model spend · net spend ≈ −30% · ungrounded escapes ≤ 1% (conformal, 90% confidence) · false interventions ≤ 2%.

**Status legend:** `[ ] Pending` · `[~] In progress` · `[x] Completed` · `[!] Blocked` · `[-] Cut (per cut list)`

---

## Definition of Done — applies to EVERY task below

A task is done only when all five hold (Implementation03 §7):

1. Code committed, CI green: `ruff check`, `mypy --strict interlock/core`, `pytest`.
2. At least one test that would **fail if the feature regressed**.
3. A trace row, metric, or console surface proves it works **at runtime**, not just in a test.
4. `make demo` (or `scripts/demo.ps1`) still runs end to end.
5. One line in `CHANGELOG.md`.

Plus, per this execution mandate: `TODO.md` checkbox flipped with a timestamp line, and `STATE_CHECKPOINT.json` rewritten.

---

## Architectural invariants — never violated without a recorded ADR

1. **One stakes estimate, two budgets.** Router and guardrail consume the *same* `Stakes` object; provable from one trace.
2. **Decisions, not scores.** Every detector output terminates in an L0–L5 action chosen by expected-loss argmin. The console explains; it never asks.
3. **The commit gate is sacred.** Never cut, stubbed or bypassed. Nor is calibration.
4. **Fail open on low stakes, fail closed on high stakes.** Governor degradation order is defined, not emergent.
5. **Tool calls gated on action semantics** (what it does × reversibility × provenance), never on words.
6. **Canary match = deterministic L5.** No model in the loop.
7. **No generator internals, ever.** The observer probes its own residual stream. Model-agnosticism is the moat.
8. **No generative LLM judge on the hot path.** ~1% of traffic, offline, calibration anchor only (`/v1/judge` is a separate route so `grep` proves it).
9. **Scores are not probabilities until calibrated.** Isotonic → fusion → conformal threshold, before any rupee arithmetic.

---

# PHASE 0 — Environment reconciliation (blocking; not in the original plan)

The plan assumes 2 engineers, Docker, and a GPU. The audit of this machine found seven deltas that must be resolved before Day 1 Task 1. Each is recorded here as a deviation with its chosen resolution.

| ID | Finding | Resolution | Status |
|---|---|---|---|
| P0.1 | **Git root is `C:\Users\saksh`** — the entire home directory is a git repo; `AIC/` is not its own. Committing would pollute the home repo. | `git init` inside `AIC/`, add `.gitignore`. Do not touch the parent repo. | `[x] Completed` 2026-08-25 — git init at project root |
| P0.2 | **Docker not installed** (`docker` not on PATH). The plan mandates `docker compose up` as the judge's entry point. | **RULED (user, 2026-08-25): Docker dropped entirely.** No compose, no Dockerfiles. The one-command entry point becomes a native supervisor — `scripts/up.ps1` / `make up` — that launches gateway + observer + console as local processes with the same healthcheck semantics. Heavy/slow installs are deferred to the task that needs them: `pyproject` splits a light `core` dependency set from an `ml` extra. **Consequence to state on the slide: the judge runs `scripts/up.ps1`, not `docker compose up`.** Logged in `IMPLEMENTATION_STATUS.md`. | `[x] Resolved` |
| P0.3 | **No NVIDIA GPU** (`nvidia-smi` absent). | **ADR-006 CPU profile is promoted from fallback to the primary path.** Observer = DeBERTa-v3-base encoder on CPU with `output_hidden_states=True`, identical HTTP contract. The Qwen3 GPU profile stays implemented but untested here. This is the profile a judge runs anyway. | `[x] Completed` 2026-08-25 — recorded as D-002 |
| P0.4 | **Ollama installed** with `qwen3:8b`, `qwen3:4b`; OpenAI-compatible at `http://127.0.0.1:11434/v1`. | Use as the **upstream generator** → the whole system demos with zero API keys. Also the Lane C `/v1/judge` anchor. **Cannot serve the observer probe** — Ollama exposes no hidden states, so the observer must run under HF `transformers`. The provider-adapter interface keeps OpenAI/Anthropic swap-in for when keys arrive. | `[x] Completed` 2026-08-25 — recorded as D-003 |
| P0.5 | System Python is 3.13.1; the plan pins 3.12. | Pin `3.12.11` via `uv python pin` (already on disk). Avoids wheel gaps for `sqlite-vec`, `presidio`, `torch`. | `[x] Completed` 2026-08-25 — .python-version pinned 3.12.11 |
| P0.6 | **All `starter/` files referenced by the plan are missing** (`core_types.py`, `sentence_gate.py`, `objective.py`, `policy_banking.yaml`), as are `docs/01`, `docs/02`, `docs/05_deploy_runbook.md`, `diagrams/`. | Reconstruct from spec: Implementation03 §2 contains the complete `core/types.py`; §5 the complete banking policy YAML; Implementation02 §4.2 the objective; §4.3 the gate state machine. The two JPGs in `Implementation/` are the authoritative sequence + interlock diagrams. | `[x] Completed` 2026-08-25 — recorded as D-005 |
| P0.7 | **Solo engineer, not two.** `CLAUDE.md` also references a non-existent `IMPLEMENTATION_PLAN.md` / `IMPLEMENTATION_STATUS.md`. | Roles **A (Stream & Enforcement)** and **B (Signals & Decisions)** become *work-stream labels* on sequential tasks, preserving the contract seam (which is the point of the split). The A↔B unblocking trick — stub risk engine + mock observer shipped first — is retained verbatim: it is what lets the entire enforcement path be built and tested with no GPU and no model. Create `IMPLEMENTATION_STATUS.md`; `TODO.md` is the plan index. | `[x] Completed` 2026-08-25 — recorded as D-006, IMPLEMENTATION_STATUS.md created |

**P0 exit:** `[x] COMPLETE 2026-08-25` — git repo initialised, Python 3.12.11 pinned, all seven deviations recorded as D-001…D-007 in `IMPLEMENTATION_STATUS.md`, Docker ruled out by the user.

---

# DAY 1 — The spine, and the seam

> **Goal at end of day:** an OpenAI SDK call goes through the gateway to a real provider, streams back token-for-token, carries a stakes estimate and a trace, and the stub risk engine can already force an intervention.

## D1-J — Joint foundation (contracts frozen here; highest-leverage block of the sprint)

- `[x]` **D1-J1.1 — Repo skeleton + CI** — *done 2026-08-25. ruff + ruff format + mypy --strict(core) + pytest all green; 16 tests. Markdown excluded from ruff format so the frozen spec docs are never rewritten.*
  - *Output:* `pyproject.toml` (uv, py3.12), `ruff.toml`, `mypy.ini` (strict on `interlock/core`), `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `Makefile` + `scripts/*.ps1` with `up`/`demo`/`eval` targets, full directory tree per Implementation01.
  - *Owner:* Joint · *Test:* CI green on an empty repo; `ruff && mypy && pytest` all exit 0.
- `[x]` **D1-J1.2 — FREEZE `interlock/core/types.py` (Contract 1)** — *done 2026-08-25. All 12 models + the Protocol, verbatim from Implementation03 §2. 24 contract tests: ladder/defect/lattice enumerations pinned, round-trip on every model, `extra=forbid` so drift fails loudly, Protocol conformance checked positively and negatively. Added `max_provenance()` (the lattice join) and `GateMode`.*
  - *Output:* `Action`, `Defect`, `Reversibility`, `Provenance`, `Fragment`, `Stakes`, `SignalReading`, `RiskContext`, `LossRow`, `Decision`, `RepairHint`, `RiskEngine` Protocol — verbatim from Implementation03 §2.
  - *Contract validation:* `mypy --strict` clean; round-trip pydantic serialisation test for every model; the `RiskEngine` Protocol structurally satisfied by both the stub and the real engine.
  - *Owner:* Joint · **Frozen after this task. Edits only at a checkpoint.**
- `[x]` **D1-J1.3 — FREEZE Observer HTTP (Contract 2) + SSE events (Contract 3)** — *done 2026-08-25. `core/observer_api.py` + `core/sse.py` + `docs/contracts/README.md`; 34 contract tests. Observer returns 200-always with in-band `degraded`; `RawSignal` has no `prob` field, so calibration cannot be bypassed. **Recorded risk:** the contract assumes SDKs ignore named SSE events, which is not universally true of SDK stream decoders — added an additive `X-Interlock-Events: off` opt-out and deferred verification against a real SDK to D1-A1.*
  - *Output:* `docs/contracts/observer_http.md` + pydantic request/response models; SSE event names `interlock.stakes|signal|decision|hold` with exact payload shapes.
  - *Contract validation:* schema test asserting `POST /v1/observe` returns 200 **always** unless malformed, and `degraded: true` + empty `signals[]` on internal failure; the `data:` channel stays byte-compatible with what the OpenAI SDK expects.
- `[ ]` **D1-J1.4 — Native service supervisor + make targets** *(replaces the compose task; see P0.2)*
  - *Output:* `scripts/up.ps1` launches gateway:8080 + observer:8081 + console:5173 as supervised local processes, polls each `/health` until ready, and prints a status table; `scripts/down.ps1` stops them cleanly. `make up|demo|eval` (and `.ps1` twins) exist — may print TODO initially. `.env.example` for provider keys, defaulting to Ollama so no key is needed.
  - *Test:* `scripts/up.ps1` reaches all-healthy from a cold start in < 90 s and `scripts/down.ps1` leaves no orphan processes.
- `[ ]` **D1-J1.5 — Demo corpus: 45 bank documents**
  - *Output:* `corpus/` + `manifest.json` — loan T&C, prepayment, claims, branch info, fee schedule, **6 deliberately contradictory pairs** (Clause 7.4 vs Clause 9.1 is the Scene-1 pair).
  - *Test:* manifest validates; contradictory pairs are machine-identifiable for the seeded eval set.

## D1-A — Stream & Enforcement

- `[ ]` **D1-A1 — `gateway/openai_compat.py`: the passthrough**
  - *Output:* `POST /v1/chat/completions` streaming + non-streaming; `httpx.AsyncClient` with pooling; correct SSE (`text/event-stream`, chunked, `X-Accel-Buffering: no`); provider adapters behind one interface — **Ollama (primary, no keys), OpenAI, Anthropic**.
  - *Test:* 12 recorded real SSE responses → `tests/fixtures/streams/*.jsonl`; a contract test replays them; byte-for-byte passthrough assertion. **These fixtures are the D1-B unblocking artefact.**
- `[ ]` **D1-A2 — `gateway/laneA.py`: pre-flight skeleton**
  - *Output:* `asyncio.gather` over injection · PII · canary · stakes · cache · route with a **40 ms hard `asyncio.wait_for`**. A detector that misses the deadline is **dropped, not awaited**; its absence is recorded as a signal with `prob=None`.
  - *Test:* a deliberately slow detector proves drop-not-await; a Lane A span is present on 100% of requests (F2).
- `[ ]` **D1-A3 — OpenTelemetry tracing**
  - *Output:* GenAI semantic conventions (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`) + `interlock.*` attributes; exporter → SQLite span table (no Jaeger container).
  - *Test:* one request produces a complete span tree with the stakes attributes attached.
- `[ ]` **D1-A4 — `ledger/writer.py` + migration 001**
  - *Output:* bounded `asyncio.Queue`, **single writer task**, one transaction per request, WAL + `busy_timeout=5000`. Migration `001_initial.sql` = the full schema from Implementation02 §3 (requests, signals, decisions, spend, tool_calls, holds, rework_edges, shadow_runs, fairness_pairs, labels) + all five indexes.
  - *Contract validation (Contract 5):* nothing on the token path touches `sqlite3` directly — enforced by a test that greps the gate/gateway modules.
  - *Test:* concurrent-write load test; migration idempotency.
- `[ ]` **D1-A5 — Demo application skeleton**
  - *Output:* FastAPI + minimal React bank support assistant, **real retrieval** (`bge-small-en-v1.5` + `sqlite-vec` over the 45 docs), `base_url` pointed at the gateway.
  - *Test:* answers a corpus question end to end through the gateway, streaming, with a trace row.

## D1-B — Signals & Decisions

- `[ ]` **D1-B1 — `risk/stub.py` + `observer/mock_server.py` (the unblocking trick — ship before anything else in this stream)**
  - *Output:* `StubRiskEngine` reads header `X-Interlock-Force: <defect>@<sentence_idx>` and returns a **fully populated `Decision`** — real loss table, fake probabilities. The mock observer returns scripted signals with a configurable sleep to exercise the deadline path.
  - *Contract validation:* satisfies the `RiskEngine` Protocol; swapping to the real engine on Day 3 must be a **one-line DI change**.
  - *Test:* `X-Interlock-Force: ungrounded@2` → a stub L2 decision visible in the trace.
- `[ ]` **D1-B2 — `signals/injection.py`**
  - *Output:* `protectai/deberta-v3-base-prompt-injection-v2` via transformers on CPU, ONNX-exported (~8 ms). Runs on the last user turn **and on every retrieved chunk separately** — this is what catches the poisoned PDF.
  - *Test:* known-injection corpus; the per-chunk scan proven by a test with a clean prompt and a poisoned chunk.
- `[ ]` **D1-B3 — `signals/pii.py` + `signals/canary.py`**
  - *Output:* Presidio (en) with custom recognisers for **PAN / Aadhaar / IFSC / account numbers**. Per-tenant canary registry, egress Aho-Corasick match (`pyahocorasick`, O(n), zero false positives), planted in **both the system prompt and the corpus**.
  - *Test:* canary match → deterministic L5 with no model in the loop (invariant 6); zero-false-positive assertion over the clean corpus.
- `[ ]` **D1-B4 — `signals/stakes.py` v1 (ADR-005: deterministic, no LLM)**
  - *Output:* feature scorer — retrieved-doc domain, monetary regex magnitude, intent keywords, user role from headers, tool schemas present, conversation depth. Domain table from the policy file. Emits `Stakes` **with a human-readable `rationale` list** and `features` for replay.
  - *Test:* the three-case table from the PDF (loan penalty ₹40,000 / internal ticket ₹200 / same at 1% risk) reproduces from the real policy file.
- `[ ]` **D1-B5 — Labelling pipeline + overnight semantic-entropy job**
  - *Output:* a script generating ~1,500 `(context, question, answer)` triples from the corpus with **induced failures**: retrieval dropped, numbers corrupted, clause IDs swapped, unanswerable questions, contradictory chunk injected. Launch 10-sample generation for the semantic-entropy labels (Ollama `qwen3:4b`).
  - *Test:* every triple carries machine-checkable ground truth; the failure taxonomy is balanced.

### Day 1 exit criteria
- `[ ]` `scripts/up.ps1` brings all 3 services to healthy from cold (per P0.2)
- `[ ]` Demo app answers through the gateway, streaming, with a trace row
- `[ ]` `X-Interlock-Force: ungrounded@2` → stub L2 decision in the trace
- `[ ]` Contracts committed and untouched since the freeze
- `[ ]` Semantic-entropy label job running

---

# DAY 2 — Signals become probabilities; tokens become a controllable stream

> **Goal:** the gate can hold, repair and release a sentence with a real signal, and every detector emits a calibrated probability with a reliability diagram.

## D2-A — The commit gate (hardest block of the sprint)

- `[ ]` **D2-A1 — `gate/segmenter.py` (TESTS FIRST — this is the "demo froze" risk)**
  - *Output:* incremental sentence segmentation, `pysbd` streaming mode + a character accumulator, hard flush at **240 chars** or `\n\n`, abbreviation guards.
  - *Test (write before the implementation):* `Rs. 40,000` · `Clause 7.4` · `e.g.` · `Dr. Rao` · `1. 2. 3.` lists · code fences · **mid-word chunk splits**.
- `[ ]` **D2-A2 — `gate/sentence_gate.py`: the state machine**
  - *Output:* `PASSTHROUGH → BUFFERING → HOLDING → REPAIRING → TERMINATED`; exactly one sentence buffered; **8 s per-sentence watchdog**; monotone escalation (never de-escalates); accurate `already_emitted` bookkeeping.
  - *ADR-003:* L0 traffic is **not buffered at all** — this is what preserves TTFT p50.
- `[ ]` **D2-A3 — Gate property test (the stage-day insurance policy)**
  - *Output:* a Hypothesis test — *for any token stream and any decision sequence, no uncommitted sentence is ever emitted, and every token is emitted exactly once or explicitly replaced.*
- `[ ]` **D2-A4 — `gate/repair.py` (L2)**
  - *Output:* truncate the buffered sentence → re-prompt the **same** model with `{context, question, answer_prefix, unsupported_claim, evidence}`, `max_tokens=80`, `stop=["\n"]` → re-verify the replacement through the same risk engine → two failures escalate to L3. Cost charged to the ledger as `component='repair'`.
- `[ ]` **D2-A5 — `gate/ladder.py` (L1/L3/L4/L5)**
  - *Output:* L1 annotate = **deterministic string transform** (citation append + hedge softening, no model); L3 reroute = re-retrieve + stronger tier + compare; L4 hold = durable row + review card + SSE `interlock.hold`; L5 block = deterministic only.
- `[ ]` **D2-A6 — `gateway/governor.py` v1**
  - *Output:* sliding-window p95, observer circuit breaker (5 failures/10 s → open, 30 s half-open), the five states `NORMAL → THIN → SHALLOW → PROBE_ONLY → BYPASS`, exposed at `/admin/governor`.
  - *Test:* pausing the observer degrades rather than 500s; **fail-open low-stakes / fail-closed high-stakes** asserted explicitly (invariant 4).
- `[ ]` **D2-A7 — Console live risk-trail websocket**, rendering the raw SSE events.

## D2-B — Calibration and the observer

- `[ ]` **D2-B1 — `risk/calibration.py`** — per-signal **5-fold cross-fitted** `IsotonicRegression(out_of_bounds="clip")`; ECE, Brier, `reliability.png`. *Target: ECE < 0.05 held-out.*
- `[ ]` **D2-B2 — `risk/conformal.py`** — Learn-then-Test threshold selection with the **Hoeffding–Bentkus** bound; output `lambda.json` with a certified `(α=0.01, δ=0.10)`.
- `[ ]` **D2-B3 — Hand-label 300 items** via a ~40-line CLI/Streamlit labeller. **Not delegated to a model** — this is the anchor set, the calibration ground truth and the meta-monitor input. *Fallback: 200 labels, widen δ to 0.15, and say so.*
- `[ ]` **D2-B4 — `observer/model.py`** — Qwen3-1.7B/4B, `output_hidden_states=True`, fp16, `torch.inference_mode()`; **DeBERTa-v3-base CPU profile behind an identical interface (the primary path here, per P0.3)**. Both must work.
- `[ ]` **D2-B5 — `observer/kvcache.py`** — prefix KV cache keyed by `context_key`, LRU of 64. The first sentence pays full prefill; later sentences pay ~30 tokens. *This is the 200 ms → 12 ms difference; build it now, not later.*
- `[ ]` **D2-B6 — `observer/verifier.py`** — MiniCheck-class (`lytang/MiniCheck-Flan-T5-Large`) with claim splitting; returns a per-claim label + **the offending span**. *Without the span, L2 repair has nothing to aim at.*
- `[ ]` **D2-B7 — Train the probes** — hidden states at every layer → per-layer logistic probes → select by held-out AUROC → `artifacts/probes/<version>/`. Produce the **accuracy-by-layer curve**. Also fit the verbal-uncertainty probe → **Overconfidence Index**.
  - *Fallback (cut-list #7):* logprob/entailment proxy behind the same interface, ~30 min swap.

### Day 2 exit criteria
- `[ ]` Gate property test passes; a real stream can be held, repaired and released
- `[ ]` Observer returns real probe scores under 25 ms p95 warm, with KV caching proven by a log line
- `[ ]` `reliability.png` + `lambda.json` committed with a certified (α=0.01, δ=0.10)
- `[ ]` Accuracy-by-layer curve exists
- `[ ]` Governor degrades when the observer is killed

---

# DAY 3 — The decision layer and the interlock

> **Goal:** no stubs left on the critical path. Real signals → real probabilities → real expected-loss table → real action. Scenes 1 and 2 work end to end.

## D3-A — Tool interlock
- `[ ]` **D3-A1 — `interlock_tools/provenance.py`** — the taint lattice `system < user < retrieved_verified < retrieved_untrusted < tool_external`; label every fragment at ingestion; propagate across turns; `influencing_taint(tool_call)` = **tier 1** exact / ≥ 0.9-token-overlap argument match, **tier 2** conservative max over untrusted fragments retrieved this turn (ADR-007).
- `[ ]` **D3-A2 — `interlock_tools/reversibility.py` + `holds.py`** — policy lookup, the taint × reversibility matrix, **durable** pending holds with resume tokens, `POST /v1/holds/{id}/approve|reject`, expiry sweeper.
  - *Test:* **kill-and-restart — the hold survives.** This is the demo/product boundary.
- `[ ]` **D3-A3 — Scene 2 wiring** — PDF upload path, hidden white-text instruction, retrieval marks the chunk `retrieved_untrusted`, the agent has a `send_email` tool, the interlock freezes it, the console raises a review card.
- `[ ]` **D3-A4 — `gateway/router.py` + `cache.py`** — RouteLLM `mf` controller; **the threshold is a function of `Stakes`** (Contribution 1 — must be visible in the trace as `route_reason` plus the shared stakes id). The cache serves **only when** cosine ≥ 0.95 **and** the retrieval-context hash matches **and** stakes ≤ threshold **and** the cached answer previously passed verification.
- `[ ]` **D3-A5 — Agent loop breaker** — n-gram repeat on `(tool, args_digest)`, 3-strike rule + context-growth slope check; cut the loop, log the saved spend.
- `[ ]` **D3-A6 — Latency instrumentation** — `overhead_ms` per request, split by lane, exported as a histogram. *The Day-5 p95 must be measured, not estimated.*

## D3-B — The risk engine, for real
- `[ ]` **D3-B1 — `risk/objective.py`** — the four-term expected loss; `Impact_d = stakes.impact_inr × class_multiplier[d] × reversibility_multiplier[...]`, all three from the versioned policy; **hard-constraint pre-pass before the argmin** (ADR-008); conformal feasibility filter; **the full `LossRow` table returned always — the table is the explanation.**
- `[ ]` **D3-B2 — `signals/fusion.py`** — logistic fusion over calibrated signals → `P(d)` per defect class, cross-fitted on the same folds as calibration.
- `[ ]` **D3-B3 — `risk/engine.py`** — the real `RiskEngine` behind the identical Protocol. Deadline-aware (a short deadline skips the verifier and prices with probe-only probabilities, marking `degraded`). **Never raises** — returns `L0_pass` with `why=["degraded: …"]` on any internal failure.
- `[ ]` **D3-B4 — THE BIG INTEGRATION** — swap `StubRiskEngine → RealRiskEngine` in one line of DI wiring. *If the contracts were honoured this just works. Budget 30 minutes of pain anyway.*
- `[ ]` **D3-B5 — `stakes` v2** — a small intent classifier over the corpus domains as **one feature among several**; the rationale stays human-readable (ADR-005).
- `[ ]` **D3-B6 — Efficacy matrix v1 from data (ADR-009)** — run the pipeline over the labelled set with **each action forced**, measure the actual reduction per defect class, and write `efficacy` back into the policy file **with Wilson intervals**. *Turns the objective's weakest assumption into a measurement.*
- `[ ]` **D3-B7 — `eval/` harness + seeded set v1** — 200 conversations, **60 induced failures**: 15 missing-retrieval, 10 number corruptions, 10 poisoned docs, 8 canary/PII, 10 demographic twin pairs, 7 loop-inducing agent tasks; each machine-checkable. `make eval` runs **off vs on** and prints all six metrics.

### Day 3 exit criteria
- `[ ]` No stub on the hot path
- `[ ]` Scene 1 (invented clause → held → repaired → cited) works live
- `[ ]` Scene 2 (poisoned PDF → tool frozen → review card) works live
- `[ ]` `make eval` prints six numbers, even if they are bad
- `[ ]` The router provably consumes the same `Stakes` object as the risk engine — from one trace

---

# DAY 4 — The money, the fairness, the console

> **Goal:** a defensible net-savings number, an e-value from a fairness run, and a console worth projecting.

## D4-A — The ledger and console shell
- `[ ]` **D4-A1 — `ledger/pricing.py`** — per-model INR token prices, config-driven.
- `[ ]` **D4-A2 — `ledger/regret.py`** — shadow-sample 5% of traffic, replay on the cheaper tier, store verdicts, estimate population regret with a **bootstrap CI**. *Never report a bare point estimate.*
- `[ ]` **D4-A3 — `ledger/rework.py`** — the session graph. Retry detection (cosine ≥ 0.90 to the previous user turn within 120 s), explicit regenerate, human escalation from holds. Charge the child cost — and the policy's human cost — back to the parent `request_id`, with **the confidence stored on the edge**.
- `[ ]` **D4-A4 — Console: split-screen live risk trail** — customer view left; per-sentence signals, the loss table and the **counterfactual** right. *The counterfactual is what makes the demo land.*
- `[ ]` **D4-A5 — Console: review queue** — approve/reject on holds.
- `[ ]` **D4-A6 — Evidence pack export** — a zip of decisions + inputs + loss tables + policy/calib versions + reviewer. *The EU AI Act Art. 12/14 artefact; a `zipfile.write` loop over data already stored.*

## D4-B — Lane C and charts
- `[ ]` **D4-B1 — `lanec/fairness.py`** — counterfactual twins; mutate name/gender/age markers via templates on sampled real queries; run both; extract **decision-relevant fields** (approved?, amount quoted, hedge count) with a structured extractor; store the pairs.
- `[ ]` **D4-B2 — `lanec/evalues.py`** — betting martingale `e_t = Π(1 + λ_t(X_t − μ₀))`, alert at `e_t ≥ 1/α`, always-valid `p = 1/max e`. One chart: e-value over time with the alert line. **Anytime-valid — never repeated ordinary significance tests.**
- `[ ]` **D4-B3 — `lanec/judge.py` + `drift.py`** — the ~1% offline calibration anchor; a meta-monitor that re-scores the human anchor set and reports when to stop trusting the fast lanes.
- `[ ]` **D4-B4 — Console ledger view** — spend / regret / rework / running net, with **the CI rendered as a band**.
- `[ ]` **D4-B5 — Console chart panels** — reliability diagram, accuracy-by-layer, e-value chart, fed from `artifacts/`.
- `[ ]` **D4-B6 — Seeded set v2** — expand the failure taxonomy; **fix leakage: the calibration set and the eval set must be provably disjoint, and you must be able to say so.**

- `[ ]` **D4-J1 — First full rehearsal** — all four scenes end to end, twice, on the real box. Write down every rough edge; fix the top five; the rest goes on a list you deliberately do not fix.

### Day 4 exit criteria
- `[ ]` Ledger shows a net number with a confidence interval
- `[ ]` Fairness run produces an e-value chart
- `[ ]` All four scenes run without a human touching a terminal mid-scene
- `[ ]` Evidence pack downloads and opens
- `[ ]` Calibration set and eval set provably disjoint

---

# DAY 5 — Measure, harden, deploy, rehearse

> **Goal:** six measured numbers, live on a URL, and the pitch run four times.

- `[ ]` **D5-J1 — FEATURE FREEZE.** No new features after this hour. Write it where you can see it.
- `[ ]` **D5-B1 — The measurement run** — `make eval` on the full seeded set, **off vs on, three seeds** → `report.html` with all six metrics and their intervals. Fill in the final slide only now.
  - **If the Pre-Action Catch Rate is below 90%, do not tune the eval. Report what you got and explain the failure modes.** A panel trusts a measured 84% far more than a suspicious 97%.
- `[ ]` **D5-A1 — Chaos pass** — kill the observer mid-stream (must degrade, not 500), an upstream 429 storm, SQLite lock contention, a malformed policy at boot (**refuse to start; the previous version stays live**), an 8 s watchdog fire.
- `[ ]` **D5-A2 — Load pass** — 20 concurrent streams × 5 minutes → capture the **p95 overhead histogram**. This number goes on the slide.
- `[ ]` **D5-A3 — Security sweep** — API keys never logged; prompts hashed unless `INTERLOCK_STORE_PROMPTS=1`; canary registry per tenant; no secrets in the image.
- `[ ]` **D5-A4 — Deploy** — `docs/05_deploy_runbook.md`; the CPU-only native profile a judge runs in one command (`scripts/up.ps1`), plus a documented single-VM path. **Tested from a clean checkout** — clone to a temp directory and follow your own README. *You will find three bugs.*
- `[ ]` **D5-B2 — Evidence pack for the pitch** — the mechanism table with **our measured numbers beside the published ones**; the six-metric scorecard; the three-case stakes table regenerated from the real policy files.
- `[ ]` **D5-B3 — `docs/LIMITATIONS.md`** — the provenance heuristic (ADR-007 over-blocks on paraphrase), the single vertical, the fairness sample size, the efficacy CIs. **Volunteer these before the panel finds them.** Highest-ROI page in the deck.
- `[ ]` **D5-J2 — Rehearse** — four run-throughs, 8 minutes each. Drill the two questions that decide the round: *"you need model internals but claim model-agnosticism"* and *"latency, honestly."* **Record one run as the backup video. Never demo live without a fallback.**

### Day 5 exit criteria
- `[ ]` Six metrics measured, with intervals, in `report.html`
- `[ ]` p95 overhead histogram captured under load
- `[ ]` Clean-checkout deploy verified on both profiles
- `[ ]` `LIMITATIONS.md` written
- `[ ]` Backup video recorded

---

## Cut list — decided now, calmly, not at 03:00 on Day 5

1. Drift/meta-monitor → one static chart
2. Fairness twins → pre-recorded, **stated as pre-recorded on the slide**
3. Shadow replay → fixed offline sample
4. Semantic cache → exact-match only
5. Loop breaker → detect and log, no cutting
6. L3 reroute → collapses into L4 hold
7. Trained probe → logprob/entailment proxy
8. Console → risk trail and ledger only

**Never cut:** the calibration step · the commit gate · the tool interlock · the seeded eval set.

## Risk register (trigger → fallback)

| Risk | Mitigation | Fallback trigger |
|---|---|---|
| Probe AUROC < 0.7 | Ship the fused verifier + logprob proxy; the architecture is unchanged | End of Day 2 |
| No GPU / OOM | **Already realised (P0.3)** — the CPU profile is the primary path | Resolved at P0 |
| Segmentation eats the demo | Property + abbreviation tests written **before** the gate | Day 2 morning |
| Provider rate limits mid-demo | Ollama is local (no limits); response cache primed on the exact demo prompts | Day 5 |
| Merge hell in `core/` | `core/` edited only at checkpoints | continuous |
| Labelling drags past 90 min | Cut to 200 labels, widen δ to 0.15, **say so** | Day 2, 13:00 |
| Net savings comes out **positive** (oversight costs money) | Raise the cache/route share on low-stakes traffic; if it is still positive, **report it honestly and show the break-even traffic mix** | Day 4, 21:30 |
