# Implementation Status

Live record of what is **built**, what is **measured**, what is **stubbed**, and every
**recorded deviation** from the plan with its rationale (CLAUDE.md §9).

Task-level state lives in `TODO.md`; machine-readable resume state in `STATE_CHECKPOINT.json`.

**Last updated:** 2026-08-25 · **Phase:** Day 1 — The spine, and the seam

---

## 1. What is built

| Component | State | Evidence |
|---|---|---|
| Repo skeleton, toolchain, CI | **built** | `ruff` + `ruff format` + `mypy --strict interlock/core` + `pytest` all green; 16 tests |
| `interlock/core/` contracts | not started | — |
| Gateway / Lane A | not started | — |
| Commit gate | not started | — |
| Observer | not started | — |
| Risk engine | not started | — |
| Ledger | not started | — |
| Console | not started | — |

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

### D-007 — Project given its own git repository
**Reality:** `git rev-parse --show-toplevel` resolved to `C:\Users\saksh` — the entire home
directory was a git repository, and this project was an untracked subdirectory of it.
**Chosen instead:** `git init` in the project directory so commits land here and never touch
the home repository. `.gitignore` covers secrets, runtime state (`*.db`), and regenerated
artefacts.

---

## 5. Invariant compliance

No invariant has been violated. The load-bearing ones to re-check at every phase boundary:

- One stakes estimate feeds **both** the router and the risk engine — provable from one trace.
- The commit gate and the calibration step are **never** cut, stubbed or bypassed.
- Canary match is a deterministic L5 with no model in the loop.
- No generative judge on the hot path — `/v1/judge` is a separate route so `grep` proves it.
- No generator internals — the observer probes its own residual stream.
