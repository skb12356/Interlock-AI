<div align="center">
  <img src="docs/images/logo.svg" alt="Interlock" width="100%" />
  <h3>Routing and guarding are the same decision.</h3>
  <p><em>Estimate what a request is worth once, then spend both the compute budget and the checking budget out of that one number.</em></p>

[![CI](https://github.com/skb12356/Interlock-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/skb12356/Interlock-AI/actions/workflows/ci.yml)
[![Architecture](https://img.shields.io/badge/docs-architecture-c8b4a0)](docs/ARCHITECTURE.md)
[![Evidence](https://img.shields.io/badge/evidence-artifacts%2F-9ad17f)](artifacts/)
[![Limitations](https://img.shields.io/badge/read-limitations-d9705f)](docs/LIMITATIONS.md)

</div>

---

## Contents

[The problem](#the-problem) · [The solution](#the-solution) · [What it looks like](#what-it-looks-like) ·
[How one request travels](#how-one-request-travels) · [The ladder (L0–L5)](#the-ladder-l0l5) ·
[The maths](#the-maths-surface-level) · [Results](#results) · [Run it](#run-it) ·
[Repository map](#repository-map) · [Documentation](#documentation) · [Demo video](#demo-video) ·
[Research foundations](#research-foundations) · [What is ours](#what-is-ours) ·
[Known limitations](#known-limitations)

---

## The problem

A bank puts an AI assistant in front of customers. Most of what it is asked is harmless —
branch timings, balances, how to reset a card. A few things are not: an invented penalty
clause, a settlement date no document supports, an email that should never have been sent.

Both kinds arrive through the same endpoint, and you cannot tell which is which until the
answer already exists. Today, teams respond by building two separate systems:

- a **router**, which asks *how much compute does this deserve?* — and optimises cost;
- a **guardrail**, which asks *how hard should I check this?* — and optimises safety.

They are tuned separately, budgeted separately, and argue with each other. The guardrail is
a pure cost line, so it gets cut. And because checking usually runs *after* generation, the
customer waits for it, which is the other reason it gets switched off.

There is a third failure that neither system addresses: **a guardrail that emits a score
does not make a decision.** "Groundedness 0.72" hands a human a threshold file. Somebody
picks 0.7, everything above it is blocked, half the blocks are wrong, and in week two the
whole thing is disabled.

## The solution

Both questions above are the same question — *how much does this request matter?* — so
Interlock computes it **once**, as an amount of money, and spends both budgets from it.

Interlock is an **OpenAI-compatible streaming proxy**. A client points `base_url` at it and
changes nothing else; the model behind it is unmodified and never sees Interlock. Around
every request it runs three lanes:

- **Lane A (pre-flight)** — injection, PII and canary checks, retrieval, the stakes
  estimate, the semantic cache, and the model routing decision.
- **Lane B (in-flight)** — a small observer model with linear probes, plus claim-level
  grounding, running **concurrently with generation** behind a one-sentence commit buffer.
  The reader is always looking at sentence *n* while sentence *n+1* is checked.
- **Lane C (offline)** — fairness twins, shadow replay, a ~1% deep-judge calibration
  anchor, drift tests. Never on the critical path.

Every calibrated signal is converted into **expected loss in rupees**, all six possible
responses are priced, and the cheapest safe one is chosen. Money saved by not over-routing
the cheap 80% pays for deep checking on the expensive 20%, so oversight funds itself instead
of being a tax.

The headline metric is the **Pre-Action Catch Rate**: the share of defects stopped *before*
a person read them or a tool acted on them. It measures the whole control path, not model
accuracy.

## What it looks like

The console is where a request becomes legible. Ask a question like any assistant:

![The chat workspace: two turns, each with the seven Interlock stages and the action Interlock took](docs/images/console-chat-session.jpeg)

Every answer carries the stages that produced it, and **see it live** opens the full trace.
Stage 04 prices all six actions and keeps the losers on screen, because the point is not
which rung won but *why*:

![Stage 04: all six actions priced in rupees, L2 repair chosen at ₹494.36 against a runner-up of L4 hold](docs/images/console-trace-ladder.jpeg)

Stage 06 shows what the customer actually saw, the stamp for what was done to it, and what
would have shipped without Interlock:

![Stage 06: the released answer, the L2 REPAIR stamp, and the counterfactual that would have shipped](docs/images/console-trace-release.jpeg)

Holds wait for a human, with the evidence and the flagged span attached, and a link back to
the conversation that caused them:

![The reviews queue: pending holds with evidence, flagged span, SLA and resume-token state](docs/images/console-reviews.jpeg)

The evidence ledger reads committed artifacts — including the target it currently misses:

![The evidence ledger: calibration, target checks and measured action latency read from artifacts](docs/images/console-evidence.jpeg)

<details>
<summary>More screenshots</summary>

![The empty chat workspace](docs/images/console-chat-empty.jpeg)

![Stage 01, pre-flight: stakes, reversibility, gate mode and routing](docs/images/console-trace-preflight.jpeg)

![The About workspace explaining the system in plain language with its citations](docs/images/console-about.jpeg)

</details>

## How one request travels

```mermaid
flowchart LR
    Client[Client<br/>OpenAI-compatible] --> A[Lane A · pre-flight<br/>stakes · detectors · retrieval · routing]
    A --> M[Upstream model<br/>unmodified]
    M -- tokens --> Gate[Commit gate<br/>one sentence behind]
    Gate --> Client
    M -.runs concurrently.-> B[Lane B · in-flight<br/>observer probe + claim verifier]
    B --> D[Control plane<br/>calibrate → price six actions → choose]
    D --> Gate
    D --> Ledger[(Ledger)]
    Ledger --> C[Lane C · offline<br/>fairness · replay · drift]
```

1. **Estimate the stakes** from domain, the largest amount in the text, and who is asking.
2. **Run the cheap deterministic checks**: injection, PII, canary.
3. **Route** on stakes first, difficulty second — before paying any model.
4. **Generate**, streaming one sentence behind so a bad sentence can still be repaired.
5. **Check while generating**: observer probe plus claim-level grounding.
6. **Calibrate** raw scores into probabilities, then **price all six actions** in rupees.
7. **Act**: pass, annotate, repair, reroute, hold or block — cheapest safe rung wins.
8. **Record** the decision, evidence, spend and latency; sample offline checks afterwards.

> **Full detail, with every formula and its source: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## The ladder (L0–L5)

Blocking everything suspicious makes an assistant useless; blocking nothing makes it
dangerous. So there are six rungs, and each one is **priced before one is chosen**.

| Rung | What it does | What it costs | When it wins |
|---|---|---|---|
| **L0 · Pass** | Release the sentence unchanged. | 0 ms | Calibrated risk is low. Most traffic ends here. |
| **L1 · Annotate** | Add a citation, hedge or flag — deterministic text, no regeneration. | ~0 ms | Mild uncertainty: the reader should know which part to check. |
| **L2 · Repair** | Regenerate **only the defective sentence**, with the evidence attached. | 13.7 s measured median | One localized factual defect, and the verifier returned the offending span. |
| **L3 · Reroute** | Regenerate on the stronger tier, re-retrieving first. | 30.7 s measured median | The weak model or the retrieval was the problem, not one sentence. |
| **L4 · Hold** | Freeze into a durable pending state and wait for a human. | ₹220 reviewer cost, SLA-bound | Irreversible action, or a cost of being wrong that exceeds a person's time. |
| **L5 · Block** | Emit no unsafe content at all. | ₹220, and the interaction is lost | A deterministic rule fired — a canary token, or an injected instruction driving an irreversible action. **No model is in this loop.** |

Two properties matter more than the list:

- **The ladder shrinks as the answer travels.** Before anything is sent, every rung is
  available. Once a sentence has reached the reader, L2, L3 and L5 are gone — you cannot
  un-say something, and the loss table says so explicitly instead of pretending.
- **Hard rules run before the arithmetic.** A canary match or a tool-policy violation
  short-circuits to L4/L5; the optimiser then picks the cheapest action among what is left.

L2 and L3 medians are measured on `qwen3:8b` via Ollama, 3 runs each
([`artifacts/action_latency.json`](artifacts/action_latency.json)) — the ladder prices those
real latencies, which is why L3 rarely wins on cheap traffic.

## The maths (surface level)

Enough to follow the decision. Each block names the published work it comes from; the full
derivations are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### 1. What is this request worth?

```
impact_inr = base_impact(domain) × monetary_multiplier(largest ₹ amount) × role_multiplier
```

carried with `reversibility ∈ {reversible ×1.0, costly ×2.5, irreversible ×8.0}`. A branch
timing is ₹50. A payment question is ₹25,000 and irreversible. Every number is in
[`policies/banking.yaml`](policies/banking.yaml) — a reviewed file, not a constant in code.

### 2. How suspicious is the answer?

Six deterministic grounding signals (unsupported content, unsupported numbers, unsupported
citations, context conflict, question drift, overconfidence) plus a linear probe on an
observer model's residual stream:

```
probe score = σ(wᵀ h_ℓ + b)      — one forward pass, one probe per layer, layer chosen on held-out AUROC
```

*Source:* semantic entropy is the strongest published hallucination signal but needs ~10
samples per question (Farquhar et al., *Nature* 630, 2024, "Detecting hallucinations… using
semantic entropy"), so it is used **offline as a label generator only**. The deployable form
is the single-pass probe of Kossen et al. (arXiv:2406.15927, §3, "Semantic Entropy Probes"),
and running that probe on a **different** model from the generator is what keeps Interlock
model-agnostic (O'Neill et al., arXiv:2507.23221).

Claim-level grounding uses a MiniCheck-class verifier (Tang, Laban & Durrett, EMNLP 2024) —
a 770M model reaching GPT-4-level fact-checking cheaply — and, crucially, it returns **the
offending span**, which is what L2 repair aims at.

### 3. Turning a score into a probability

A raw score is not a probability, and pricing in rupees with an uncalibrated score is
arithmetic that only looks rigorous. Two steps:

```
per signal:   g(s) = isotonic fit,  minimising Σ (g(sᵢ) − yᵢ)²  subject to monotonicity
fused:        P(defect) = σ( β₀ + Σ_k β_k · g_k(s_k) )
across defects: P(any) = 1 − Π_d (1 − P(d))
```

Fitted with 5-fold stratified CV; **every reported number is out-of-fold**, because fitting
and scoring on the same data drives calibration error to zero no matter how bad the model is.

*Source:* Zadrozny & Elkan (2002), "Transforming Classifier Scores into Accurate Multiclass
Probability Estimates" — the isotonic step is §3 of that paper.

### 4. Choosing the action

```
E[L(a)] =  Σ_d P(d) · Impact_d · (1 − eff[a][d])     (1) harm that survives the action
        +  (1 − P(any)) · Nuisance(a)                (2) the cost of a false alarm
        +  tokens(a) · price + human_cost(a)         (3) compute, and a reviewer's time
        +  λ_time · Δlatency(a) / 1000               (4) the customer's waiting, priced

Impact_d = impact_inr × defect_multiplier[d] × reversibility_multiplier
```

with `λ_time = ₹0.40/s`, `price = ₹0.60 / 1k tokens`, `human_review = ₹220`. Choose
`argmin_a E[L(a)]` over the actions that are still available.

Term (2) is what stops over-blocking — and over-blocking is what gets guardrails switched
off. Human cost is charged unconditionally on L4 and L5: the reviewer is paid whether or not
the answer turns out to have been fine.

*Source:* the reject option in selective classification (Geifman & El-Yaniv, 2017,
"Selective Classification for Deep Neural Networks") — trade coverage against error instead
of answering everything. Interlock's addition is pricing that trade in money, and putting
"escalate to a stronger model" and "escalate to a human" on the same axis, as framed by
*Cascaded Language Models for Cost-Effective Human–AI Decision-Making* (NeurIPS 2025).

### 5. Turning a threshold into a promise

Sweeping thresholds and quoting the best one is multiple testing: the winner is partly
lucky, and the quoted rate is optimistic by an unstateable amount. Interlock selects the
threshold with **Learn-then-Test**: each candidate λ is a hypothesis, its p-value comes from
the **minimum of Hoeffding's and Bentkus's bounds** (Bentkus is far tighter in the rare-event
regime), and **fixed-sequence testing** walks thresholds from strictest to loosest, spending
no correction budget because the ordering itself carries information.

What comes out is a defensible sentence: *at most 1% ungrounded escapes, at 90% confidence,
on n = 840 held-out items.*

*Source:* Angelopoulos et al. (2022), "Conformal Risk Control" — distribution-free risk
control by threshold selection rather than eyeballing.

### 6. Watching fairness without crying wolf

Lane C compares counterfactual twins — the same request with protected attributes varied —
and monitors the disparity continuously. Continuous monitoring with ordinary significance
tests manufactures false alarms, so the monitor uses **e-values**, whose validity survives
optional stopping.

*Source:* Koolen & Grünwald (2022) on log-optimal anytime-valid e-values, and Henzinger et
al. (2023) on monitoring algorithmic fairness at runtime.

## Results

All figures are read from committed artifacts, not typed into this file. Regenerate them
with `make eval` / `make calibrate`.

### The release targets

| Metric | Measured | Target | Verdict |
|---|---:|---|:--:|
| Pre-Action Catch Rate | **100%** (43/43, CI 0.918–1.000) | ≥ 90% | ✅ |
| Added p95 latency | **0.40 ms** (decision path, excludes generation) | ≤ 120 ms | ✅ |
| Ungrounded escapes | **0.0%** (0/25, CI 0–0.133) | ≤ 1% @ 90% conf | ✅ |
| Verification cost | **5.58%** of model spend (modelled) | ≈ 6% | ✅ |
| Net spend change | **−17.8%** | ≈ −15% | ✅ |

Source: [`artifacts/eval/report.json`](artifacts/eval/report.json), 200 seeded conversations
with 43 induced defects. The artifact is generated by `make eval` and carries its own
target strings, which are the earlier, more aggressive ones.

There is a known behaviour behind the spend figures, recorded as finding F-019: at ₹40,000
impact with a 2.5× reversibility multiplier, `L0_pass` only wins if `P(defect) < ~0.0001`,
while the detector's floor on clean text is ~0.02. Nothing passes above ₹10,000 — the
objective working correctly on an impact model that is deliberately conservative. The
measured `banking-v4` policy adjustment was selected from 216 bounded candidates across
three immutable seeds, and preserves 100% pre-action catch with zero grounding escapes.

Net spend has two stated causes rather than one unknown: 57% of the seeded set is ₹10,000+
traffic that the stakes threshold forces to the strong tier, and **no cache hit is modelled
at all** — the plan's conservative 20–45% range is deliberately not claimed, because nothing
in this build has measured one.

### Calibration

| | Value | On |
|---|---:|---|
| ECE | 0.0037 | 10,000 items, 1,000 positives, 5-fold, out-of-fold |
| Brier | 0.0207 | same |
| AUROC | 0.909 | same |

Per-signal AUROC is published including the weak signals — `citation_unsupported` 0.600,
`context_conflict` 0.575, `question_drift` 0.536, `overconfidence` 0.504 — because a fusion
layer that silently down-weights three near-chance signals should say so.

### The conformal guarantee

**Certified: at most 1% ungrounded escapes, at 90% confidence, on n = 840 held-out items.**
The selected threshold is λ = 0.015, where the measured escape rate is **0.000** and the
Learn-then-Test p-value is 0.00059 — comfortably inside α = 0.01, δ = 0.10.

That guarantee has a price, and it is the reason the filter is a mode rather than the
default: at λ = 0.015 the system checks **100% of traffic**. The next threshold up
(λ = 0.020) checks only 6.7% of traffic, and the procedure declines to certify it — its
measured escape rate is 19.9%, so no promise can honestly be attached to it. That is the
procedure working: it certifies what the data supports and refuses what it does not.

Run it with `make eval-guaranteed`; the selection is committed in
[`artifacts/calibration/lambda.json`](artifacts/calibration/lambda.json).

### Measured action latency

`L2_repair` 13.7 s · `L3_reroute` 30.7 s — median of 3 runs each on `qwen3:8b` via Ollama
([`artifacts/action_latency.json`](artifacts/action_latency.json)).

## Run it

Nothing below needs an API key.

**Console only** (deterministic replay gateway, no model, no index):

```bash
uv run python scripts/replay_console.py --port 8099
npm --prefix console run dev -- --host 127.0.0.1
```

Open <http://127.0.0.1:5173> and ask a question. The replay gateway picks a recorded trace
from the prompt text, so `prepayment` reaches an L2 repair, `forward … claim` an L4 hold,
`internal reference` an L5 canary block, and `branch … hours` a clean L0 pass.

**Full local stack** (Ollama, two model tiers, real retrieval):

```bash
ollama pull qwen3:4b && ollama pull qwen3:8b
uv run python scripts/build_index.py
make up        # gateway :8080 · observer :8081 · console :5173
```

**Point any OpenAI client at it:**

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="not-needed")
stream = client.chat.completions.create(
    model="interlock",
    messages=[{"role": "user", "content": "What are the prepayment charges on my home loan?"}],
    stream=True,
)
```

The interlock events (`interlock.stakes`, `.signal`, `.decision`, `.hold`) arrive on the same
SSE stream alongside the standard OpenAI chunks.

Full setup, configuration and troubleshooting: [`docs/REFERENCE.md`](docs/REFERENCE.md) and
[`docs/05_deploy_runbook.md`](docs/05_deploy_runbook.md).

## Repository map

| Path | What lives there |
|---|---|
| `interlock/gateway/` | FastAPI proxy, Lane A, router, cache, console projections |
| `interlock/risk/` | expected-loss objective, calibration, conformal thresholds, risk engine |
| `interlock/signals/` | stakes model, grounding signals, injection, PII, canary |
| `interlock/observer/` | observer service, linear probes, MiniCheck-class verifier |
| `interlock/gate/` | sentence commit gate and the intervention ladder |
| `interlock/interlock_tools/` | provenance tracking and the tool-call interlock |
| `interlock/eval/` | seeded evaluation, induced defects, metrics, anchors |
| `console/` | React operator console (chat, trace, reviews, evidence, about) |
| `policies/` | versioned policy — the governance artefact |
| `artifacts/` | committed evidence: evaluation, calibration, latency |
| `corpus/` | 45-document banking corpus, including one poisoned and one benign-untrusted doc |
| `docs/` | architecture, runbook, limitations |

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the whole system in detail, with the
  maths and the paper each mechanism comes from.
- [docs/REFERENCE.md](docs/REFERENCE.md) — operator and developer reference: dependencies,
  every start-up path, the API surface, configuration, the test gate, security model and
  deployment shape.
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — what this build does not do.
- [docs/05_deploy_runbook.md](docs/05_deploy_runbook.md) — running it.
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) — what is built, what is measured,
  what is stubbed, and every recorded deviation with its reason.
- [artifacts/](artifacts/) — the evidence behind every number above.

The screenshots in this file are regenerated from a live console against the replay gateway
with `node console/scripts/capture-screenshots.mjs` — none of them is a mock-up.

## Demo video

<!-- Replace with the recorded walkthrough before submission. -->
_TODO: add the demo video link._

## Research foundations

Almost every mechanism here is somebody else's published result, re-implemented and pointed
at this problem. The wording below distinguishes what is **implemented** from what is
**inspiration**.

| Research paper | Concept used | Where it appears in Interlock |
|---|---|---|
| [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401), Lewis et al., 2020 | Combining model knowledge with retrieved external documents and preserving evidence provenance. | Retrieval pipeline, grounded answers, trusted/untrusted document handling. |
| [The Probabilistic Relevance Framework: BM25 and Beyond](https://doi.org/10.1561/1500000019), Robertson & Zaragoza, 2009 | BM25-style probabilistic lexical retrieval. | `interlock/retrieval/` and the corpus search index. |
| [Detecting Hallucinations in Large Language Models Using Semantic Entropy](https://www.nature.com/articles/s41586-024-07421-0), Farquhar et al., 2024 | Measuring uncertainty over meaning rather than only token probabilities. | Inspiration for semantic uncertainty and hallucination detection. **The full multi-sample semantic-entropy method is not used on the hot path.** |
| [Semantic Entropy Probes: Robust and Cheap Hallucination Detection in LLMs](https://arxiv.org/abs/2406.15927), Kossen et al., 2024 | Approximating semantic uncertainty using a single hidden-state pass and a lightweight probe. | Observer model and residual-stream probe design. |
| [A Single Direction of Truth: An Observer Model's Linear Residual Probe Exposes and Steers Contextual Hallucinations](https://arxiv.org/abs/2507.23221), O'Neill et al., 2025 | A generator-independent observer can detect contextual hallucinations from its own residual activations. | Lane B observer, layer-wise linear probes, generator-agnostic monitoring. |
| [MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents](https://arxiv.org/abs/2404.10774), Tang, Laban & Durrett, 2024 | Lightweight claim-level grounding verification instead of expensive LLM judging for every claim. | MiniCheck-class claim verification, unsupported-claim detection, synthetic defect generation. |
| [Transforming Classifier Scores into Accurate Multiclass Probability Estimates](https://doi.org/10.1145/775047.775151), Zadrozny & Elkan, 2002 | Converting raw classifier scores into meaningful probabilities for cost-sensitive decisions. | Per-signal isotonic calibration, calibrated defect probabilities, risk-based action selection. |
| [Conformal Risk Control](https://arxiv.org/abs/2208.02814), Angelopoulos et al., 2022 | Choosing thresholds with statistical risk guarantees instead of tuning them by eye. | Conformal threshold selection and the ungrounded-escape guarantee in `interlock/risk/conformal.py`. |
| [Selective Classification for Deep Neural Networks](https://arxiv.org/abs/1705.08500), Geifman & El-Yaniv, 2017 | The reject option: trade prediction coverage against error risk. | Pass, annotate, repair, reroute, hold and block form a risk–coverage control ladder. |
| [Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813), Debenedetti et al., 2025 | Separating trusted control flow from untrusted data flow and enforcing tool capabilities outside the language model. | Provenance tracking, reversibility-aware tool policies, deterministic tool-call holds/blocks. **Interlock uses a simplified heuristic rather than a CaMeL-complete interpreter.** |
| Monitoring Algorithmic Fairness, Henzinger et al., 2023 | Monitoring fairness properties continuously during system operation. | Lane C counterfactual fairness twins and decision-level disparity monitoring. |
| Log-optimal Anytime-valid E-values, Koolen & Grünwald, 2022 | Evidence measures that remain valid under sequential monitoring and optional stopping. | Anytime-valid e-value monitoring for Lane C fairness observations. |

> **Disclaimer.** Interlock is research-informed software. It adapts ideas from hallucination
> detection, retrieval, calibration, conformal risk control, selective prediction, fairness
> monitoring and secure agent design. The implementation is an engineering adaptation and
> does not claim to reproduce the guarantees or benchmark results of the cited papers.

## What is ours

The papers above support individual mechanisms. These are Interlock's own system
contributions rather than copied research results:

- **One stakes estimate shared by routing and guardrails** — the thesis of the whole build.
- **Expected-loss action selection in a common currency**, so "block, edit or escalate" is
  arithmetic a risk officer can review rather than an engineer's threshold.
- **The L0–L5 intervention ladder**, and the rule that it shrinks as the answer travels.
- **Cost, regret, rework and net-value accounting** — reporting what was *wasted*, not only
  what was spent.
- **ConsoleHub operational event publishing**, so the console explains decisions instead of
  asking humans to make them.
- **Sentence-level streaming holds with resume tokens.**
- **The combined F-019 probability floor and relative-action-gain policy.**
- **A 300-item project-specific calibration-anchor dataset.**

## Known limitations

Read these before quoting any number above.

- **High-stakes clean traffic is intervened on**, because the impact model prices it that
  way (finding F-019). This is a policy decision about the impact model, not a detector
  tuning task, and it is still open.
- **The certified conformal threshold intervenes on 100% of traffic.** The guarantee is real
  and, at this detector quality, operationally expensive.
- **Verification cost and net spend are modelled**, from policy token prices and measured
  action latencies — not observed billing. No cache saving is modelled at all.
- **Calibration is fitted on induced failures**, not human labels (deviation D-010).
- **The dense retrieval arm is a lexical stand-in**, not a trained sentence encoder (D-009).
- **The router is a deterministic difficulty heuristic**, not RouteLLM's trained controller.
- **Three of six grounding signals are near chance** on this set and are weighted accordingly.
- Interlock is an **evidence-oriented prototype**, not a production-certified system.

Full list with measurements: [docs/LIMITATIONS.md](docs/LIMITATIONS.md) and
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

---

<div align="center">
  <sub>Built for the Accenture Innovation Challenge 2026 · Problem Statement 1 — ControlPlane.AI</sub>
</div>
