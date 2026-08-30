# False-Intervention Reduction and Release Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select and ship the safest measured false-intervention reduction, complete the local evaluation/reporting gaps, and leave Person 1/Person 2 submission evidence reproducible and honest.

**Architecture:** Cache one real calibrated decision trace per case and replay candidate policy adjustments over those immutable probabilities, stakes, hard-rule outcomes, tool outcomes, and loop outcomes. Select only a cross-seed Pareto candidate that preserves the safety gates, express it through versioned policy fields, and regenerate the same product metrics used by the current scorecard. Finish the existing OpenRouter and console evidence paths without inventing human, production, or deployment results.

**Tech Stack:** Python 3.12, Pydantic, YAML, JSON/Markdown, pytest, Ruff, mypy, React 19, Vitest, Vite, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-30-false-intervention-release-design.md`

## Global Constraints

- Work on `console-master-integration`; never commit directly to `master`.
- Preserve hard canary, PII, tool-provenance, monetary-cap, and irreversible-action rules.
- Require catch >= 90% and no increase from the baseline empirical ungrounded-escape count on every seed.
- Report annotation-inclusive and disruptive false interventions separately.
- Use seeds 20260826, 20260827, and 20260828 for selection.
- Never describe generated labels, replay, deterministic fixtures, or local sweeps as human-reviewed or production evidence.
- Never stage `.gitignore`, `graphify-out/`, `tmp/`, `.claude/`, API credentials, or local OpenRouter metadata.
- Write the smallest regression test first, run it RED for the intended reason, then implement and run GREEN.
- Use focused commits and inspect `git diff --cached --name-only` plus `git diff --cached --check` before each commit.

---

### Task 1: Build the cross-seed policy comparison harness

**Files:**
- Create: `interlock/eval/policy_experiment.py`
- Create: `scripts/compare_policy_methods.py`
- Create: `tests/unit/test_policy_experiment.py`
- Generate: `artifacts/eval/policy_comparison.json`
- Generate: `artifacts/eval/policy_comparison.md`

**Interfaces:**
- Produces: `PolicyAdjustment(impact_scale: float, probability_deadband: float, nuisance_multiplier: float)`
- Produces: `CandidateSeedResult(seed: int, catch_rate: float, escape_count: int, false_intervention_rate: float, disruptive_rate: float, action_counts: Mapping[str, int])`
- Produces: `apply_adjustment(*, probs, stakes, policy, adjustment, hard_rules, already_emitted) -> ActionChoice`
- Produces: `select_candidate(results, *, baseline_escape_by_seed) -> CandidateResult`
- Consumes: real baseline `Decision.probs`, `Decision.hard_rule`, `CaseOutcome`, `EvalCase`, `Policy`, and `choose_action`.

- [ ] **Step 1: Write RED tests for transformations and hard-rule preservation**

Assert that impact scaling changes only the effective `Stakes` passed to the objective,
deadband clamps probabilities at zero, nuisance multiplication leaves L0 and human-review
cost unchanged, and a baseline hard-rule result cannot be weakened by any candidate.

- [ ] **Step 2: Run the focused tests and confirm failure because the module is absent**

```bash
uv run pytest -q tests/unit/test_policy_experiment.py
```

- [ ] **Step 3: Implement pure candidate application and immutable result types**

Use `model_copy` for adjusted stakes/policy objects and call the existing
`choose_action`; never mutate the loaded policy or original stakes. Include the original
and effective values in the candidate record.

- [ ] **Step 4: Write RED tests for selection**

Use literal three-seed candidates to prove a candidate is rejected when one seed has
catch 0.89, when any escape count exceeds baseline, or when it is Pareto-dominated.
Prove ranking uses worst-seed disruptive rate, then worst-seed any-intervention rate,
then the normalized adjustment distance.

- [ ] **Step 5: Implement cross-seed selection and Markdown rendering**

Retain every rejected candidate with explicit reasons. The selected result must carry
all three per-seed metrics and `selected_on_production_traffic: false`.

- [ ] **Step 6: Implement the CLI using one cached real trace per seed**

Build each seeded set, run the real engine once, retain decisions by request ID, and
replay the exact candidate matrix from the spec without re-running detectors. Recompute
action, intervention, catch, verification spend, and metrics while preserving tool/loop
outcomes and hard rules from the baseline trace.

- [ ] **Step 7: Run the comparison and verify artifact invariants**

```bash
uv run python scripts/compare_policy_methods.py \
  --json artifacts/eval/policy_comparison.json \
  --markdown artifacts/eval/policy_comparison.md
```

Assert the artifact contains all three seeds, baseline, single-family candidates,
combined candidates, rejected reasons, and one selected eligible non-dominated result.

- [ ] **Step 8: Run focused/full gates and commit**

```bash
uv run pytest -q tests/unit/test_policy_experiment.py tests/unit/test_objective.py tests/unit/test_eval_harness.py
uv run ruff format --check .
uv run ruff check .
uv run pytest -q tests console/tests/python
```

Commit: `feat(eval): compare false-intervention policies`

---

### Task 2: Ship the selected policy adjustment transparently

**Files:**
- Modify: `interlock/core/policy.py`
- Modify: `interlock/risk/engine.py`
- Modify: `interlock/risk/objective.py`
- Modify: `policies/banking.yaml`
- Modify: `tests/unit/test_policy.py`
- Modify: `tests/unit/test_real_engine.py`
- Modify: `tests/unit/test_objective.py`
- Regenerate: `artifacts/eval/report-seed-20260826.json`
- Regenerate: `artifacts/eval/report-seed-20260827.json`
- Regenerate: `artifacts/eval/report-seed-20260828.json`
- Regenerate: `artifacts/eval/report.json`

**Interfaces:**
- Produces policy fields `decision_adjustment.impact_scale`, `.probability_deadband`, and `.nuisance_multiplier` with neutral defaults 1, 0, and 1.
- Production `RealRiskEngine` applies the exact values from `policy_comparison.json` only to probabilistic expected-loss pricing.
- Decision `why` records original/effective impact, deadband, and nuisance multiplier whenever non-neutral.

- [ ] **Step 1: Write RED policy-validation tests**

Reject impact scales outside `(0, 1]`, deadbands outside `[0, 1)`, and nuisance
multipliers below 1. Confirm a policy without the block loads neutral defaults.

- [ ] **Step 2: Run RED, implement the typed policy block, then run GREEN**

```bash
uv run pytest -q tests/unit/test_policy.py
```

- [ ] **Step 3: Write RED engine/objective tests**

Prove probabilistic choices use effective values, explanations disclose the adjustment,
original stakes remain unchanged, and canary hard rules still produce the same L5 action.

- [ ] **Step 4: Implement the minimal production seam**

Route the policy adjustment through `choose_action` without changing the `Stakes`
contract. Keep a complete loss table in effective rupee terms and disclose the original
stakes in `why`.

- [ ] **Step 5: Copy the selected artifact values into `banking.yaml` and bump policy version**

Refuse the update if the selected record is absent, ineligible, or fails any seed safety
gate. Add a comment with the comparison artifact path and three-seed measured result.

- [ ] **Step 6: Regenerate all three seeded reports and compare to selection evidence**

```bash
for seed in 20260826 20260827 20260828; do
  uv run python scripts/eval.py --seed "$seed" --json "artifacts/eval/report-seed-$seed.json"
done
```

Assert production reports reproduce the candidate action and safety metrics within exact
count equality.

- [ ] **Step 7: Run gates and commit**

Commit: `fix(risk): balance sentence interventions by measured policy`

---

### Task 3: Finish OpenRouter reporting and budget integrity

**Files:**
- Create: `interlock/eval/anchor_report.py`
- Create: `scripts/report_manual_anchors.py`
- Create: `tests/unit/test_anchor_report.py`
- Modify: `scripts/eval_manual_anchors.py`
- Modify: `tests/unit/test_judge_run.py`
- Generate: `artifacts/eval/manual_anchor_report.json`
- Generate: `artifacts/eval/manual_anchor_report.md`

**Interfaces:**
- Produces: `build_anchor_report(labels, judgments, *, model) -> dict[str, Any]`
- Produces: `render_anchor_markdown(report) -> str`
- The CLI preloads costs for every resumed model output before allocating the shared remaining budget.

- [ ] **Step 1: Write RED report tests with hand-derived confusion/validity values**

Cover strict three-class agreement, binary grounded/defective results, Wilson intervals,
per-mode/level/domain slices, evidence-cluster counts, request-level cost deduplication,
latency percentiles, invalid results, and bounded failed examples.

- [ ] **Step 2: Implement the pure report and CLI, then run GREEN**

Render the taxonomy warning prominently and never call the generated anchor human-reviewed.

- [ ] **Step 3: Write RED resumed multi-model cap regression**

Prewrite two model outputs each costing USD 0.30 under a USD 1.50 shared cap and prove
the first resumed model receives at most USD 0.90 additional capacity rather than USD 1.20.

- [ ] **Step 4: Fix cost preloading and run all judge-run tests**

Reject incompatible or malformed existing records before any network dispatch.

- [ ] **Step 5: Generate the GPT-4o Mini report, run gates, and commit**

Commit: `feat(eval): report real OpenRouter anchor results`

---

### Task 4: Build the combined release evidence report

**Files:**
- Create: `interlock/eval/product_report.py`
- Create: `scripts/build_product_report.py`
- Create: `tests/unit/test_product_report.py`
- Generate: `artifacts/eval/product_report.json`
- Generate: `artifacts/eval/product_report.md`

**Interfaces:**
- Produces: `build_product_report(anchor, seeded, policy_comparison, artifacts) -> dict[str, Any]`
- Produces: `render_product_markdown(report) -> str`

- [ ] **Step 1: Write RED honesty and status-merging tests**

Prove the report distinguishes pass, miss, inconclusive, unavailable, and not-run;
retains failed metrics; keeps replay/live/generated provenance; and never turns absent
economics, human labels, fairness samples, or penetration testing into zero/pass.

- [ ] **Step 2: Implement pure merging and rendering**

Include current policy results, comparison evidence, OpenRouter results, latency/load,
fairness, security, and external gates with direct artifact paths.

- [ ] **Step 3: Generate, inspect, run gates, and commit**

Commit: `feat(eval): assemble submission evidence report`

---

### Task 5: Close Person 2 handoff and browser verification

**Files:**
- Modify: `coordination/PERSON2_NOTES.md`
- Modify: `IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`
- Modify only if a verified browser regression requires it: `console/**`

- [ ] **Step 1: Run frontend unit/type/build gates**

```bash
npm --prefix console run test:unit -- --run
npm --prefix console run typecheck
npm --prefix console run build
```

- [ ] **Step 2: Run Playwright desktop/mobile journeys and inspect failures**

```bash
npm --prefix console run test:e2e
```

Use systematic debugging and a failing regression test before any console code fix.

- [ ] **Step 3: Synchronize handoff documentation**

Record the stage-flow console, current dependencies/test counts, removed upload control,
aggregate-only live Lane A timing, deferred egress authorization, and honest real-data
requirements.

- [ ] **Step 4: Commit**

Commit: `docs(console): finalize stage-flow handoff`

---

### Task 6: Final release verification and handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/LIMITATIONS.md`
- Modify: `STATE_CHECKPOINT.json`
- Modify: `TODO.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run remaining locally reproducible chaos and security checks**

Run `pytest -m chaos`, the local security sweep, and the current load/rehearsal artifact
validators. Record measured failures rather than editing targets.

- [ ] **Step 2: Run the complete fresh quality gate**

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
npm --prefix console run test:unit -- --run
npm --prefix console run typecheck
npm --prefix console run build
npm --prefix console run test:e2e
```

- [ ] **Step 3: Update status sources without overstating completion**

Mark only locally completed items done. Keep human efficacy review, production traffic,
external security/deployment, semantic entropy, live fairness/economics, sensitive-data
authorization, and repeated human demo rehearsal as external gates.

- [ ] **Step 4: Commit and inspect branch state**

Commit: `docs(release): finalize measured submission handoff`

- [ ] **Step 5: Report exact results and remaining external gates**

Include commit IDs, test counts, selected-policy metrics for all seeds, artifact links,
and branch/push status. Do not claim success for any command not run fresh.
