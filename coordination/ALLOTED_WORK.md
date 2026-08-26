# Allotted work — Person 1 / Person 2

**Written:** 2026-08-26 · **Repo:** `https://github.com/skb12356/Interlock-AI` (private)

Two people, working in parallel for about a day, then merging. This file says who owns
what. `MERGE_PLAN.md` in this folder says how the two halves come back together.

Read this file first. Read `../CLAUDE.md` second — it is binding on both of us and it
overrides anything here.

---

## 0. The one rule that makes this work

**Person 2 never needs Person 1's machine, Person 1's Ollama, or Person 1's
unfinished code.**

Everything Person 2 builds against is already committed and frozen:

| What | Where | Status |
|---|---|---|
| SSE event shapes | `interlock/core/sse.py` | **Contract 3 — frozen** |
| 12 recorded model streams | `tests/fixtures/streams/*.jsonl` | real recorded output |
| Calibration artefacts | `artifacts/calibration/*.json` | committed |
| Eval results (both modes) | `artifacts/eval/*.json` | committed |
| Measured action latencies | `artifacts/action_latency.json` | committed |
| **Probe accuracy-by-layer curve** | `artifacts/probes/curve.json` | committed — a real chart |
| **Detector sensitivity sweep** | `artifacts/eval/sensitivity.json` | committed — the F-019 experiment |
| A replay server | `scripts/replay_console.py` | **no Ollama, no GPU, no calibration** |

Run this and you have a live SSE stream with real interlock events, on the frozen wire
format, with nothing else installed:

```bash
uv sync --group dev
uv run python scripts/replay_console.py        # serves on http://127.0.0.1:8djson099
```

If Person 2 is ever blocked waiting on Person 1, something has gone wrong with this
split — say so immediately rather than working around it.

---

## 1. Person 1 — Saksham (backend, ML, decision layer)

### What is already done (21 commits, 653 tests passing)

| Area | State |
|---|---|
| Contracts 1–5 | **frozen** and honoured; enforced by contract tests |
| OpenAI-compatible streaming proxy | built; 12 real recorded fixtures |
| Lane A pre-flight | injection / PII / canary / stakes / routing, under a hard deadline |
| Retrieval | hybrid FTS5 + sqlite-vec over 45 docs, RRF fusion |
| Commit gate | text streams one sentence behind; tool calls one *call* behind |
| Intervention ladder | L0–L5, all rungs reachable, verified live |
| Tool interlock | taint × reversibility, durable holds, survives a process kill |
| Calibration + conformal | isotonic, cross-fitted, Learn-then-Test with Hoeffding–Bentkus |
| Real risk engine | on the hot path; no stub in production wiring |
| Governor | five degradation states, circuit breaker, invariant 4 |
| Seeded eval set | 200 conversations, `make eval` prints six metrics |

**Measured, not claimed** — full table in `../IMPLEMENTATION_STATUS.md`:

```
Pre-Action Catch Rate    100.00%   PASS
Added p95 latency          15 ms   PASS
Verification cost          3.53%   PASS
Net spend change         -20.15%   PASS
Ungrounded escapes         0.00%   PASS
False interventions       91.08%   MISS  <- F-019, see below
```

### What Person 1 does next, in priority order

| # | Task | Why it is P1's |
|---|---|---|
| **1** | **F-019 — the impact model** | **Blocks the demo.** At ₹40,000 stakes nothing can pass, so Scene 1 currently *blocks a reasonable answer*. It is a decision about `impact_inr`, `lambda_time` and per-sentence charging — pure decision-layer. |
| 2 | F-021 — capacity fallback | The strong tier (qwen3:8b, 6.6 GiB) does not always fit; the customer currently gets nothing. Needs an explicit, *recorded* fallback — never a silent reroute. |
| 3 | D4-A1/A2/A3 — ledger economics | Per-model pricing, cost-regret with a bootstrap CI, rework attribution. Feeds the console's ledger view. |
| 4 | D4-B1/B2/B3 — Lane C | Fairness twins, e-value martingale, deep-judge anchor + drift. Feeds the console's chart panels. |
| 5 | D2-B3 — 300 hand labels | The anchor set. **Not delegable to a model** — that is the whole point of it. |
| 6 | D2-B4/B5/B6/B7 — the observer | Probe model, KV cache, MiniCheck verifier, trained probes. Needs torch. |
| 7 | D3-A4 — router + semantic cache | The remaining half of Contribution 1's spend side. |
| 8 | D4-A6 — evidence pack export | A `zipfile.write` loop over data already stored. |

### Files Person 1 owns

Everything **except** the Person 2 list below. In particular: `interlock/risk/**`,
`interlock/signals/**`, `interlock/lanec/**`, `interlock/ledger/**`,
`interlock/eval/**`, `interlock/retrieval/**`, `interlock/interlock_tools/**`,
`policies/**`, `scripts/**` (except `replay_console.py`).

---

## 2. Person 2 — the console, the demo UI, the visible half

You are building **everything a judge actually looks at.** None of it exists yet, and
the plan is blunt about why it matters: *"the counterfactual is what makes the demo
land."* Right now the counterfactual is emitted in an SSE event that nobody can see.

### The one-line brief

> Interlock decides. The console **explains decisions already made** — it never renders
> a gauge and waits for a human to pick a threshold. (Architectural invariant 2.)

That constraint is not stylistic. Every screen must answer *"why did it do that?"* and
never ask *"what should it do?"*. A slider that sets a risk threshold is the one thing
this product is arguing against.

### Your tasks

| # | Task | Plan ref | What "done" looks like |
|---|---|---|---|
| **1** | **Split-screen live risk trail** | D4-A4 | Customer's view on the left, streaming. On the right: per-sentence signals, the full six-row loss table, and **the counterfactual** — the text that *would* have shipped. This is the money shot. |
| 2 | Live websocket / SSE plumbing | D2-A7 | Console subscribes to a stream and renders `interlock.stakes`, `interlock.signal`, `interlock.decision`, `interlock.hold` as they arrive. |
| 3 | Review queue | D4-A5 | List pending holds; approve (needs the resume token) / reject (does not). Endpoints already exist and work. |
| 4 | Ledger view | D4-B4 | Spend / regret / rework / running net, **with the CI rendered as a band, never a bare point estimate.** |
| 5 | Chart panels | D4-B5 | Reliability diagram, e-value chart, accuracy-by-layer. Fed from `artifacts/`. |
| 6 | Bank support demo UI | D1-A5 rem. | Minimal chat window pointed at the gateway. `base_url` is the only integration. |

### Files Person 2 owns — and only these

```
console/**                          <- everything, all yours, does not exist yet
interlock/gateway/console_ws.py     <- pre-created stub, already mounted for you
scripts/replay_console.py           <- extend freely
coordination/PERSON2_NOTES.md       <- your scratch notes, create it if useful
```

**Do not edit `interlock/gateway/app.py`.** The websocket router is *already mounted*
for you, so you never need to touch it. That is deliberate — it removes the only file
where we would otherwise collide.

If you find yourself needing to change a file outside that list, **stop and message
Saksham** rather than editing it. That is the single highest-risk thing you can do to
the merge.

### What you are given, precisely

**Frozen event shapes** (`interlock/core/sse.py` — Contract 3, will not change):

```jsonc
// event: interlock.stakes   — once, before the model is called
{"impact_inr": 40000.0, "reversibility": "costly", "domain": "prepayment",
 "mode": "buffered", "stakes_id": "stk_...", "route_reason": "stakes_high",
 "model_served": "qwen3:8b"}

// event: interlock.signal   — per sentence, per detector
{"sentence_idx": 0, "name": "grounding.citation_unsupported", "prob": 0.87}

// event: interlock.decision — per sentence; THIS is the money shot
{"decision_id": "dec_...", "sentence_idx": 0, "action": "L2_repair",
 "chosen_loss": 494.36, "runner_up": "L4_hold", "margin": 88.46,
 "counterfactual": "Prepayment attracts a foreclosure charge of 2% under Clause 7.4.",
 "hard_rule": null, "degraded": false}

// event: interlock.hold     — a durable pending state
{"hold_id": "hold_...", "kind": "tool_call", "reason": "...", "tool": "send_email"}
```

Ask for events with the header `X-Interlock-Events: all`.

**Working HTTP endpoints** (all live today, no work needed from P1):

| Endpoint | Gives you |
|---|---|
| `POST /v1/chat/completions` | the SSE stream, OpenAI-compatible |
| `GET  /health` | engine, retrieval, governor state, policy version |
| `GET  /admin/governor` | state, p95, what was given up, transition log |
| `GET  /admin/latency` | added latency p50/p95, **split by lane**, buffered vs not |
| `GET  /v1/holds` | the review queue (never leaks resume tokens) |
| `POST /v1/holds/{id}/approve` | body `{"resume_token": "...", "resolved_by": "you"}` |
| `POST /v1/holds/{id}/reject` | body optional |

**Committed data to render right now:**

- `artifacts/eval/report.json` — six metrics with Wilson intervals
- `artifacts/eval/report-guaranteed.json` — the same run with the conformal filter on
- `artifacts/calibration/report.json` — ECE, Brier, AUROC, per-bin reliability curve
- `artifacts/calibration/lambda.json` — the certified threshold and its caveats
- `artifacts/action_latency.json` — measured L2/L3 latencies
- `artifacts/probes/curve.json` — accuracy-by-layer for the observer probe. Peaks
  **mid-stack** (layer 4 of 6 at AUROC 0.945), which is the shape that says the probe
  found grounding rather than the encoder's own task head. Worth a chart.
- `artifacts/eval/sensitivity.json` — the F-019 experiment: how good a detector would
  have to be. Two series worth plotting against each other, since they diverge sharply.

### Stack

The plan says **React over websockets**, and the whole system is deliberately
dependency-light (no Redis, no Postgres, no Kubernetes). Keep the console in that
spirit — a small Vite + React app, no component library unless it earns its place.
If you would rather do plain HTML + a bit of JS to move faster on day one, that is
fine; say so in `MERGE_PLAN.md`'s checklist so P1 knows what landed.

### The one number that needs the most careful presentation

`make eval` reports **false interventions at 91.08%** against a ≤2% target. Rendering
that as a big red number would be accurate and would misrepresent the system. Split, it
is:

| band | any intervention | disruptive (L2+) |
|---|---|---|
| < ₹100 | 0% | **0%** |
| ₹100–1,000 | 100% | **17%** |
| ₹1,000–10,000 | 100% | 100% |
| ₹10,000+ | 100% | 100% |

`L1_annotate` appends a citation and ships the answer otherwise unchanged, for 5 ms. At
moderate stakes that is the entire difference between 100% and 17%. If the console shows
only the aggregate, a judge will conclude the system is uniformly over-eager when it is
doing something much more specific: passing cheap traffic, citing sources at moderate
stakes, and verifying everything expensive.

Show both, always. `artifacts/eval/report.json` carries the per-band rows.

### Three things that will make a judge trust it

1. **Show the loss table, all six rows** — including the actions that *could not* be
   taken and why. The table is the explanation; showing only the winner hides the
   argument.
2. **Show the counterfactual side by side** with what shipped. "This is what you would
   have been told" is the entire demo.
3. **Render every rate with its interval.** `artifacts/` already carries Wilson bounds
   for exactly this reason. A bare 91% invites a comparison the data cannot support.

### Do not

- Do not add a threshold slider, a "sensitivity" dial, or anything that asks a human to
  choose a number. Invariant 2.
- Do not display a **raw** detector score as though it were a probability — the
  `interlock.signal` event carries `prob` precisely so you never have to. (ADR-002.)
- Do not commit `node_modules/`, a `.env`, or any API key.
- Do not present the ungrounded-escape guarantee without the intervention rate beside
  it. It is certified *and* currently achieved by intervening on everything (F-016).
  A screen showing only the first half would be the exact kind of technically-true
  claim this project exists to argue against.

---

## 3. Known-bad things you will see, so you do not chase them

These are **recorded findings**, not bugs for you to fix. Full detail in
`../STATE_CHECKPOINT.json`.

| ID | What you will observe | Whose |
|---|---|---|
| **F-019** | High-stakes traffic intervenes ~100% of the time; Scene 1 may **block** a reasonable answer | P1 — blocking |
| F-019b | The false-intervention rate looks catastrophic in aggregate (91%) and much less so split by stakes and by *disruption* — see below | P1 |
| F-021 | Strong tier can 500 with "requires more system memory" | P1 |
| F-016 | Conformal guarantee holds only at a 100% intervention rate | P1 |
| F-015 | On an L4 hold at sentence 0, later sentences still stream | P1 — unsettled |
| F-018 | Semantic-entropy labels not generated | P1 — hardware |

If the console makes any of these *more visible*, that is a feature. Do not hide them.

---

## 4. Cadence

- **Day 1:** P2 builds against `scripts/replay_console.py`. Zero contact needed.
- **End of day 1:** P2 pushes a branch. See `MERGE_PLAN.md`.
- **Day 2:** merge, then both finish remaining work against the merged tree.

Ground rule: **push a branch, never commit to `master` directly.** Both of us.
