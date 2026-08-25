# Interlock — 5-Day Implementation Plan (2 engineers)

**Budget:** 5 days × ~11 focused hours × 2 people = **110 person-hours.** Every task below carries an estimate; the day totals are ≈ 20–22 h/day across both people, leaving ~10% slack. If you blow through the slack, the cut list in §8 tells you exactly what dies and in what order.

**Daily shape**

| Block | Time | Note |
|---|---|---|
| B1 | 09:00 – 13:00 | Deep work. No integration. |
| **CP1** | 13:30 – 13:45 | Screen share, `make demo`, merge, fix red. |
| B2 | 14:00 – 18:00 | Deep work. |
| B3 | 18:30 – 21:30 | Deep work, then hardening. |
| **CP2** | 21:30 – 22:00 | `make demo` + `make eval`, merge, write tomorrow's first task on the board. |

**Roles**

- **Member A — Stream & Enforcement.** Owns `gateway/`, `gate/`, `interlock/`, `ledger/`, `console/`, `deploy/`, CI. The plumbing everything sits on and the thing that must never drop a token.
- **Member B — Signals & Decisions.** Owns `observer/`, `signals/`, `risk/`, `lanec/`, `eval/`, `artifacts/`. The intelligence and the numbers.

The demo is half the score, so **both** people are on product from Day 4 evening.

---

## Repository layout (create this on Day 1, hour 1)

```
interlock/
├── core/            # SHARED — types.py, policy.py, errors.py, ids.py, clock.py
├── gateway/         # A — app.py, openai_compat.py, laneA.py, router.py, cache.py, governor.py
├── gate/            # A — sentence_gate.py, ladder.py, repair.py, segmenter.py
├── interlock_tools/ # A — provenance.py, reversibility.py, holds.py
├── ledger/          # A — writer.py, pricing.py, rework.py, regret.py, api.py
├── observer/        # B — server.py, model.py, probes.py, verifier.py, kvcache.py
├── signals/         # B — injection.py, pii.py, canary.py, stakes.py, fusion.py
├── risk/            # B — engine.py, objective.py, calibration.py, conformal.py, stub.py
├── lanec/           # B — shadow.py, fairness.py, evalues.py, judge.py, drift.py
├── eval/            # B — seed_set/, runner.py, metrics.py, report.py
├── console/         # A shell + B charts — Vite/React
├── policies/        # banking.yaml, defaults.yaml
├── artifacts/       # B — calib/, probes/  (git-lfs or .gitignored + make fetch)
├── migrations/      # NNN_*.sql
├── tests/           # unit/ contract/ property/ chaos/ fixtures/
├── deploy/          # docker-compose.yml, Dockerfile.*, Caddyfile, .env.example
└── Makefile
```

---

# DAY 1 — The spine, and the seam

**Goal at 22:00:** an OpenAI SDK call goes through the gateway to a real provider, streams back token-for-token, carries a stakes estimate and a trace, and the stub risk engine can already force an intervention. Two people can now work for four days without blocking each other.

### 09:00 – 11:00 — JOINT (2 h each, 4 h total). Do not skip this.

| # | Task | Output |
|---|---|---|
| J1.1 | Repo, `pyproject.toml` (uv), ruff+mypy config, pre-commit, GitHub Actions running `ruff && mypy core && pytest` | CI green on an empty repo |
| J1.2 | **Freeze `core/types.py`** — paste `starter/core_types.py`, walk through every field together | Contract 1 frozen |
| J1.3 | Freeze the Observer HTTP contract and the SSE event names (docs/02 §3–§4) | Contracts 2 & 3 frozen |
| J1.4 | `docker-compose.yml` with 4 services + healthchecks; `make up`, `make demo`, `make eval` targets that exist but mostly print TODO | `docker compose up` starts and all healthchecks pass |
| J1.5 | Agree the demo corpus: 45 bank documents (loan T&C, prepayment, claims, branch info, fee schedule, 6 deliberately contradictory pairs) | `corpus/` with a manifest |

> The single highest-leverage two hours of the sprint. Every hour you skip here costs three hours of merge pain on Day 3.

### 11:00 – 13:00 — SPLIT: unblock each other first

**A (2 h)** — `gateway/openai_compat.py`: `POST /v1/chat/completions` streaming + non-streaming, `httpx.AsyncClient` with connection pooling, correct SSE passthrough (chunked, `text/event-stream`, no buffering by any proxy — set `X-Accel-Buffering: no`), provider adapters for OpenAI + Anthropic behind one interface. Record 12 real SSE responses to `tests/fixtures/streams/*.jsonl` — **give these to B before lunch.**

**B (2 h)** — `risk/stub.py` + `observer/mock_server.py`. The stub reads a header `X-Interlock-Force: <defect>@<sentence_idx>` and returns a fully populated `Decision` (real loss table, fake probabilities). The mock observer returns scripted signals with a configurable sleep so A can test the deadline path. **Hand both to A before lunch.**

**CP1 13:30:** A can run `curl` through the gateway and see a forced L2 on sentence 2. Both are now unblocked. ✅

### 14:00 – 18:00 (4 h each)

**A — Lane A skeleton + tracing**
- `laneA.py`: `asyncio.gather` over injection / PII / canary / stakes / cache / route, with a 40 ms `asyncio.wait_for` hard deadline. **Any detector that misses the deadline is dropped, not awaited** — its absence is recorded as a signal with `prob=None`. (1.5 h)
- OpenTelemetry, GenAI semantic conventions (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`) + custom `interlock.*` attributes. Exporter → SQLite span table (skip Jaeger; one less container). (1 h)
- `ledger/writer.py`: bounded `asyncio.Queue`, single writer task, one transaction per request, WAL + `busy_timeout`. Migrations 001 (full schema from docs/01 §3). (1.5 h)

**B — Detectors + stakes v1**
- `signals/injection.py`: `protectai/deberta-v3-base-prompt-injection-v2` via `transformers` on CPU, ONNX-exported for ~8 ms. Runs on the last user turn **and every retrieved chunk separately** (the poisoned-PDF case). (1.5 h)
- `signals/pii.py` (Presidio, en, custom recognisers for PAN/Aadhaar/IFSC/account numbers) and `signals/canary.py` (per-tenant canary registry, egress Aho-Corasick match — `pyahocorasick`, O(n), zero false positives). (1 h)
- `signals/stakes.py` v1: **deterministic feature scorer, no LLM.** Features: retrieved doc collection → domain, monetary regex magnitude, intent keywords, user role from headers, tool schemas present in the request, conversation depth. Output `Stakes` with a rationale list. Domain table comes from the policy file. (1.5 h)

### 18:30 – 21:30 (3 h each)

**A** — Demo application skeleton: a FastAPI + minimal React bank support assistant with real retrieval (`bge-small-en-v1.5` + sqlite-vec over the 45 docs), pointed at the gateway via `base_url`. A governance layer with nothing to govern demos terribly. (3 h)

**B** — Labelling pipeline: script that generates ~1,500 (context, question, answer) triples from the corpus with **induced failures** — retrieval dropped, numbers corrupted, clause IDs swapped, unanswerable questions, contradictory chunk injected. Generate 10-sample answers for the semantic-entropy labels overnight (leave it running). (3 h)

**CP2 21:30 — Day 1 exit criteria**
- [ ] `docker compose up` → 4 healthy services
- [ ] Demo app answers a question through the gateway, streaming, with a trace row
- [ ] `X-Interlock-Force: ungrounded@2` produces a stub L2 decision in the trace
- [ ] Contracts committed and untouched since 11:00
- [ ] Overnight: semantic-entropy label job running

**Day 1 totals:** A 11 h · B 11 h

---

# DAY 2 — Signals become probabilities; tokens become a controllable stream

**Goal at 22:00:** the gate can hold, repair and release a sentence with a real (if not yet calibrated) signal, and every detector emits a calibrated probability with a reliability diagram you can put on a slide.

### 09:00 – 13:00 (4 h each)

**A — The commit gate (the hardest 4 hours of the sprint)**
- `gate/segmenter.py`: incremental sentence segmentation. `pysbd` in streaming mode with a character accumulator; hard flush at 240 chars or `\n\n`; abbreviation guards. **Write the failing tests first**: `Rs. 40,000`, `Clause 7.4`, `e.g.`, `Dr. Rao`, `1. 2. 3.` lists, code fences, mid-word chunk splits. (1.5 h)
- `gate/sentence_gate.py`: the state machine from `starter/sentence_gate.py` — `PASSTHROUGH → BUFFERING → HOLDING → REPAIRING → TERMINATED`, one-sentence buffer, 8 s per-sentence watchdog, monotone escalation (never de-escalates), `already_emitted` bookkeeping. (2 h)
- Property test with Hypothesis: *for any token stream and any decision sequence, no uncommitted sentence is ever emitted, and every token is emitted exactly once or explicitly replaced.* This test is your insurance policy for stage day. (0.5 h)

**B — Calibration**
- `risk/calibration.py`: per-signal 5-fold cross-fitted isotonic regression; ECE, Brier, reliability diagram to PNG. (1.5 h)
- `risk/conformal.py`: Learn-then-Test threshold selection with the Hoeffding–Bentkus bound; input = held-out labels, output = `lambda.json` with the certified `(α, δ)`. (1.5 h)
- Label 300 items by hand (yes, actually — use a 40-line Streamlit/CLI labeller; it takes ~70 min for 300 at 14 s each). **Do not delegate this to a model.** It is the anchor set, the calibration ground truth and the meta-monitor input. (1 h)

**CP1 13:30:** A demonstrates a buffered stream with a forced repair; B shows a reliability diagram for one signal.

### 14:00 – 18:00 (4 h each)

**A — Repair and the ladder**
- `gate/repair.py` (L2): truncate the buffered sentence, re-prompt the same model with `{question, answer_prefix, unsupported_claim, evidence}`, `max_tokens=80`, `stop=["\n"]`; re-verify the replacement through the same risk engine; two failures → escalate to L3. Cost of the repair is charged to the ledger under `component='repair'`. (2 h)
- `gate/ladder.py`: L1 annotate (citation append + hedge softening as a deterministic string transform, no model), L3 reroute (re-retrieve + stronger tier + compare), L4 hold (durable row + review card + SSE `interlock.hold`), L5 block (deterministic only). (2 h)

**B — Observer service, real weights**
- `observer/model.py`: load `Qwen3-1.7B` (8 GB VRAM) or `Qwen3-4B` (if ≥16 GB) with `output_hidden_states=True`, fp16, `torch.inference_mode()`. **CPU fallback profile:** `deberta-v3-base` as the observer encoder — same interface, worse AUROC, runs on a laptop. Both must work; the CPU profile is what a judge runs. (1.5 h)
- `observer/kvcache.py`: prefix KV cache keyed by `context_key` with an LRU of 64 entries. First sentence pays full context prefill; every later sentence pays ~30 tokens. This is the difference between 200 ms and 12 ms per sentence — build it now, not later. (1.5 h)
- `observer/verifier.py`: MiniCheck-class claim verifier (`lytang/MiniCheck-Flan-T5-Large`, 0.8 B) with claim splitting; returns per-claim label + the offending span. **The span is what L2 repairs** — without it, repair has nothing to aim at. (1 h)

### 18:30 – 21:30 (3 h each)

**A** — Governor v1 (`gateway/governor.py`): sliding-window p95, observer circuit breaker (5 failures/10 s → open, 30 s half-open), the 5 degradation states from docs/01 §4.6, exposed at `/admin/governor`. (2 h) + wire the console's live risk trail websocket, rendering the raw SSE events. (1 h)

**B** — Train the probes: extract hidden states at every layer for the labelled set, fit logistic probes per layer, select by held-out AUROC, save `artifacts/probes/<version>/`. Produce the **accuracy-by-layer curve** — it is a genuinely good slide and it takes one matplotlib call. Also fit the verbal-uncertainty probe (hedged/unhedged paraphrase pairs, cheap to generate) → the Overconfidence Index. (3 h)

**CP2 21:30 — Day 2 exit criteria**
- [ ] Gate property test passes; a real stream can be held, repaired and released
- [ ] Observer returns real probe scores in < 25 ms p95 warm (GPU) with KV caching proven by a log line
- [ ] `reliability.png` + `lambda.json` committed with a certified (α=0.01, δ=0.10)
- [ ] Accuracy-by-layer curve exists
- [ ] Governor degrades when you `docker pause interlock-observer`

**Day 2 totals:** A 11 h · B 11 h

---

# DAY 3 — The decision layer and the interlock

**Goal at 22:00:** no stubs left on the critical path. Real signals → real probabilities → real expected-loss table → real action. The tool interlock freezes a poisoned tool call. Scenes 1 and 2 of the demo work end to end.

### 09:00 – 13:00 (4 h each)

**A — Tool interlock**
- `interlock_tools/provenance.py`: the taint lattice; label every context fragment at ingestion (system / user / retrieved_verified / retrieved_untrusted / tool_external); propagate across turns; `influencing_taint(tool_call)` = tier-1 exact/≥0.9-overlap argument match, tier-2 conservative max over untrusted fragments retrieved this turn. (2 h)
- `interlock_tools/reversibility.py` + `holds.py`: policy lookup table, the taint × reversibility matrix, durable pending holds with resume tokens, `POST /v1/holds/{id}/approve|reject`, expiry sweeper. Kill-and-restart test: the hold survives. (2 h)

**B — The risk engine, for real**
- `risk/objective.py`: the four-term expected loss, hard-constraint pre-pass, conformal feasibility filter, then argmin. Full `LossRow` table returned always — the table *is* the explanation. (1.5 h)
- `signals/fusion.py`: logistic fusion over calibrated signals → `P(d)` per defect class, cross-fitted on the same folds as calibration. (1 h)
- `risk/engine.py`: the real `RiskEngine`, replacing the stub behind the identical Protocol. Deadline-aware: if `remaining_deadline_ms` is short, skip the verifier and price with probe-only probabilities, marking `degraded`. Never raises. (1.5 h)

**CP1 13:30 — the big integration.** Swap `StubRiskEngine → RealRiskEngine` in one line of DI wiring. If contracts were honoured, this is the moment it just works. Budget 30 min of pain anyway.

### 14:00 – 18:00 (4 h each)

**A**
- Demo scene 2 wiring: PDF upload path in the demo app, hidden white-text instruction, retrieval marks the chunk `retrieved_untrusted`, agent has a `send_email` tool, interlock freezes it, console raises a review card. (2 h)
- Router + semantic cache (`gateway/router.py`, `cache.py`): RouteLLM `mf` controller checkpoint; **threshold is a function of `Stakes`**, so one estimate drives both spend and scrutiny — this is Contribution 1 and it must be visible in the trace as `route_reason` + the shared `stakes_id`. Cache serves only when cosine ≥ 0.95 **and** retrieval-context hash matches **and** stakes ≤ threshold **and** the cached answer previously passed verification. (2 h)

**B**
- `risk/stakes v2`: add a small intent classifier over the demo corpus domains; keep it deterministic-by-default with the classifier as one feature among several, so the rationale stays human-readable. (1.5 h)
- Efficacy matrix v1 from data: run the current pipeline over the labelled set with each action forced, measure how much of each defect class each action actually removes, write `efficacy` back into the policy file with Wilson intervals. **This turns §4.2's weakest assumption into a measurement.** (2.5 h)

### 18:30 – 21:30 (3 h each)

**A** — Agent loop breaker: detect repetition/oscillation in tool-call sequences (n-gram repeat on `(tool, args_digest)` with a 3-strike rule + context-growth slope check), cut the loop, log the saved spend. (1.5 h) + latency instrumentation: `overhead_ms` measured per request, split by lane, exported as a histogram. You need the p95 number to be *measured* on Day 5. (1.5 h)

**B** — `eval/` harness skeleton + seeded set v1: 200 conversations, 60 induced failures (15 missing-retrieval, 10 number corruptions, 10 poisoned docs, 8 canary/PII, 10 demographic twin pairs, 7 loop-inducing agent tasks), each with machine-checkable ground truth. `make eval` runs it twice — Interlock off, Interlock on — and prints all six metrics. (3 h)

**CP2 21:30 — Day 3 exit criteria**
- [ ] No stub on the hot path
- [ ] Scene 1 (invented clause → held → repaired → cited) works live
- [ ] Scene 2 (poisoned PDF → tool frozen → review card) works live
- [ ] `make eval` prints six numbers, even if they're bad
- [ ] Router consumes the same `Stakes` object the risk engine does — provable from one trace

**Day 3 totals:** A 11 h · B 11 h

---

# DAY 4 — The money, the fairness, the console

**Goal at 22:00:** the ledger produces a defensible net-savings number, the fairness run produces an e-value, and the console is something you'd be happy projecting.

### 09:00 – 13:00 (4 h each)

**A — The ledger**
- `ledger/pricing.py` (per-model INR token prices, config-driven) + `regret.py`: shadow-sample 5% of traffic, replay on the cheaper tier, store verdicts, estimate population regret with a bootstrap CI. (2 h)
- `ledger/rework.py`: the session graph. Retry detection (cosine ≥ 0.90 to the previous user turn within 120 s), explicit regenerate, human escalation from holds. Charge child cost — and the policy's human cost — back to the parent `request_id`, with the confidence stored on the edge. (2 h)

**B — Lane C**
- `lanec/fairness.py`: counterfactual twins — mutate name/gender/age markers via templates on sampled real queries, run both, extract decision-relevant fields (approved?, amount quoted, hedge count) with a structured extractor, store pairs. (2 h)
- `lanec/evalues.py`: sequential test with a betting martingale, `e_t = Π(1 + λ_t(X_t − μ₀))`, alert at `e_t ≥ 1/α`; always-valid p = 1/max e. One chart: e-value over time with the alert line. (1.5 h)
- `lanec/judge.py`: the ~1% offline calibration anchor + `drift.py` meta-monitor that re-scores the human anchor set nightly and reports when to stop trusting the fast lanes. (0.5 h)

### 14:00 – 18:00 (4 h each)

**A — Console, for real** (Vite + React + Tailwind + Recharts)
- Split-screen live risk trail: customer view left, per-sentence signals + loss table + counterfactual right. (2 h)
- Review queue with approve/reject on holds; evidence pack export (zip of decisions + inputs + loss tables + policy/calib versions + reviewer) — this is your EU AI Act Art. 12/14 artefact and it's a `zipfile.write` loop over data you already have. (2 h)

**B — Console charts + eval polish**
- Ledger view: spend / regret / rework / running net, with the CI rendered as a band (never a bare point estimate). (1.5 h)
- Reliability diagram, accuracy-by-layer, e-value chart as console panels fed from `artifacts/`. (1 h)
- Seeded set v2: expand the failure taxonomy, fix leakage between the calibration set and the eval set (**critical: they must be disjoint, and you must be able to say so**). (1.5 h)

### 18:30 – 21:30 (3 h each) — JOINT: first full rehearsal

Run all four demo scenes end to end, on the real box, twice. Write down every rough edge. Fix the top five. Everything else goes on a list you deliberately do not fix.

**CP2 21:30 — Day 4 exit criteria**
- [ ] Ledger shows a net number with a confidence interval
- [ ] Fairness run produces an e-value chart from sampled traffic
- [ ] All four scenes run without a human touching a terminal mid-scene
- [ ] Evidence pack downloads and opens
- [ ] Calibration set and eval set are provably disjoint

**Day 4 totals:** A 11 h · B 11 h

---

# DAY 5 — Measure, harden, deploy, rehearse

**Goal at 22:00:** the six numbers are measured (not aspirational), the thing is live on a public URL, and you have run the pitch four times.

### 09:00 – 13:00 (4 h each)

**Joint 09:00 – 10:00** — Freeze the feature set. **No new features after this hour.** Write the freeze on a whiteboard where you can both see it.

**B (3 h)** — The measurement run: `make eval` on the full seeded set, off vs on, three seeds, produce `report.html` with all six metrics and their intervals. Fill in the blanks on the final slide *only now*. If Pre-Action Catch Rate is below 90%, do not tune the eval — report what you got and explain the failure modes. A panel trusts a measured 84% far more than a suspicious 97%.

**A (3 h)** — Hardening pass:
- Chaos: `docker kill observer` mid-stream (must degrade, not 500), upstream 429 storm, SQLite lock contention, malformed policy at boot, 8 s watchdog fire.
- Load: `vegeta`/`locust` at 20 concurrent streams for 5 minutes → capture the p95 overhead histogram. **This number goes on the slide.**
- Security sweep: API keys not logged, prompts hashed unless `STORE_PROMPTS=1`, canary registry per tenant, no secrets in the image.

### 14:00 – 18:00

**A (4 h) — Deploy.** See `docs/05_deploy_runbook.md`. Target: a single GPU VM (g5.xlarge / RunPod A10G) with Caddy + automatic TLS on a real domain, plus the **CPU-only compose profile** that a judge runs on a laptop in one command. Both paths must be tested from a clean checkout — clone into `/tmp` and follow your own README. You will find three bugs. Everybody does.

**B (4 h)** — Evidence pack for the pitch: the §7 mechanism table with *your* measured numbers beside the published ones; the six-metric scorecard; the three-case stakes table regenerated from real policy files rather than the slide's illustrative numbers. Then write `docs/LIMITATIONS.md` — the things you know are thin (provenance heuristic, single vertical, fairness sample size, efficacy CIs). **Volunteer these before the panel finds them.** It is the highest-ROI page in the deck.

### 18:30 – 21:30 — JOINT: rehearse

Four run-throughs, 8 minutes each, alternating who drives. Then rehearse the defence answers from §11 of the pitch out loud — especially "you need model internals but claim model-agnosticism" and "latency, honestly", which are the two that decide the round. Record one run as the backup video. Never demo live without a recorded fallback.

**Day 5 totals:** A 11 h · B 11 h

---

## 8. Cut list — in this order, no debate at the time

Cutting is a decision you make now, calmly, not at 03:00 on Day 5.

1. Drift monitor / meta-monitor → one static chart from a single run.
2. Fairness twins → pre-recorded run, **stated as pre-recorded on the slide**.
3. Shadow replay → fixed offline sample, no live sampling.
4. Semantic cache → exact-match only.
5. Agent loop breaker → detection + logging, no cutting.
6. L3 reroute → collapses into L4 hold.
7. Trained probe → fall back to the observer's own logprob/entailment proxy (worse AUROC, same interface, ~30 min swap).
8. Console → ship the risk trail and the ledger only; drop the charts panel.

**Never cut:** the calibration step, the commit gate, the tool interlock, the seeded eval set. Those four are what make it real rather than staged, and each is exactly what a technical judge will probe.

## 9. Risk register

| Risk | P | Impact | Mitigation | Trigger for the fallback |
|---|---|---|---|---|
| Probe AUROC < 0.7 on the demo corpus | med | high | Ship the fused verifier + logprob proxy; the architecture is unchanged and the story survives | Day 2, 21:30 |
| GPU unavailable / OOM | med | high | CPU observer profile built on Day 2, not improvised on Day 5 | Day 2, 18:00 |
| Sentence segmentation eats the demo | med | fatal | Property + abbreviation tests written Day 2 morning, before the gate | Day 2, 11:00 |
| Provider rate limits during the demo | high | high | Response cache keyed on the exact demo prompts, primed before you walk on stage | Day 5, 14:00 |
| Merge hell in `core/` | low | high | `core/` edited only at checkpoints | continuous |
| Labelling drags past 90 min | med | med | Cut to 200 labels; widen δ to 0.15 and say so | Day 2, 13:00 |
| Net savings comes out positive (oversight costs money) | low | fatal to the pitch | You will know by Day 4 CP2. Fix by raising the cache/route share on low-stakes traffic; if it's still positive, **report it honestly and show the break-even traffic mix** | Day 4, 21:30 |

## 10. If you had a 6th day

In priority order: (1) a second vertical to prove the policy file is the only thing that changes; (2) replace the provenance heuristic with real dataflow tracking; (3) train the router on your own regret ledger and show the flywheel closing; (4) Postgres + a second observer replica to make the scale story concrete rather than theoretical.


