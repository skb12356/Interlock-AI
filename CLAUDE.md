# CLAUDE.md

## 1. Project Overview

### What this project is

**Interlock** is an AI control plane that unifies two systems every enterprise AI stack builds separately: the **router** that decides how much compute to spend on a request, and the **guardrail** that decides how hard to check the answer. Both are answering the same question — *how much does this particular request matter?* — so Interlock computes one **stakes estimate** per request and spends both budgets from it. Money saved on the ~80% of traffic that never needed a frontier model pays for the deep checking on the ~20% that did. Oversight funds itself instead of being a tax.

The product is an **OpenAI-compatible streaming proxy**: a client points `base_url` at Interlock, and Interlock sits transparently in front of any model (GPT, Claude, Gemini, open weights — none of them modified). Around every request it runs three lanes: **Lane A (pre-flight, ~25 ms)** — injection, PII, canary-token, stakes estimation, semantic cache, and model routing; **Lane B (in-flight)** — a small (~4B) generator-agnostic *observer model* whose linear probes detect likely hallucination from a single forward pass, plus MiniCheck-class claim-level groundedness, running concurrently with generation behind a **one-sentence commit buffer**; and **Lane C (offline, off the critical path)** — counterfactual fairness twins, shadow replay on cheaper models, a ~1% deep-judge calibration anchor, and drift tests. A control plane converts every calibrated risk signal into **expected loss in one currency (₹)** and picks the cheapest safe action on an intervention ladder: Pass → Annotate → Repair the bad sentence → Reroute → Hold for a human → Block.

The primary users are enterprises running LLM assistants and agents in high-stakes domains; the reference deployment is a **bank customer-support assistant over a real document corpus (40–60 docs)**, with tool-calling (payments, email) gated by a provenance-aware tool-call interlock. The audience for the built artefact is the **Accenture Innovation Challenge 2026** judging panel (Problem Statement 1, ControlPlane.AI): the demo, the measured numbers, and the evidence pack are first-class deliverables, not afterthoughts.

The north-star metric is the **Pre-Action Catch Rate**: the share of defects stopped *before* a human or a tool acted on them. Targets the implementation must be measured against from the start: catch rate ≥ 90%, added p95 latency ≤ 120 ms (time-to-first-token statistically unchanged), verification cost ≤ 5% of model spend, net spend change ≈ −30%, ungrounded escapes ≤ 1% (conformally bounded at 90% confidence), false interventions ≤ 2%.

Every mechanism is either a published result being re-implemented (semantic-entropy probes, generator-agnostic observers, MiniCheck, conformal factuality thresholds, RouteLLM-style pre-generation routing, SentGuard-style sentence-commit streaming, CaMeL-style tool interlocks, anytime-valid fairness statistics) or an honestly-labelled self-innovation (the unified stakes estimate, the expected-loss objective, the cost-regret/rework ledger, the Pre-Action Catch Rate metric). Do not invent novelty claims; cite the source instead.

### Primary objective
Build a production-quality implementation of the requirements described in:
`IMPLEMENTATION_PLAN.md`

The implementation plan is the source of truth for product requirements. The design rationale, evidence base, and target numbers behind it live in `Interlock-v2.pdf` at the repository root; when the plan is silent, that document governs intent.

---

# 2. Core Instructions

You are the primary engineering agent for this repository.

Before making significant changes:

1. Read this file completely.
2. Read `IMPLEMENTATION_PLAN.md` completely.
3. Read `IMPLEMENTATION_STATUS.md` if it exists.
4. Inspect the existing repository and understand its architecture.
5. Check existing tests before creating new implementations.
6. Do not assume that the implementation plan perfectly matches the existing codebase.

Do not blindly follow an implementation approach if it conflicts with:
- the existing architecture
- current library/API behavior
- security requirements
- data integrity
- established project conventions

Preserve the intended product behavior while choosing the technically correct implementation.

---

# 3. Engineering Principles

Always prioritize:

1. Correctness
2. Completeness
3. Reliability
4. Security
5. Maintainability
6. Testability
7. Performance
8. Simplicity

Prefer simple, understandable solutions over unnecessary abstraction.

Do not introduce frameworks, libraries, services, or architectural patterns unless they provide a clear benefit.

Do not rewrite working code unnecessarily.

Before creating a new utility/helper/module, check whether an existing one already solves the problem.

Project-specific corollaries:

- **Prefer deterministic checks over model-based checks** wherever one exists (canary-token egress matching, tool-policy rules, provenance checks). Cheap tricks that are provably correct beat clever ones that are probably correct.
- **Never put a generative LLM judge on the hot path.** Generative judging is confined to ~1% of traffic, offline, as a calibration anchor only.
- **Scores are not probabilities until calibrated.** Every detector signal must pass through isotonic calibration and conformal thresholding before any expected-loss arithmetic uses it. Skipping calibration turns the decision layer into decoration.
- **Latency is part of the objective, not a side constraint.** Only Lane A (~25 ms) and the one-sentence commit buffer may touch the user's critical path. Everything else runs concurrently with generation or fully offline.

---

# 4. Repository Structure

Understand the repository structure before modifying it.

The repository is young; the intended layout is:

```text
src/interlock/         → the control plane
  proxy/               → OpenAI-compatible FastAPI proxy, SSE streaming passthrough
  lanes/preflight/     → Lane A: injection, PII, canary, stakes model, cache, router
  lanes/inflight/      → Lane B: observer probe client, claim verifier, risk forecast
  lanes/offline/       → Lane C: fairness twins, shadow replay, deep-judge anchor, drift
  decision/            → expected-loss optimiser, intervention ladder, commit gate
  interlock/           → tool-call interlock: provenance tracking, reversibility policy
  calibration/         → isotonic calibration, conformal thresholds, meta-monitor
  ledger/              → cost-regret ledger, rework attribution, ROI accounting
  policies/            → versioned stakes/policy files (policy-as-code), per-industry defaults
  storage/             → SQLite + DuckDB persistence: traces, policies, pending holds
observer/              → the ~4B observer model service and linear-probe artefacts
demo/                  → bank support assistant demo app + document corpus
console/               → React operator console (websockets); explains decisions, never asks for them
evals/                 → seeded evaluation set (200 conversations, 60 induced failures), labels, metrics
tests/                 → unit and integration tests
scripts/               → labelling, probe training, calibration, replay tooling
config/                → deployment configuration, docker-compose
docs/                  → documentation; Interlock-v2.pdf is the design source document
```

Create directories as the phases in `IMPLEMENTATION_PLAN.md` require them; do not scaffold empty trees speculatively.

---

# 5. Technology Stack

Deliberately smaller than it wants to be:

- Python 3.12, FastAPI with SSE streaming
- One ~4B open observer model served via vLLM (plain `transformers` fallback if GPU is scarce)
- SQLite + DuckDB for everything stateful — traces, policies, pending holds
- OpenTelemetry GenAI-convention traces on every call
- React console over websockets
- Docker Compose so the whole system runs in one command

Explicit omissions: **no Redis, no Postgres, no ClickHouse, no Kubernetes.** One process, one file-backed store, one command. Do not add infrastructure services without a demonstrated need recorded in `IMPLEMENTATION_PLAN.md`.

---

# 6. Architectural Invariants

These are load-bearing. Do not violate them without an explicit, recorded decision.

1. **One stakes estimate, two budgets.** The router and the guardrail must consume the same stakes estimate. Never fork them into separately tuned systems.
2. **Decisions, not scores.** Every detector output ends in a chosen action from the intervention ladder (L0 Pass · L1 Annotate · L2 Repair · L3 Reroute · L4 Hold · L5 Block), selected by minimising expected loss: surviving harm + false-alarm nuisance + compute cost + λ·latency. The console explains decisions already made; it never renders a gauge and waits for a human threshold.
3. **The commit gate is sacred.** Text streams one sentence behind generation so a bad sentence can be repaired before anyone reads it. The commit gate and the calibration step are never cut, stubbed, or bypassed — under any schedule pressure. (The documented cut order when time runs short: fairness twins → pre-recorded run; shadow replay → fixed sample; trained probe → logprob/entailment proxy.)
4. **Fail open on low stakes, fail closed on high stakes.** Interlock has its own latency budget and a load governor with a defined degradation order: background analysis thins first, then live-check depth; if the risk engine still misses its deadline, low-stakes traffic passes and high-stakes traffic holds.
5. **Tool calls are gated on action semantics, not words.** The tool-call interlock evaluates what the action does, whether it is reversible, and the provenance of the instruction that triggered it. Irreversible actions (money, email, deletes, external writes) triggered by untrusted content freeze into a durable pending state that survives a restart and wait for a human.
6. **Canary tokens are per-tenant, planted in both the system prompt and the retrieved corpus, and matched on egress.** A canary match is a deterministic L5 hard stop — no model in the loop.
7. **Model-agnosticism is the moat.** Never require generator internals. Hallucination probing runs on the observer model's own residual stream over (context, question, candidate answer). The proxy must work unchanged against any OpenAI-compatible backend.
8. **Close the loop.** Every caught incident becomes a permanent regression test; every detected drift retunes a threshold. The meta-monitor continuously re-scores a human-labelled anchor set so the system can report when its own detectors are no longer trustworthy.
9. **Honest accounting.** The ledger reports waste (cost-regret) and rework attribution with confidence intervals, not just spend. Assume the conservative end of published ranges (e.g. 20–45% cache hit rates, not 60–70%).

---

# 7. Non-Goals

Deliberately not built — do not drift into them:

- Another observability dashboard that asks humans to make decisions.
- Judge-everything-with-a-frontier-model (slow, expensive, documented to be overconfident and self-preferring).
- Bias reports from public benchmarks about somebody else's model.
- Our own foundation model.
- Five industry verticals configured thinly. One vertical (banking support) done convincingly.

---

# 8. Testing and Verification

- Follow test-driven development: write the failing test before the implementation.
- The **seeded evaluation set** (`evals/`) is the proof of the product: 200 conversations with known ground truth, including 60 deliberately induced failures (missing retrieval, poisoned documents, demographic twin pairs, loop-inducing agent tasks). The headline number is the metric delta with Interlock off vs on.
- All six target metrics (§1) are measured continuously from the first working phase — the finale must be a measurement, not an aspiration. Never report a target as achieved without a run that produced the number.
- Latency claims require measured p50/p95 with and without Interlock, including time-to-first-token.
- Calibration quality ships with reliability diagrams; conformal guarantees ship with the coverage level they were computed at.
- Fairness monitoring uses anytime-valid tests (e-values / always-valid p-values), never repeated ordinary significance tests.

---

# 9. Workflow

- Build in the phase order of `IMPLEMENTATION_PLAN.md` (spine → signals + calibration → observer probe → decision layer → economics + fairness). Every phase must end in something demonstrable.
- Keep `IMPLEMENTATION_STATUS.md` updated as phases progress: what is built, what is measured, what is stubbed, and any recorded deviations from the plan with their rationale.
- Policy files are versioned, diffable artefacts — treat threshold and stakes changes as reviewed code changes, never as inline constants.
- Never commit secrets, API keys, or tenant canary strings. Configuration comes from the environment or `config/`.
- Commit in small, coherent units with messages that state what changed and why.
