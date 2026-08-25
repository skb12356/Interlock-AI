# Implementation Status

Live record of what is **built**, what is **measured**, what is **stubbed**, and every
**recorded deviation** from the plan with its rationale (CLAUDE.md §9).

Task-level state lives in `TODO.md`; machine-readable resume state in `STATE_CHECKPOINT.json`.

**Last updated:** 2026-08-25 · **Phase:** Day 2 — the commit gate and the ladder are built and wired

---

## 1. What is built

| Component | State | Evidence |
|---|---|---|
| Repo skeleton, toolchain, CI | **built** | ruff + ruff format + mypy --strict + pytest, all green |
| Contract 1 — `RiskEngine` + types | **frozen** | `tests/contract/test_contract1_types.py` |
| Contract 2 — Observer HTTP | **frozen** | `tests/contract/test_contract2_observer.py` |
| Contract 3 — SSE wire format | **frozen** | `tests/contract/test_contract3_sse.py`; verified against the real OpenAI SDK |
| Contract 4 — policy as code | **frozen** | `tests/contract/test_contract4_policy.py` |
| Contract 5 — DB schema | **frozen** | `migrations/001_initial.sql`; enforced by a grep test |
| `core/` ids, clock, money, errors | **built** | `tests/unit/test_core_ids_clock.py` |
| Expected-loss objective | **built** | reproduces the pitch's three-case table; every ladder rung reachable |
| Stub risk engine + mock observer | **built** | the Day-1 unblocking artefacts; retained as chaos fixtures |
| Demo corpus (45 docs) | **built** | 6 contradictory pairs, 1 poisoned PDF, 1 benign untrusted control |
| Lane A (pre-flight) | **built** | drops slow detectors rather than awaiting them |
| Detectors: injection, PII, canary | **built** | deterministic-first; zero false positives over the whole corpus |
| Stakes model v1 | **built** | deterministic feature scorer with a readable rationale |
| Ledger + migrations | **built** | one txn per request; holds awaited and restart-proof |
| Streaming proxy + 12 real fixtures | **built** | recorded from live Ollama |
| Segmenter | **built** | tests written first; chunk-order independent |
| Commit gate + property test | **built** | `tests/property/test_commit_gate.py` |
| Ladder L1/L2/L4/L5 | **built** | verified live end to end |
| **Observer (real weights)** | not started | D2-B4 |
| **Calibration + conformal** | not started | D2-B1..B3 — **on the never-cut list** |
| **Retrieval + demo app** | not started | D1-A5 — blocks F-010 |
| **Tool interlock** | not started | D3-A1..A2 — **on the never-cut list** |
| **Seeded eval set** | not started | D3-B7 — **on the never-cut list** |
| **Governor / Lane C / console** | not started | D2-A6, D4 |

**Test count:** 448 passing.

### Sequencing change

`risk/objective.py` was built at D1-B1 rather than D3-B1. The plan requires the stub to
return "a fully populated Decision (real loss table, fake probabilities)", which is not
possible without the real four-term arithmetic. Building it early is also strictly
better: the enforcement path is priced correctly from the first day rather than against
placeholder numbers. The parts of D3-B1 that genuinely need calibration artefacts — the
conformal feasibility filter — remain at D3-B1.

## 2. What is measured

Nothing yet. The six target metrics are measured from the first working phase and reported
by `make eval` — **never reported as achieved without a run that produced the number**
(CLAUDE.md §8).

| Metric | Target | Measured |
|---|---|---|
| Pre-Action Catch Rate | ≥ 90% | — |
| Added p95 latency | ≤ 120 ms | — |
| Verification cost | ≤ 5% of model spend | — |
| Net spend change | ≈ −30% | — |
| Ungrounded escapes | ≤ 1% @ 90% confidence | — |
| False interventions | ≤ 2% | — |

## 3. What is stubbed

Nothing yet. Planned stubs, per the plan's own unblocking design:

- `risk/stub.py` — `StubRiskEngine`, header-driven forced decisions. **Replaced by the real
  engine at D3-B4** (a one-line DI change; the Protocol is identical).
- `observer/mock_server.py` — scripted signals with a configurable sleep, to exercise the
  Lane B deadline path without a GPU. Retained permanently as a test fixture.

---

## 4. Recorded deviations

Each is a deliberate, recorded departure from `Implementation/*`. Nothing here changes a
product behaviour or an architectural invariant.

### D-001 — Docker dropped entirely *(user ruling, 2026-08-25)*
**Plan:** `docker compose up` with four services (gateway, observer, console, caddy) is the
judge's one-command entry point; a `--profile cpu` variant is mandatory (ADR-006).
**Reality:** Docker is not installed on the build machine, and the user ruled against
spending sprint time on the install.
**Chosen instead:** a native supervisor — `scripts/up.ps1` / `make up` — starts the same
three services as local processes with the same health-polling semantics. `pyproject.toml`
splits a light `core` dependency set (the spine always starts) from a deferred `ml` extra
(torch, transformers, presidio), installed by the first task that needs it.
**Cost of the choice:** the "one command on a judge's laptop" story becomes
`scripts/up.ps1` rather than `docker compose up`, and portability is asserted rather than
demonstrated. **Say this plainly rather than implying a container build exists.**

### D-002 — CPU observer profile promoted from fallback to primary
**Plan:** Qwen3-1.7B/4B observer on GPU, with a DeBERTa-v3-base CPU profile as the fallback
(ADR-006).
**Reality:** no NVIDIA GPU on this machine (`nvidia-smi` absent).
**Chosen instead:** the CPU profile is the path that gets built and tested first. The GPU
path is still implemented behind the identical interface, but is **untested here and must
be reported as untested**. ADR-006 already anticipated this: the CPU profile is the one a
judge runs anyway, and it doubles as the chaos-test target.
**Cost of the choice:** lower probe AUROC than the published Qwen3 numbers. The measured
value goes on the slide, not the paper's.

### D-003 — Ollama as the upstream generator, and as both router tiers
**Plan:** provider adapters for OpenAI and Anthropic; the demo needs a real provider key.
**Reality:** Ollama is installed with `qwen3:4b` and `qwen3:8b`, OpenAI-compatible at
`http://127.0.0.1:11434/v1`.
**Chosen instead:** Ollama is the default upstream, so the whole system demos with **no API
key and no rate limit** (this also retires the "provider rate limits during the demo" risk).
The two local models give a genuine two-tier router — `qwen3:4b` cheap, `qwen3:8b` strong —
so routing, cost-regret and shadow replay operate on real measured spend rather than a
simulated price table. OpenAI/Anthropic adapters stay behind the same interface for when a
key is added.
**Important constraint:** Ollama **cannot** host the observer. Probes require
`output_hidden_states=True` on the residual stream, which Ollama does not expose; the
observer runs under HF `transformers`. This is consistent with ADR-001 — the observer was
never the generator.

### D-004 — Package root is `interlock/`, not `src/interlock/`
**Conflict:** `CLAUDE.md` §4 sketches `src/interlock/...` with different sub-package names;
`Implementation01` ("Repository layout — create this on Day 1, hour 1") and
`Implementation03` (which names `interlock/core/types.py` in the frozen contract) both use a
top-level `interlock/`.
**Chosen instead:** follow the contract documents. They are more recent, more specific, and
the contract text names the import path directly. Sub-package names follow Implementation01
(`gate/`, `interlock_tools/`, `lanec/`, `signals/`, `risk/`) rather than CLAUDE.md's
`lanes/preflight/` sketch.

### D-005 — `starter/` and `docs/` scaffolding reconstructed from the specs
**Plan:** references `starter/core_types.py`, `starter/sentence_gate.py`,
`starter/objective.py`, `starter/policy_banking.yaml`, `docs/01`, `docs/02`,
`docs/05_deploy_runbook.md` and `diagrams/`.
**Reality:** none of these files exist in the workspace.
**Chosen instead:** reconstruct from the specs, which carry the content inline —
Implementation03 §2 is the complete `core/types.py`, §5 the complete banking policy YAML,
Implementation02 §4.2 the objective, §4.3 the gate state machine. The two JPGs in
`Implementation/` are the authoritative request-lifecycle sequence diagram and tool-interlock
decision tree, and both agree with the prose.

### D-006 — Solo engineer; A/B become work-stream labels
**Plan:** two engineers — A (Stream & Enforcement) and B (Signals & Decisions) — with two
daily integration checkpoints.
**Reality:** one engineer, executing sequentially.
**Chosen instead:** keep the **contract seam** exactly as frozen (it is what makes the
architecture modular, independent of headcount) and keep the stub-first unblocking order —
`StubRiskEngine` and the mock observer ship before the enforcement path, so the gate,
repair, hold and interlock paths are all built and tested with no GPU and no model. A/B
become labels indicating which contract side a task sits on. Checkpoints become the
per-task verification gate in `TODO.md`'s Definition of Done.

### D-008 — Lane A deadline raised from 40 ms to 120 ms *(user ruling, 2026-08-25)*
**Plan:** Lane A has a ~25 ms budget with a 40 ms hard deadline, and the non-functional
target is "≤ 40 ms added p95 on low-stakes traffic".
**Reality:** the user instructed 120 ms; the CPU-only profile has no ONNX-accelerated
detectors, so the original figure assumed hardware this build does not have.
**Cost of the choice, stated plainly:** the low-stakes tail may now reach 120 ms, which
**breaks the ≤ 40 ms low-stakes target and spends the entire ≤ 120 ms headline budget in
one lane.** Configurable via `INTERLOCK_LANE_A_DEADLINE_MS`; lower it once the detectors
are ONNX-exported. Measured Lane A latency is currently **sub-millisecond** with the
deterministic detectors, so the deadline is headroom rather than expected cost — but the
number reported at D5-A2 must be the measured p95, never the target.

### D-007 — Project given its own git repository
**Reality:** `git rev-parse --show-toplevel` resolved to `C:\Users\saksh` — the entire home
directory was a git repository, and this project was an untracked subdirectory of it.
**Chosen instead:** `git init` in the project directory so commits land here and never touch
the home repository. `.gitignore` covers secrets, runtime state (`*.db`), and regenerated
artefacts.

---

## 4b. Open findings

**The authoritative list is `STATE_CHECKPOINT.json -> open_findings` (F-001 … F-010).**
The three below are the ones with consequences for what we may claim on stage; the rest
are recorded there with their resolution.


Things the build has surfaced that are not yet resolved. Each has a test pinning the
current behaviour so a regression is noticed here rather than on stage.

### F-001 — The illustrative L5 nuisance caused the optimiser to over-block *(fixed)*
The plan's example policy prices a false block at ₹900. With the four-term objective and
the shipped multipliers, the high-stakes case (₹40,000 loan question at P(ungrounded)=0.31)
priced **block at ₹621 against hold at ₹868** — so the optimiser chose to block, on exactly
the traffic where the design says blocking should be rare, and where the pitch's own worked
example chooses *hold*.

Fixed in the policy file, not in code: `L5_block` nuisance raised to ₹1,500, on the reasoning
that refusing to answer a customer costs more than making them wait — you lose the interaction
*and* still escalate to a human. With that, the three cases from the pitch reproduce exactly:
**Hold / Repair / Pass**. `L1_annotate` efficacy on `ungrounded` was also lowered from 0.25 to
0.20, because above ≈0.2485 the arithmetic annotates traffic that should simply pass, which
erodes the "L0 is free" property the whole latency budget depends on.

Pinned by `test_blocking_is_never_chosen_by_the_optimiser_on_these_cases`.

### F-002 — The objective intervenes on everything at high stakes *(open, by design for now)*
At ₹40,000 impact, even **P(ungrounded)=0.001** puts ₹100 of expected harm against a repair
that costs ₹2.18 and removes 80% of it. The argmin therefore repairs *every* sentence in a
high-stakes domain. This is arithmetically correct and operationally unusable.

It is not a bug in the objective; it is the reason the design has two further mechanisms that
are not yet built:

* the **conformal feasibility filter** (D3-B1), which restricts the argmin to actions meeting
  the certified risk constraint rather than letting it optimise freely, and
* **measured efficacy** (D3-B6), which will almost certainly reduce the assumed 0.80 repair
  efficacy and so reduce the incentive to repair.

The **false-intervention rate (≤ 2%)** is the metric that disciplines this, and it is measured
at D3-B7 and reported at D5-B1. **This must not be quietly tuned away** — if the measured rate
comes out high, that is the finding, and the honest response is to report it with the
break-even analysis rather than to adjust the policy until the number looks good.

Pinned by `test_a_nonzero_baseline_would_intervene_on_everything_at_high_stakes`.

### F-003 — `impact_inr` is a base, not the final impact *(recorded)*
`Impact_d = impact_inr × defect_multiplier × reversibility_multiplier` (Implementation02 §4.2),
so `loan_terms` at ₹40,000 with `costly` derives to ₹100,000. The pitch's Case A instead treats
₹40,000 as the *final* impact. The formula is followed as specified — it is the documented
contract — but the domain figures in `policies/banking.yaml` are therefore **base** values that
the multipliers scale, and the three-case table on the slide must be regenerated from the real
policy at D5-B2 rather than quoted from the deck. The plan already anticipates this ("rather
than the slide's illustrative numbers").

---

## 5. Invariant compliance

No invariant has been violated. The load-bearing ones to re-check at every phase boundary:

- One stakes estimate feeds **both** the router and the risk engine — provable from one trace.
- The commit gate and the calibration step are **never** cut, stubbed or bypassed.
- Canary match is a deterministic L5 with no model in the loop.
- No generative judge on the hot path — `/v1/judge` is a separate route so `grep` proves it.
- No generator internals — the observer probes its own residual stream.
