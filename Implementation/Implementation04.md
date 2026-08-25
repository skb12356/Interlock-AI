# Architecture Decision Records

Nine decisions that a technical judge will ask about. Each is written so you can read the "Decision" line aloud and defend it in 20 seconds.

---

## ADR-001: Observer model beside the generator, not probes inside it

**Status:** Accepted · **Deciders:** A + B

### Context
The strongest cheap hallucination signal is a linear probe on residual-stream activations. That normally requires hosting the generator — which contradicts "sits on top of any model", the requirement *and* the moat. Enterprises will keep paying OpenAI or Anthropic.

### Decision
Run a small open **observer** model (Qwen3-1.7B/4B) over `(context, question, candidate answer)` and probe **its own** residual stream. One forward pass, no generation, concurrent with the generator's streaming.

### Options considered

| | A: probe the generator | B: observer model | C: black-box only (logprobs + NLI) |
|---|---|---|---|
| Complexity | High (self-host everything) | Medium | Low |
| Model-agnostic | No | **Yes** | Yes |
| Signal quality | Best | Near-best (+5–27 pts over baselines in the published generator-agnostic setting) | Weakest |
| Cost | Full inference | ~1 forward pass of a 2–4 B model | ~0 |
| Owns a trained artefact | Yes | **Yes** | No — just a clever prompt |

### Trade-off analysis
C is a day cheaper and loses the technical claim entirely; there is nothing to defend and nothing to own. A is strictly better on signal and unshippable for the actual customer. B costs one extra forward pass, which is hidden under generation latency anyway, and is the only option that keeps both the probe artefact and full model-agnosticism.

### Consequences
- Easier: swapping generators, deploying beside a closed API, demonstrating on GPT and Claude in the same run.
- Harder: needs a GPU for the good version → **hence the mandatory CPU fallback profile (ADR-006)**.
- Revisit: if a customer already self-hosts, probing the generator directly is a strict upgrade and the interface doesn't change.

### Action items
1. [ ] `observer/model.py` with a `probe(hidden_states) -> signals` interface independent of the model family
2. [ ] KV-prefix cache keyed on `context_key` (Day 2)
3. [ ] Prove it on two generator families in the eval run (Day 5)

---

## ADR-002: Calibrate every signal, and set thresholds by conformal risk control

**Status:** Accepted

### Context
`groundedness = 0.72` has no units and cannot be multiplied by rupees. Every arithmetic claim downstream is invalid without a real probability, and every "≤1% escapes" claim is a wish without a distribution-free bound.

### Decision
Cross-fitted isotonic regression per signal → logistic fusion → thresholds selected by Learn-then-Test with a Hoeffding–Bentkus UCB at (α=0.01, δ=0.10) on a **300-item human-labelled** set that is disjoint from the eval set.

### Options considered
- **Platt scaling** — parametric, smoother with tiny n, but assumes a sigmoid shape our detectors don't have.
- **Isotonic** — non-parametric, monotone, needs a few hundred points. Chosen.
- **Eyeballed thresholds** — free, and it is precisely the failure we were hired to fix.

### Trade-offs
Isotonic can overfit at n=300, which is why it is cross-fitted 5-fold and why we report ECE rather than asserting calibration. LTT gives a bound on the *escape risk* rather than coverage of a set, which is what the pitch actually claims.

### Consequences
- Easier: every downstream number is defensible; the guarantee is a bound, not a hope.
- Harder: ~4 person-hours of manual labelling, unglamorous, on Day 2.
- **This is the step that separates a working prototype from a demo. It is not on the cut list.**

---

## ADR-003: Stakes-gated buffering instead of buffering everything

**Status:** Accepted

### Context
A one-sentence commit buffer is what lets you repair before anyone reads. Applied to every request, it delays *every* first token by a full sentence and kills the "TTFT statistically unchanged" claim.

### Decision
L0 traffic streams unbuffered. Buffering engages when stakes ≥ threshold or any pre-flight flag fires. Mode escalates mid-stream and never de-escalates; for already-emitted text the ladder is capped at annotate/notify, and the console says so.

### Trade-offs
Sentence 1 of a low-stakes answer cannot be repaired. That is acceptable *by construction*: low stakes is the reason it is low stakes, and the expected-loss table shows the residual harm explicitly rather than hiding it.

### Consequences
- p50 TTFT delta ≈ 0 across the traffic mix; p95 TTFT on high-stakes traffic rises by one sentence — **report both numbers, never the blended one.**
- Needs an accurate `already_emitted` flag threaded through the gate; get it wrong and the optimiser prices an action it cannot take.

---

## ADR-004: SQLite + DuckDB, not Postgres + Redis + ClickHouse

**Status:** Accepted

### Context
Five days, two people, and a judge who must run this in one command. Every service is a service that can fail on stage.

### Decision
SQLite in WAL mode as the system of record; DuckDB attached read-only for the analytics the console needs; no cache server, no queue broker, no OLAP store.

### Options
| | SQLite+DuckDB | Postgres+Redis+ClickHouse |
|---|---|---|
| Containers | 0 extra | 3 extra |
| Concurrent writers | one writer task | many |
| Judge runs it | `docker compose up` | same, but 3 more healthchecks to fail |
| Ceiling | ~2k writes/s, single node | horizontal |

### Trade-offs
Single-node ceiling, and every write must funnel through one writer task — which is a constraint we wanted anyway (nothing on the token path may touch the DB synchronously). The migration path is a SQLAlchemy URL change plus swapping `sqlite-vec` for `pgvector`.

### Consequences
Revisit at the first multi-tenant concurrent-write customer, not before.

---

## ADR-005: Deterministic stakes model, not an LLM stakes classifier

**Status:** Accepted

### Context
Stakes is the single number that drives both budgets. If it is a black box, the whole governance story collapses — "who decided this was worth ₹40,000?" is the question the panel will ask, and "a model did" is a losing answer.

### Decision
A feature scorer over the policy file: retrieved domain, monetary magnitude, user role, tool reversibility, intent keywords, conversation depth. A small intent classifier contributes **one feature among several**; it never decides alone. Output carries a human-readable rationale list.

### Trade-offs
Lower ceiling on nuance than an LLM classifier; needs a policy entry per domain. In exchange: auditable, diffable, sub-millisecond, replayable, and reviewable by risk and compliance rather than by an engineer. That is a governance feature, not a gap.

---

## ADR-006: A CPU-only profile is a first-class deliverable

**Status:** Accepted

### Context
The GPU may be unavailable at the venue. A judge's laptop definitely has no A10G.

### Decision
`docker compose --profile cpu up` swaps the 4B observer for a DeBERTa-base observer behind the identical HTTP contract. Degraded AUROC, identical behaviour, full demo.

### Consequences
Built on Day 2 as a normal task, not improvised on Day 5 as a panic. Also doubles as the chaos-test target and as the story for "what if the customer has no GPU budget?".

---

## ADR-007: Two-tier provenance heuristic instead of full dataflow taint

**Status:** Accepted, with a known limitation

### Context
The published CaMeL design does proper capability/dataflow tracking through an interpreter. That is weeks of work.

### Decision
Tier 1: exact / ≥0.9-token-overlap match between tool arguments and untrusted fragments. Tier 2: if no match, conservatively take the max taint over all untrusted fragments retrieved this turn.

### Trade-offs
Catches the demo case (an email address lifted verbatim from a poisoned PDF) precisely, and over-blocks on paraphrased injections. Over-blocking is the safe direction here and it is priced — term ② of the objective charges the nuisance, so the optimiser will not freeze a low-stakes reversible call just because a fragment was untrusted.

### Consequences
**Put this in `LIMITATIONS.md` and say it before the panel finds it.** Claiming CaMeL-equivalence and being caught is far more damaging than naming the gap.

---

## ADR-008: Expected loss in one currency, with hard rules outside the optimiser

**Status:** Accepted

### Context
Incomparable scores force a human to pick thresholds. But a pure optimiser can also be argued into a bad action by a mis-calibrated probability.

### Decision
Deterministic rules run **before** the argmin and can short-circuit to L4/L5 with no model in the loop (canary hit, blocked tool, irreversible × untrusted). The optimiser then chooses the cheapest action among those satisfying the conformal risk constraint.

### Trade-offs
Two mechanisms instead of one, so "it's all just arithmetic" needs a caveat on stage. Worth it: a canary leak must not be a probability judgement, and a system whose worst case depends entirely on calibration quality is not one an enterprise switches on.

---

## ADR-009: Efficacy is measured, not assumed

**Status:** Accepted

### Context
`eff[a][d]` — how much of defect *d* does action *a* actually remove — sits inside every row of the loss table. If those numbers are made up, the whole objective is theatre with extra steps.

### Decision
Ship a prior on Day 1; on Day 3, force each action on the labelled set, measure the actual reduction per defect class with Wilson intervals, and write the measurements back into the policy file. Lane C re-estimates them nightly.

### Consequences
- Easier: "where do these numbers come from?" has a one-sentence answer with a chart behind it.
- Harder: 2.5 h on Day 3 that feels like it isn't shipping a feature. It is shipping the only thing that makes the feature true.