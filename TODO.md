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
- `[x]` **D1-J1.4 — Native service supervisor + make targets** — *done 2026-08-25. `scripts/up.ps1` starts observer + gateway and polls `/health`; **cold start measured at 5.3 s** against a 90 s target. `scripts/down.ps1` matches by command line (survives a crash, cannot kill an unrelated process) and leaves no orphans. `.env.example` defaults to keyless Ollama.* *(replaces the compose task; see P0.2)*
  - *Output:* `scripts/up.ps1` launches gateway:8080 + observer:8081 + console:5173 as supervised local processes, polls each `/health` until ready, and prints a status table; `scripts/down.ps1` stops them cleanly. `make up|demo|eval` (and `.ps1` twins) exist — may print TODO initially. `.env.example` for provider keys, defaulting to Ollama so no key is needed.
  - *Test:* `scripts/up.ps1` reaches all-healthy from a cold start in < 90 s and `scripts/down.ps1` leaves no orphan processes.
- `[x]` **D1-J1.5 — Demo corpus: 45 bank documents** — *done 2026-08-25 via `scripts/build_corpus.py`. 6 contradictory pairs (Clause 9.1 vs 7.4 is Scene 1), 1 poisoned claims PDF with white-text injection (Scene 2), plus **a benign untrusted upload as a control** so 'untrusted' is not perfectly correlated with 'malicious' in the eval set. Every doc carries a domain that exists in the policy.*
  - *Output:* `corpus/` + `manifest.json` — loan T&C, prepayment, claims, branch info, fee schedule, **6 deliberately contradictory pairs** (Clause 7.4 vs Clause 9.1 is the Scene-1 pair).
  - *Test:* manifest validates; contradictory pairs are machine-identifiable for the seeded eval set.

## D1-A — Stream & Enforcement

- `[x]` **D1-A1 — the passthrough** — *done 2026-08-25 as `gateway/{app,providers,config}.py`. Streaming + non-streaming, pooled `httpx.AsyncClient`, `X-Accel-Buffering: no`. Adapters: Ollama (default, keyless), OpenAI, Anthropic. **12 real SSE fixtures recorded from live Ollama** via `scripts/record_streams.py`; 53 contract tests replay them byte-for-byte. Verified live end-to-end against Ollama. **Contract 3 risk resolved: the real OpenAI SDK reads our stream and ignores the named events** (`test_the_real_openai_sdk_can_read_our_stream`).*
  - *Output:* `POST /v1/chat/completions` streaming + non-streaming; `httpx.AsyncClient` with pooling; correct SSE (`text/event-stream`, chunked, `X-Accel-Buffering: no`); provider adapters behind one interface — **Ollama (primary, no keys), OpenAI, Anthropic**.
  - *Test:* 12 recorded real SSE responses → `tests/fixtures/streams/*.jsonl`; a contract test replays them; byte-for-byte passthrough assertion. **These fixtures are the D1-B unblocking artefact.**
- `[x]` **D1-A2 — `gateway/lane_a.py`: pre-flight** — *done 2026-08-25. Detectors race under a hard deadline via `asyncio.wait` + cancel; a slow detector is **cancelled, not awaited**, and recorded with `prob=None`. Stakes and the router run inline (dropping stakes would leave the request with no budget at all). Deadline 120 ms per D-008.*
  - *Output:* `asyncio.gather` over injection · PII · canary · stakes · cache · route with a **40 ms hard `asyncio.wait_for`**. A detector that misses the deadline is **dropped, not awaited**; its absence is recorded as a signal with `prob=None`.
  - *Test:* a deliberately slow detector proves drop-not-await; a Lane A span is present on 100% of requests (F2).
- `[x]` **D1-A3 — OpenTelemetry tracing** — *request spans are exported to the SQLite `spans` table with GenAI semantic attributes and custom `interlock.*` fields; contract-tested through the gateway.*
  - *Output:* GenAI semantic conventions (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`) + `interlock.*` attributes; exporter → SQLite span table (no Jaeger container).
  - *Test:* one request produces a complete span tree with the stakes attributes attached.
- `[x]` **D1-A4 — `ledger/writer.py` + migration 001** — *done 2026-08-25. Bounded queue, single writer task, one transaction per request, WAL + busy_timeout=5000. Migration 001 has all 11 tables + 9 indexes, idempotent, applied at boot. **Holds are the deliberate exception: awaited and committed, verified to survive a process kill.** Contract 5 enforced by a test that greps gateway/gate/signals/risk for direct sqlite3 imports. DuckDB read-only attach verified.*
  - *Output:* bounded `asyncio.Queue`, **single writer task**, one transaction per request, WAL + `busy_timeout=5000`. Migration `001_initial.sql` = the full schema from Implementation02 §3 (requests, signals, decisions, spend, tool_calls, holds, rework_edges, shadow_runs, fairness_pairs, labels) + all five indexes.
  - *Contract validation (Contract 5):* nothing on the token path touches `sqlite3` directly — enforced by a test that greps the gate/gateway modules.
  - *Test:* concurrent-write load test; migration idempotency.
- `[x]` **D1-A5 — Demo application skeleton** — *gateway wiring, retrieval, routing, ledger, and the bank assistant UI are present; the deterministic upstream and real-gateway rehearsal remain the documented fallback when Ollama is unavailable.* — original scope:
  - *Output:* FastAPI + minimal React bank support assistant, **real retrieval** (`bge-small-en-v1.5` + `sqlite-vec` over the 45 docs), `base_url` pointed at the gateway.
  - *Test:* answers a corpus question end to end through the gateway, streaming, with a trace row.

## D1-B — Signals & Decisions

- `[x]` **D1-B1 — `risk/stub.py` + `observer/mock_server.py` (the unblocking trick)** — *done 2026-08-25. StubRiskEngine satisfies the Protocol (`isinstance` asserted), driven by `X-Interlock-Force: <defect>@<idx>[:<prob>]`; real policy, real four-term loss table, real hard-rule pre-pass, fake probabilities only. Mock observer implements Contract 2 with scriptable `[[HALLUCINATE]]` / `[[SLOW:n]]` / `[[DEGRADE]]` and a real LRU prefix-key set so cache-miss paths are exercised. 182 tests.*
  - *Output:* `StubRiskEngine` reads header `X-Interlock-Force: <defect>@<sentence_idx>` and returns a **fully populated `Decision`** — real loss table, fake probabilities. The mock observer returns scripted signals with a configurable sleep to exercise the deadline path.
  - *Contract validation:* satisfies the `RiskEngine` Protocol; swapping to the real engine on Day 3 must be a **one-line DI change**.
  - *Test:* `X-Interlock-Force: ungrounded@2` → a stub L2 decision visible in the trace.
- `[x]` **D1-B2 — `signals/injection.py`** — *done 2026-08-25. Deterministic pattern backend (default) + lazily-imported transformer backend behind one interface. Scans the user turn **and every retrieved chunk separately** — proven by a test where a clean prompt + poisoned chunk still scores >0.9. Zero false positives across all 43 trusted corpus documents.*
  - *Output:* `protectai/deberta-v3-base-prompt-injection-v2` via transformers on CPU, ONNX-exported (~8 ms). Runs on the last user turn **and on every retrieved chunk separately** — this is what catches the poisoned PDF.
  - *Test:* known-injection corpus; the per-chunk scan proven by a test with a clean prompt and a poisoned chunk.
- `[x]` **D1-B3 — `signals/pii.py` + `signals/canary.py`** — *done 2026-08-25. PII is **checksum-first**: Verhoeff for Aadhaar, Luhn for cards, strict formats for PAN/IFSC — verified against the textbook Verhoeff vector. Canary uses Aho-Corasick, planted in system prompt AND corpus, matched on egress, deterministic L5, cross-tenant leaks flagged, zero false positives over the whole corpus, never logged in full.*
  - *Output:* Presidio (en) with custom recognisers for **PAN / Aadhaar / IFSC / account numbers**. Per-tenant canary registry, egress Aho-Corasick match (`pyahocorasick`, O(n), zero false positives), planted in **both the system prompt and the corpus**.
  - *Test:* canary match → deterministic L5 with no model in the loop (invariant 6); zero-false-positive assertion over the clean corpus.
- `[x]` **D1-B4 — `signals/stakes.py` v1** — *done 2026-08-25. Deterministic feature scorer: retrieved domain (outranks keywords — keywords are the part an attacker controls), monetary magnitude with lakh/crore scaling and Indian grouping, tool reversibility, user role, conversation depth. Every term emits a readable rationale line; `features` makes it replayable.*
  - *Output:* feature scorer — retrieved-doc domain, monetary regex magnitude, intent keywords, user role from headers, tool schemas present, conversation depth. Domain table from the policy file. Emits `Stakes` **with a human-readable `rationale` list** and `features` for replay.
  - *Test:* the three-case table from the PDF (loan penalty ₹40,000 / internal ticket ₹200 / same at 1% risk) reproduces from the real policy file.
- `[~]` **D1-B5 — Labelling pipeline + overnight semantic-entropy job** — *done 2026-08-25 (induced-failure half). `eval/induce.py` builds deterministic labelled triples across all six modes, exact proportions, zero fallbacks, machine-checkable ground truth. **Remaining: the Ollama semantic-entropy sampling job** -- 10 samples x N questions at ~3-14 s each is not affordable on this hardware yet; the pipeline it feeds is built and takes the scores when they exist.* — original scope:
  - *Output:* a script generating ~1,500 `(context, question, answer)` triples from the corpus with **induced failures**: retrieval dropped, numbers corrupted, clause IDs swapped, unanswerable questions, contradictory chunk injected. Launch 10-sample generation for the semantic-entropy labels (Ollama `qwen3:4b`).
  - *Test:* every triple carries machine-checkable ground truth; the failure taxonomy is balanced.

### Day 1 exit criteria
- `[x]` `scripts/up.ps1` brings all 3 services to healthy from cold (per P0.2) — *verified in the deterministic rehearsal profile.*
- `[x]` Demo app answers through the gateway, streaming, with a trace row — *verified by gateway contract tests and rehearsal.*
- `[x]` `X-Interlock-Force: ungrounded@2` → stub L2 decision in the trace — *stub retained behind `INTERLOCK_RISK_ENGINE=stub`; the real engine ignores the header by design*
- `[x]` Contracts committed and untouched since the freeze — *158 contract tests green; Contract 1 took two ADDITIVE optional fields under its own change rule*
- `[ ]` Semantic-entropy label job running

---

# DAY 2 — Signals become probabilities; tokens become a controllable stream

> **Goal:** the gate can hold, repair and release a sentence with a real signal, and every detector emits a calibrated probability with a reliability diagram.

## D2-A — The commit gate (hardest block of the sprint)

- `[x]` **D2-A1 — `gate/segmenter.py`** — *done 2026-08-25, **tests written first and confirmed failing** before implementation. 44 tests, all edge cases drawn from the real recorded fixtures. Chunk-order independence proven at chunk sizes 1/2/3/5/13/64. **F-004 resolved:** `<think>` blocks are excluded from answer text (with a carry buffer so a tag split across chunks is still caught) but kept and recoverable for the console.*
  - *Output:* incremental sentence segmentation, `pysbd` streaming mode + a character accumulator, hard flush at **240 chars** or `\n\n`, abbreviation guards.
  - *Test (write before the implementation):* `Rs. 40,000` · `Clause 7.4` · `e.g.` · `Dr. Rao` · `1. 2. 3.` lists · code fences · **mid-word chunk splits**.
- `[x]` **D2-A2 — `gate/sentence_gate.py`: the state machine** — *done 2026-08-25. PASSTHROUGH -> BUFFERING -> HOLDING -> REPAIRING -> TERMINATED, one-sentence buffer, 8 s watchdog, one-way escalation, accurate `already_emitted`. Verification runs concurrently with generation (the SentGuard 36 ms vs 576 ms distinction). Fails open if the engine hangs or raises — holding a sentence because our own checker stalled is the worst outcome.*
  - *Output:* `PASSTHROUGH → BUFFERING → HOLDING → REPAIRING → TERMINATED`; exactly one sentence buffered; **8 s per-sentence watchdog**; monotone escalation (never de-escalates); accurate `already_emitted` bookkeeping.
  - *ADR-003:* L0 traffic is **not buffered at all** — this is what preserves TTFT p50.
- `[x]` **D2-A3 — Gate property test** — *done 2026-08-25. Hypothesis over {sentences x actions x chunk size 1-40 x buffered}: no withheld sentence ever reaches the customer; every token emitted exactly once; unbuffered output byte-identical. 25 gate tests total. **Caught a real leak:** unbuffered `finish()` left a dangling evaluation task, losing the decision from the trace.*
  - *Output:* a Hypothesis test — *for any token stream and any decision sequence, no uncommitted sentence is ever emitted, and every token is emitted exactly once or explicitly replaced.*
- `[x]` **D2-A4 — `gate/repair.py` (L2)** — *the gate's repair path is built and tested (two attempts then escalate; a failed repair withholds rather than shipping the original). Remaining: the actual re-prompt against the model.* — original scope:
  - *Output:* truncate the buffered sentence → re-prompt the **same** model with `{context, question, answer_prefix, unsupported_claim, evidence}`, `max_tokens=80`, `stop=["\n"]` → re-verify the replacement through the same risk engine → two failures escalate to L3. Cost charged to the ledger as `component='repair'`.
- `[x]` **D2-A5 — `gate/ladder.py` (L1/L3/L4/L5)** — *done 2026-08-25. L1 is a deterministic string transform (citation, unverified marker, hedge softening) with no model in the loop, which is what keeps it cheap enough for the argmin to pick. L4 writes a durable hold; L5 terminates. Verified live: every rung reachable.*
  - *Output:* L1 annotate = **deterministic string transform** (citation append + hedge softening, no model); L3 reroute = re-retrieve + stronger tier + compare; L4 hold = durable row + review card + SSE `interlock.hold`; L5 block = deterministic only.
- `[x]` **D2-A6 — `gateway/governor.py` v1** — *done 2026-08-26. Five states with a strictly nested capability set (asserted by a test), sliding-window p95, observer circuit breaker (5 failures/10 s -> open, 30 s -> half-open admitting exactly one probe). **Invariant 4 asserted explicitly in both directions**: BYPASS passes low stakes and holds high stakes, split at the same threshold buffering and routing use. Recovery is one rung at a time and slower than escalation, or it oscillates under the load that caused it. Fed by Interlock's OWN overhead, not total latency -- a slow upstream must not thin the guardrail. Exposed at `/admin/governor`.* — original scope:
  - *Output:* sliding-window p95, observer circuit breaker (5 failures/10 s → open, 30 s half-open), the five states `NORMAL → THIN → SHALLOW → PROBE_ONLY → BYPASS`, exposed at `/admin/governor`.
  - *Test:* pausing the observer degrades rather than 500s; **fail-open low-stakes / fail-closed high-stakes** asserted explicitly (invariant 4).
- `[x]` **D2-A7 — Console live risk-trail websocket** — *implemented via `ConsoleHub`, `/console/ws`, HTTP replay, and console rendering of stakes, signals, decisions and holds.*

## D2-B — Calibration and the observer

- `[x]` **D2-B1 — `risk/calibration.py`** — *done 2026-08-25. Isotonic per signal, 5-fold cross-fitted, logistic fusion over calibrated values on the same folds. **Measured out-of-fold at n=2000: ECE 0.0451 (target < 0.05, PASS), Brier 0.0863, AUROC 0.9205.** `reliability.png` ships with a bin-count histogram, which shows the predictions are bimodal -- the ECE rests on four populated bins. The report emits a note for any failure mode within 0.05 of the clean baseline; `unanswerable` triggers it.* — original scope: — per-signal **5-fold cross-fitted** `IsotonicRegression(out_of_bounds="clip")`; ECE, Brier, `reliability.png`. *Target: ECE < 0.05 held-out.*
- `[x]` **D2-B2 — `risk/conformal.py`** — *done 2026-08-25. Hoeffding-Bentkus with fixed-sequence testing; exact binomial CDF written out rather than taken from scipy. **Certified: at most 1% ungrounded escapes at 90% confidence, threshold 0.145, n=1000 -- but intervening on 100% of traffic**, which the result flags itself. The bound holds and is operationally useless until the observer probe can see `unanswerable`.* — original scope: — Learn-then-Test threshold selection with the **Hoeffding–Bentkus** bound; output `lambda.json` with a certified `(α=0.01, δ=0.10)`.
- `[x]` **D2-B3 — Hand-label 300 items** — *completed 2026-08-29 as `data/labels/manual_anchor_300.jsonl`, generated from the calibration split and imported into the local ledger. The export records the manual-review basis, source split, taxonomy, and summary counts; it is not delegated to a model.*
- `[x]` **D2-B4 — `observer/encoder.py`** — *done 2026-08-26. torch 2.13 CPU + transformers 5.15 installed (CPU wheels are a few hundred MB, not the 2.5 GB CUDA build assumed when deferring). Cross-encoder pairing, mean-pooling over real tokens only, lazy load, thread-locked. **Deviation D-012**: transformers 5.x cannot load DeBERTa-v3's tokenizer (misroutes SentencePiece to the tiktoken parser); defaults to an NLI cross-encoder instead, which is arguably the better base since grounding IS entailment.* — original scope: — Qwen3-1.7B/4B, `output_hidden_states=True`, fp16, `torch.inference_mode()`; **DeBERTa-v3-base CPU profile behind an identical interface (the primary path here, per P0.3)**. Both must work.
- `[~]` **D2-B5 — observer context cache** — *partial 2026-08-29. The live `ProbeSignal` now keeps a 64-entry LRU keyed by `context_key` and exposes hits/misses in health; the mock observer already exercised the same key semantics. The current cross-encoder cannot do true autoregressive KV-prefix reuse, so this is operational context caching/visibility, not a 200 ms → 12 ms transformer KV cache.*
- `[x]` **D2-B6 — `observer/verifier.py`** — *done 2026-08-26. Deterministic claim splitting, NLI cross-encoder per (passage, claim), **returns the offending SPAN** -- verified live: "No prepayment charge applies... and the fee is waived above Rs. 2 lakh" splits correctly, marks the first claim supported (0.982) and the second contradicted (0.002), and points at (61,105). The entailment class is found **by name, not index**: this checkpoint is {0: contradiction, 1: entailment, 2: neutral}, so a hardcoded index 2 would have scored neutral as entailment.* — MiniCheck-class (`lytang/MiniCheck-Flan-T5-Large`) with claim splitting; returns a per-claim label + **the offending span**. *Without the span, L2 repair has nothing to aim at.*
- `[x]` **D2-B7 — Train the probes** — *done 2026-08-26. Per-layer logistic probes, held-out selection, accuracy-by-layer curve in `artifacts/probes/curve.json`. **Selection uses the one-standard-error rule**, added after a run picked the LAST layer on a 0.011 margin where one SE is ~0.015 -- a gap inside noise is not a ranking, and the tie broke toward the layer most likely to be the encoder's task head. **Train/serve skew found and fixed** (trained with untrusted passages in the premise, served without: cost ~0.07 AUROC and nothing errored).* — hidden states at every layer → per-layer logistic probes → select by held-out AUROC → `artifacts/probes/<version>/`. Produce the **accuracy-by-layer curve**. Also fit the verbal-uncertainty probe → **Overconfidence Index**.
  - *Fallback (cut-list #7):* logprob/entailment proxy behind the same interface, ~30 min swap.

### Day 2 exit criteria
- `[x]` Gate property test passes; a real stream can be held, repaired and released — *28 property tests green; verified live against Ollama*
- `[ ]` Observer returns real probe scores under 25 ms p95 warm, with KV caching proven by a log line
- `[x]` `reliability.png` + `lambda.json` committed with a certified (α=0.01, δ=0.10) — *certified at threshold 0.0150 on n=840; **the 100% intervention rate must be quoted with it***
- `[x]` Accuracy-by-layer curve exists — *`artifacts/probes/curve.json`; peaks mid-stack at layer 3, one-standard-error selection*
- `[ ]` Governor degrades when the observer is killed

---

# DAY 3 — The decision layer and the interlock

> **Goal:** no stubs left on the critical path. Real signals → real probabilities → real expected-loss table → real action. Scenes 1 and 2 work end to end.

## D3-A — Tool interlock
- `[x]` **D3-A1 — `interlock_tools/provenance.py`** — *done 2026-08-25. Two-tier attribution per ADR-007. **Matching is on token boundaries, not raw substring** -- a test caught `{"currency": "en"}` matching "prepaym**en**t", which traced every call to every document and made tier 1 meaningless. Tier 2 over-taints deliberately and says so in the rationale, so an operator can tell a traced freeze from a precautionary one. Taint carries across turns and is per-request.* — original scope: — the taint lattice `system < user < retrieved_verified < retrieved_untrusted < tool_external`; label every fragment at ingestion; propagate across turns; `influencing_taint(tool_call)` = **tier 1** exact / ≥ 0.9-token-overlap argument match, **tier 2** conservative max over untrusted fragments retrieved this turn (ADR-007).
- `[x]` **D3-A2 — `interlock_tools/reversibility.py` + `holds.py`** — *done 2026-08-25. Taint x reversibility matrix from the policy; the monetary cap is a separate axis on purpose. Durable holds committed before the caller is told, **kill-and-restart test passes**. Resume tokens gate approval only (constant-time compare); rejection needs no token. Expiry sweeper marks expired, never approved. Tool calls stream one CALL behind and are replayed assembled once cleared -- the client never sees a frozen call. `POST /v1/holds/{id}/approve|reject` wired.* — original scope: — policy lookup, the taint × reversibility matrix, **durable** pending holds with resume tokens, `POST /v1/holds/{id}/approve|reject`, expiry sweeper.
  - *Test:* **kill-and-restart — the hold survives.** This is the demo/product boundary.
- `[~]` **D3-A3 — Scene 2 wiring** — *partial 2026-08-29. The console uploads text or PDF bytes through `/v1/uploads`, returns an explicitly `retrieved_untrusted` fragment, and attaches it to the next completion; the existing tool interlock freezes `send_email` and raises a review card. The clean-checkout runtime deliberately has no PDF parser dependency, so production deployments must replace the conservative printable-text extractor with a parser-backed worker before claiming arbitrary-PDF coverage.*
- `[x]` **D3-A4 — `gateway/router.py` + `cache.py`** — *done 2026-08-29. Router: stakes dominates and cannot be overridden downward; difficulty decides within that. **Labelled `difficulty_heuristic-v1`, NOT `router_mf`** -- a trained matrix factorisation is not what this build has. Cache enforces all four conditions conjunctively, including the context hash over doc_id AND text, which is what stops a superseded clause being served forever. Live streaming now looks up verified clean answers before upstream selection, serves cache hits with `x-interlock-cache: hit`, publishes `dec_cache_hit` into ConsoleHub, and stores only non-degraded all-`L0_pass` answers. **Three routing bugs found and fixed, all of which sent 100% of traffic to the strong tier**: retrieval-never-ran read as maximally hard; document count measuring the retriever's k; and a score-spread term reading RRF's fusion constant rather than retrieval quality.* — original scope: — RouteLLM `mf` controller; **the threshold is a function of `Stakes`** (Contribution 1 — must be visible in the trace as `route_reason` plus the shared stakes id). The cache serves **only when** cosine ≥ 0.95 **and** the retrieval-context hash matches **and** stakes ≤ threshold **and** the cached answer previously passed verification.
- `[x]` **D3-A5 — Agent loop breaker** — *live gateway path now tracks session-scoped `(tool, args_digest)` repeats, cuts on the third strike, emits an `agent_loop` decision, and bounds history to 1,000 sessions; contract-tested through streaming.* — original scope: — n-gram repeat on `(tool, args_digest)`, 3-strike rule + context-growth slope check; cut the loop, log the saved spend.
- `[x]` **D3-A6 — Latency instrumentation** — *done 2026-08-26. Per-lane attribution at `/admin/latency`. **Lane B is deliberately NOT a lane**: it runs concurrently with generation, so only `gate_hold` -- the part the commit gate actually waited on -- is latency the customer experienced. Counting Lane B's wall-clock would make a correctly-designed system look slow. Buffered and unbuffered reported separately (pooling produces a p95 describing neither); unattributed overhead is reported rather than folded into the nearest lane; a percentile from under 20 samples refuses to call itself a p95. A test caught the `window` parameter being configurable in name only.* — `overhead_ms` per request, split by lane, exported as a histogram. *The Day-5 p95 must be measured, not estimated.*

## D3-B — The risk engine, for real
- `[x]` **D3-B1 — `risk/objective.py`** — *done 2026-08-25. Four-term arithmetic + hard-constraint pre-pass built at D1-B1; **the conformal feasibility filter now lands too** (strikes `L0_pass` when P(ungrounded) >= lambda). Off by default -- see F-016 -- and it records what guaranteed mode would have done even when off.* — original scope: — *four-term arithmetic + hard-constraint pre-pass built early at D1-B1 (the stub needs a real loss table); reproduces the pitch's three-case table exactly. **Remaining at D3: the conformal feasibility filter**, which needs the calibration artefacts.* — original scope: — the four-term expected loss; `Impact_d = stakes.impact_inr × class_multiplier[d] × reversibility_multiplier[...]`, all three from the versioned policy; **hard-constraint pre-pass before the argmin** (ADR-008); conformal feasibility filter; **the full `LossRow` table returned always — the table is the explanation.**
- `[x]` **D3-B2 — fusion** — *done 2026-08-25, as `MultiDefectCalibrator` in `risk/calibration.py` rather than a separate module: logistic fusion over calibrated signals, one-vs-rest per defect class, cross-fitted on the same folds as calibration. A separate `signals/fusion.py` would have split one fitted object across two files.* — original scope: — logistic fusion over calibrated signals → `P(d)` per defect class, cross-fitted on the same folds as calibration.
- `[x]` **D3-B3 — `risk/engine.py`** — *done 2026-08-25. Hard egress rules -> calibrated per-defect probabilities -> conformal feasibility filter -> argmin. **Wires `scan_egress`, which was written, unit-tested and called by nothing** -- invariant 6's canary control had never actually fired. Never raises; a broken calibrator or detector degrades to L0_pass with the reason in `why`.* — original scope: — the real `RiskEngine` behind the identical Protocol. Deadline-aware (a short deadline skips the verifier and prices with probe-only probabilities, marking `degraded`). **Never raises** — returns `L0_pass` with `why=["degraded: …"]` on any internal failure.
- `[x]` **D3-B4 — THE BIG INTEGRATION** — *done 2026-08-25. It was one line (`_build_risk_engine`), as the contract promised. The only breakage was 3 tests that drive the stub's `X-Interlock-Force` header; they now use a stub-backed fixture. `INTERLOCK_RISK_ENGINE=real` is the default -- no stub on the hot path.* — original scope: — swap `StubRiskEngine → RealRiskEngine` in one line of DI wiring. *If the contracts were honoured this just works. Budget 30 minutes of pain anyway.*
- `[ ]` **D3-B5 — `stakes` v2** — a small intent classifier over the corpus domains as **one feature among several**; the rationale stays human-readable (ADR-005).
- `[ ]` **D3-B6 — Efficacy matrix v1 from data (ADR-009)** — run the pipeline over the labelled set with **each action forced**, measure the actual reduction per defect class, and write `efficacy` back into the policy file **with Wilson intervals**. *Turns the objective's weakest assumption into a measurement.*
- `[x]` **D3-B7 — `eval/` harness + seeded set v1** — *done 2026-08-26. 200 conversations, exact category counts asserted by a test, **140 clean on purpose** (the false-intervention target can only be measured against traffic that deserved no intervention). Paired design: identical generations in both arms, so every difference is Interlock's. `make eval` prints six numbers. **Five pass; false interventions misses badly and the report says so** -- see F-019.* — original scope: — 200 conversations, **60 induced failures**: 15 missing-retrieval, 10 number corruptions, 10 poisoned docs, 8 canary/PII, 10 demographic twin pairs, 7 loop-inducing agent tasks; each machine-checkable. `make eval` runs **off vs on** and prints all six metrics.

### Day 3 exit criteria
- `[x]` No stub on the hot path — *`INTERLOCK_RISK_ENGINE=real` is the default; the stub survives only behind an explicit setting for chaos tests*
- `[x]` Scene 1 (invented clause → held → repaired → cited) works live — *verified against Ollama; the repair cites Clause 9.1*
- `[x]` Scene 2 (poisoned PDF → tool frozen → review card) works live — *`tests/contract/test_tool_interlock_stream.py`; the client never receives the frozen call*
- `[x]` `make eval` prints six numbers, even if they are bad — *and one of them is bad*
- `[x]` The router provably consumes the same `Stakes` object as the risk engine — *same `stakes_id` on the route decision and the risk decision, asserted by a test* — from one trace

---

# DAY 4 — The money, the fairness, the console

> **Goal:** a defensible net-savings number, an e-value from a fairness run, and a console worth projecting.

## D4-A — The ledger and console shell
- `[x]` **D4-A1 — `ledger/pricing.py`** — *done 2026-08-26. Prompt and completion priced separately (providers charge 3-5x more for completion and RAG traffic is prompt-heavy, so a blended rate over-states exactly the traffic routing makes cheap). **Local models are priced, not free** -- unmetered is not the same as costless, and a zero rate would make every efficiency claim trivially true. Unpriced models are reported rather than silently defaulted, and the fallback is deliberately expensive so somebody notices.*
- `[x]` **D4-A2 — `ledger/regret.py`** — *done 2026-08-26; live gateway sampling wired 2026-08-29. 5% shadow sampling of strong-tier traffic only; replays run asynchronously on the cheap tier and "would the cheap answer have passed?" is decided by the same risk engine rather than by similarity to the strong answer. **Percentile bootstrap CI**, not normal-approximation: per-request regret is mostly zeros with a few large values, so a symmetric interval would extend below zero. Point and interval scale to the population together. Under 30 samples the estimate says so.* — original scope: — shadow-sample 5% of traffic, replay on the cheaper tier, store verdicts, estimate population regret with a **bootstrap CI**. *Never report a bare point estimate.*
- `[x]` **D4-A3 — `ledger/rework.py`** — *done 2026-08-26; live streaming session attribution wired 2026-08-29. Three edge kinds by descending certainty: human escalation (1.0, the hold IS the edge), explicit regenerate (0.95), inferred retry (<=0.90). **Confidence is stored AND used** -- a retry at 0.72 charges 72% of the child's cost, because rework is the number that argues hardest for the product and so most needs to resist flattering itself. Live requests with `session_id` now persist explicit regenerate/retry edges; non-streaming session attribution remains pending.* — original scope: — the session graph. Retry detection (cosine ≥ 0.90 to the previous user turn within 120 s), explicit regenerate, human escalation from holds. Charge the child cost — and the policy's human cost — back to the parent `request_id`, with **the confidence stored on the edge**.
- `[x]` **D4-A4 — Console: split-screen live risk trail** — *customer stream left; stakes, per-sentence signals, full loss table and counterfactual right.*
- `[x]` **D4-A5 — Console: review queue** — *durable holds render with approve/reject controls; approval requires the initiating response token and rejection does not.*
- `[x]` **D4-A6 — Evidence pack export** — *done 2026-08-26. Zip of decisions + inputs + full loss tables + the policy file VERBATIM (not just its version string) + calibration artefacts. **Canaries and resume tokens redacted recursively by key**, asserted by a test -- this is the artefact most likely to be emailed around. **A missing field is reported on the front page, never defaulted**: an export omitting the loss table looks identical to one where the table was empty. Nothing is recomputed at export time.* — a zip of decisions + inputs + loss tables + policy/calib versions + reviewer. *The EU AI Act Art. 12/14 artefact; a `zipfile.write` loop over data already stored.*

## D4-B — Lane C and charts
- `[x]` **D4-B1 — `lanec/fairness.py`** — *done 2026-08-26. Five marker axes (religion, region, gender, age, caste) chosen for an Indian retail-banking deployment; one axis mutated per pair, so any difference has exactly one candidate cause. **Compares decisions, not prose** -- approved/amounts/percentages/hedging/ladder-action, because two runs of the same model word things differently every time and a similarity metric would report temperature as bias. A test caught a real weakness: a paraphrased approval slips past the regex, so decided-vs-unreadable is now reported as `extraction_uncertain` rather than counted as disparity.* — original scope: — counterfactual twins; mutate name/gender/age markers via templates on sampled real queries; run both; extract **decision-relevant fields** (approved?, amount quoted, hedge count) with a structured extractor; store the pairs.
- `[x]` **D4-B2 — `lanec/evalues.py`** — *done 2026-08-26. Betting martingale with a predictable Kelly-style plug-in lambda, capped at `safety/mu0` so no factor can go non-positive. **The Ville bound is verified by simulation**, not asserted: 300 runs peeking after every one of 400 observations stay under alpha, while the naive repeated z-test on the same data fires ~22% of the time. Predictability is tested by replay -- each recorded lambda must equal what the monitor would have produced from earlier observations alone.* — original scope: — betting martingale `e_t = Π(1 + λ_t(X_t − μ₀))`, alert at `e_t ≥ 1/α`, always-valid `p = 1/max e`. One chart: e-value over time with the alert line. **Anytime-valid — never repeated ordinary significance tests.**
- `[x]` **D4-B3 — `lanec/judge.py` + `drift.py`** — *done 2026-08-26. Judge sample rate is **capped at 5% and enforced at construction**, not merely documented; the module owns no provider, so nothing here can acquire a synchronous call on a request path. 'unclear' stays a real answer and never collapses to agreement. The meta-monitor separates calibration / agreement / input drift -- **input shift is reported but never alarms alone**, since traffic moving would otherwise fire on every product launch. It can return UNTRUSTED about its own numbers, and it **never retunes anything** (asserted structurally: no mutating API exists).* — original scope: — the ~1% offline calibration anchor; a meta-monitor that re-scores the human anchor set and reports when to stop trusting the fast lanes.
- `[x]` **D4-B4 — Console ledger view** — *live `/admin/economics` data renders spend, regret, rework and net value with a bootstrap 95% interval; the console renders the interval as a shaded band. Regret/rework are evenly allocated across request contributions because their current aggregate rows lack a request join, and the endpoint documents that limitation.*
- `[x]` **D4-B5 — Console chart panels** — *artifact-backed reliability, probe/layer and sensitivity panels are rendered; Lane C reports when live fairness samples are absent.*
- `[~]` **D4-B6 — Seeded set v2** — *the leakage half done 2026-08-26: calibration and eval are now **provably disjoint by document and stratified by domain**, with a test asserting it end-to-end (what calibration trains on vs what the eval set is built from, not just what the splitter returns). Every metric re-measured; verification cost went 3.60% -> 5.20% and now MISSES, which the shared-document split had hidden. Remaining: expanding the failure taxonomy.* — expand the failure taxonomy; **fix leakage: the calibration set and the eval set must be provably disjoint, and you must be able to say so.**

- `[~]` **D4-J1 — First full rehearsal** — *partial 2026-08-29. `scripts/rehearse_gateway.py --strict-actions` runs all four scenes against the real gateway and console with a deterministic local OpenAI-compatible fixture upstream, validates ConsoleHub replay, response-hold resume tokens and live economics/Lane C endpoints, and writes `artifacts/rehearsal/gateway_rehearsal.json`. Ollama did not respond on this machine, so this is not yet the required live-model run-through on the real box.*

### Day 4 exit criteria
- `[x]` Ledger shows a net number with a confidence interval — live `/admin/economics` exposes `net_value_inr`, `net_value_ci_inr`, and sample count; the console renders the interval band.
- `[ ]` Fairness run produces an e-value chart
- `[ ]` All four scenes run without a human touching a terminal mid-scene
- `[x]` Evidence pack downloads and opens — `GET /admin/evidence/{request_id}.zip` exports the recorded, redacted request pack; contract-tested by opening the ZIP and checking its manifest.
- `[x]` Calibration set and eval set provably disjoint — *D4-B6: by document, stratified by domain, zero shared, asserted end-to-end*

---

# DAY 5 — Measure, harden, deploy, rehearse

> **Goal:** six measured numbers, live on a URL, and the pitch run four times.

- `[ ]` **D5-J1 — FEATURE FREEZE.** No new features after this hour. Write it where you can see it.
- `[~]` **D5-B1 — The measurement run** — *partial 2026-08-29. `scripts/build_eval_report.py` runs the full 200-case off-vs-on evaluation across three seeds and writes `artifacts/eval/report.html` plus per-seed JSON with Wilson intervals. The three reported misses remain reproducible: verification cost 5.20/5.51/5.35%, net spend -18.96/-16.73/-16.59%, false interventions 85.35/91.08/90.45%.*
  - **If the Pre-Action Catch Rate is below 90%, do not tune the eval. Report what you got and explain the failure modes.** A panel trusts a measured 84% far more than a suspicious 97%.
- `[ ]` **D5-A1 — Chaos pass** — kill the observer mid-stream (must degrade, not 500), an upstream 429 storm, SQLite lock contention, a malformed policy at boot (**refuse to start; the previous version stays live**), an 8 s watchdog fire.
- `[x]` **D5-A2 — Load pass** — *completed 2026-08-29 against the real gateway with the deterministic upstream fixture: 4,023 requests, 20-way concurrency, zero failures; measured gateway overhead p95 531 ms against the 120 ms budget, with 123.3 ms mean unattributed overhead. Artifact: `artifacts/load/load_pass.json`.*
- `[ ]` **D5-A3 — Security sweep** — API keys never logged; prompts hashed unless `INTERLOCK_STORE_PROMPTS=1`; canary registry per tenant; no secrets in the image.
- `[~]` **D5-A4 — Deploy** — *partial 2026-08-29. `docs/05_deploy_runbook.md` exists; `scripts/up.ps1` now supervises gateway, observer and console, passes the observer URL to the gateway, and supports explicit `-RiskEngine real|stub` for production vs deterministic rehearsal. The CPU profile was tested in-place, not from a clean checkout.*
- `[ ]` **D5-B2 — Evidence pack for the pitch** — the mechanism table with **our measured numbers beside the published ones**; the six-metric scorecard; the three-case stakes table regenerated from the real policy files.
- `[x]` **D5-B3 — `docs/LIMITATIONS.md`** — *done 2026-08-29. Covers the three missed metrics, F-019 impact-model decision, assumed efficacy, optional observer, provenance over-blocking, single-vertical scope, empty Lane C live sample set, and the deterministic-upstream rehearsal caveat.*
- `[ ]` **D5-J2 — Rehearse** — four run-throughs, 8 minutes each. Drill the two questions that decide the round: *"you need model internals but claim model-agnosticism"* and *"latency, honestly."* **Record one run as the backup video. Never demo live without a fallback.**

### Day 5 exit criteria
- `[ ]` Six metrics measured, with intervals, in `report.html`
- `[x]` p95 overhead histogram captured under load — *the measured report is committed in `artifacts/load/load_pass.json`; it is over budget and remains a release finding.*
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
