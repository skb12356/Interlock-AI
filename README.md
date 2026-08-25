# Interlock

**Routing and guarding are the same decision. This is the layer that makes it once.**

Interlock is an AI control plane that unifies two systems every enterprise AI stack builds
separately: the **router** that decides how much compute to spend on a request, and the
**guardrail** that decides how hard to check the answer. Both answer the same question —
*how much does this request matter?* — so Interlock computes one **stakes estimate** per
request and spends both budgets from it. The money saved on the ~80% of traffic that never
needed a frontier model pays for the deep checking on the ~20% that did. Oversight funds
itself instead of being a tax.

It is an **OpenAI-compatible streaming proxy**: point `base_url` at Interlock and it sits
transparently in front of any model — GPT, Claude, Gemini, open weights — none of them modified.

## The one metric

**Pre-Action Catch Rate** — not accuracy, but the share of defects stopped *before* a human
or a tool acted on them.

## How it works

Three lanes and a controller:

| Lane | When | What runs | On the critical path? |
|---|---|---|---|
| **A — pre-flight** | before the model is called (~25 ms) | injection · PII · canary · stakes · cache · route | yes, 25 ms |
| **B — in-flight** | concurrent with token generation | observer probe · claim verifier · overconfidence index | no — hidden under generation |
| **C — offline** | afterwards, sampled | fairness twins · shadow replay · deep-judge anchor (~1%) · drift | never |

Every calibrated signal is converted into **expected loss in one currency (₹)** and the
control plane picks the cheapest safe action on an intervention ladder:

`L0 Pass → L1 Annotate → L2 Repair the bad sentence → L3 Reroute → L4 Hold for a human → L5 Block`

Text streams **one sentence behind** generation, so a bad sentence can be repaired before
anyone reads it.

## Quick start

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). No API key is needed —
the default upstream is a local [Ollama](https://ollama.com) install.

```bash
uv sync --group dev      # core + dev dependencies
make up                  # gateway :8080, observer :8081, console :5173
make demo                # bank support assistant, end to end
make eval                # seeded eval set: Interlock off vs on, six metrics
```

On Windows without `make`, use the equivalents in `scripts/`:

```powershell
.\scripts\up.ps1
.\scripts\down.ps1
```

Then point any OpenAI-compatible client at it:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="local")
```

## Repository layout

```
interlock/core/            frozen contracts -- types, policy, errors  (mypy --strict)
interlock/gateway/         OpenAI-compatible proxy, Lane A, router, cache, governor
interlock/gate/            commit gate: segmenter, state machine, ladder, repair
interlock/interlock_tools/ tool-call interlock: provenance, reversibility, holds
interlock/ledger/          spend, cost-regret, rework attribution
interlock/observer/        the observer model, probes, claim verifier
interlock/signals/         injection, PII, canary, stakes, fusion
interlock/risk/            calibration, conformal thresholds, expected-loss optimiser
interlock/lanec/           fairness twins, e-values, deep judge, drift
interlock/eval/            seeded evaluation set and metrics
policies/                  versioned policy-as-code (diffable, auditable)
migrations/                NNN_*.sql, applied at boot, idempotent
```

## Documentation

| Document | What it is |
|---|---|
| `TODO.md` | the master task list and current status |
| `IMPLEMENTATION_STATUS.md` | what is built, what is measured, what is stubbed, recorded deviations |
| `Implementation/Implementation01.md` | the 5-day plan |
| `Implementation/Implementation02.md` | system design |
| `Implementation/Implementation03.md` | the five frozen interface contracts |
| `Implementation/Implementation04.md` | ADR-001 … ADR-009 |
| `Interlock-v2.pdf` | design rationale, evidence base, target numbers |
| `docs/LIMITATIONS.md` | what we know is thin — read this one |

## A note on claims

Every mechanism here is either a published result being re-implemented (semantic-entropy
probes, generator-agnostic observers, MiniCheck, conformal factuality thresholds,
RouteLLM-style pre-generation routing, SentGuard-style sentence-commit streaming,
CaMeL-style tool interlocks, anytime-valid fairness statistics) or an honestly-labelled
self-innovation (the unified stakes estimate, the expected-loss objective, the
cost-regret/rework ledger, the Pre-Action Catch Rate metric). Where something is published,
we cite it rather than claim it.
