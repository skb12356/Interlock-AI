# OpenRouter Product Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a diversified 200-clean/100-defective anchor, run it safely through OpenRouter, and combine its results with deterministic product gates into an honest strengths-and-failures report.

**Architecture:** Keep the paid generative judge isolated to offline response-grounding labels. Add a deterministic anchor builder, an injected OpenAI-compatible judge transport, a resumable budget controller, and pure report builders. Existing deterministic, contract, property, console, and Playwright suites remain authoritative for security and control-plane behavior; a combined report links their evidence without allowing an LLM judge to override it.

**Tech Stack:** Python 3.12, HTTPX, JSON/JSONL, existing Interlock eval and Wilson-metric modules, pytest/respx, Ruff, mypy, React/Vitest/Playwright verification gates.

**Spec:** `docs/superpowers/specs/2026-08-30-openrouter-product-evaluation-design.md`

## Global Constraints

- The anchor is exactly 300 rows: 200 clean plus 20 rows for each of `retrieval_dropped`, `number_corrupted`, `clause_swapped`, `unanswerable`, and `contradiction`.
- Clean levels are exactly 67 L1, 67 L2, 66 L3; every 20-row failure mode is exactly 7 L1, 7 L2, 6 L3.
- The audit distribution is diagnostic and must never replace the 10% production base-rate assumption used to fit calibration.
- Generative judging stays offline and never changes a shipped answer or a policy threshold.
- Paid execution requires `OPENAI_API_KEY` and an explicit `--allow-external-context` flag.
- Default paid budget is USD 1.50; no automatic fallback model is allowed.
- No test or CI job may make a real network call or consume provider credits.
- Never log or persist API keys, authorization headers, resume tokens, or tenant canaries.
- Preserve and never stage unrelated changes under `console/`, `.claude/`, `.gitignore`, `graphify-out/`, and `tmp/`.
- Use explicit `git add <owned paths>` only; inspect `git diff --cached --name-only` and `git diff --cached --check` before every commit.
- After each production commit run its targeted tests, Ruff format/lint, strict mypy, and the complete Python suite. Run frontend/build gates when report or documentation work references the product-wide verification state.

## File Structure

- `interlock/eval/anchor.py` — exact mode/level quotas and deterministic challenge-context enrichment.
- `interlock/eval/openrouter_judge.py` — OpenAI-compatible payloads, response parsing, retry classification, and normalized result types.
- `interlock/eval/judge_run.py` — stratified prefixes, resumable JSONL state, token/cost accounting, and budget stops.
- `interlock/eval/anchor_report.py` — confusion matrix, per-slice metrics, Wilson intervals, invalid counts, and failure rows.
- `interlock/eval/product_report.py` — pure merger for anchor, seeded, verification, latency, fairness, and economics evidence.
- `interlock/eval/live_rehearsal.py` — safe plan/digest generation and live SSE rehearsal result normalization.
- `scripts/build_manual_anchor.py` — CLI adapter for the exact anchor builder and ledger import.
- `scripts/eval_manual_anchors.py` — OpenRouter-compatible paid CLI.
- `scripts/report_manual_anchors.py` — JSON/Markdown anchor report CLI.
- `scripts/run_product_checks.py` — local verification command runner that writes an auditable manifest.
- `scripts/build_product_report.py` — combined JSON/Markdown report CLI.
- `scripts/rehearse_openrouter.py` — explicit-opt-in live gateway rehearsal CLI.
- `tests/unit/test_manual_anchor.py` — anchor quotas, levels, metadata, failure, determinism, and import behavior.
- `tests/unit/test_openrouter_judge.py` — real parser/transport boundary against synthetic HTTP responses.
- `tests/unit/test_judge_run.py` — resume, stratification, budget, and secret-redaction behavior.
- `tests/unit/test_anchor_report.py` — hand-derived metric and failure-report expectations.
- `tests/unit/test_product_report.py` — honest status merging and unavailable-evidence behavior.
- `tests/unit/test_live_rehearsal.py` — opt-in guard, safe metadata, and SSE contract parsing.
- `data/labels/manual_anchor_300.jsonl` and `.summary.json` — regenerated, reviewable anchor artifacts.
- `artifacts/eval/manual_anchor_report.json` and `.md` — paid-run aggregate evidence, without credentials.
- `artifacts/eval/product_report.json` and `.md` — combined final evidence pack.

---

### Task 1: Build the exact diversified 300-item anchor

**Files:**
- Create: `interlock/eval/anchor.py`
- Modify: `interlock/eval/induce.py`
- Modify: `scripts/build_manual_anchor.py`
- Modify: `tests/unit/test_manual_anchor.py`
- Regenerate: `data/labels/manual_anchor_300.jsonl`
- Regenerate: `data/labels/manual_anchor_300.summary.json`

**Interfaces:**
- Produces: `ANCHOR_MODE_COUNTS: dict[str, int]`
- Produces: `ChallengeLevel = Literal["L1_direct", "L2_distractor", "L3_conflict"]`
- Produces: `AnchorTriple(triple: LabelledTriple, challenge_level: ChallengeLevel, domain: str)`
- Produces: `build_anchor(chunks: Sequence[Chunk], *, seed: int) -> list[AnchorTriple]`
- Produces: stable `evidence_cluster_id` values that exclude question wording and row IDs.
- Produces: `TripleGenerator.generate_exact(mode_counts: Mapping[str, int]) -> list[LabelledTriple]`
- Consumed by: `scripts/build_manual_anchor.build_labels()` and later stratified paid-run selection.

- [ ] **Step 1: Write the failing exact-quota and level tests**

Add literal expectations to `tests/unit/test_manual_anchor.py`:

```python
def test_manual_anchor_has_the_approved_200_100_mode_matrix() -> None:
    rows = build_labels(300, seed=20260829)
    assert Counter(row["payload"]["failure_mode"] for row in rows) == {
        "clean": 200,
        "retrieval_dropped": 20,
        "number_corrupted": 20,
        "clause_swapped": 20,
        "unanswerable": 20,
        "contradiction": 20,
    }
    assert sum(row["gold_ungrounded"] for row in rows) == 80
    assert sum(row["gold_contradicted"] for row in rows) == 20


def test_every_mode_has_its_declared_challenge_levels() -> None:
    rows = build_labels(300, seed=20260829)
    grouped = Counter(
        (row["payload"]["failure_mode"], row["payload"]["challenge_level"])
        for row in rows
    )
    assert [grouped[("clean", level)] for level in LEVELS] == [67, 67, 66]
    for mode in ANCHOR_FAILURE_MODES:
        assert [grouped[(mode, level)] for level in LEVELS] == [7, 7, 6]
```

Name the mutations these catch before running: reusing `DEFECT_BASE_RATE`, losing a
failure mode during generation, or assigning all rows to the easiest level.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest -q \
  tests/unit/test_manual_anchor.py::test_manual_anchor_has_the_approved_200_100_mode_matrix \
  tests/unit/test_manual_anchor.py::test_every_mode_has_its_declared_challenge_levels
```

Expected: FAIL because the current builder produces 270 clean / 30 defective rows and
does not emit `challenge_level`.

- [ ] **Step 3: Add loud exact-mode generation**

In `interlock/eval/induce.py`, add a public exact generator that validates mode names and
refuses `_one()` fallbacks:

```python
def generate_exact(self, mode_counts: Mapping[str, int]) -> list[LabelledTriple]:
    unknown = set(mode_counts) - set(FAILURE_MODES)
    if unknown:
        raise ValueError(f"unknown failure modes: {sorted(unknown)}")
    plan = [mode for mode, count in mode_counts.items() for _ in range(count)]
    self._rng.shuffle(plan)
    triples: list[LabelledTriple] = []
    for index, mode in enumerate(plan):
        triple = self._one(index, mode)
        if triple is None or triple.failure_mode != mode:
            raise ValueError(f"cannot satisfy exact quota for {mode}")
        triples.append(triple)
    return triples
```

Import `Mapping` from `collections.abc`. Do not change `generate()` or
`DEFECT_BASE_RATE`; calibration continues to use the existing production assumption.

- [ ] **Step 4: Add deterministic challenge enrichment**

In `interlock/eval/anchor.py`, define literal quotas and wrap exact triples. Assign level
counts per mode with `divmod(count, 3)`, then enrich context deterministically:

- L1 keeps the baseline context.
- L2 adds one trusted, different-document distractor.
- L3 adds two trusted, different-document distractors while preserving the original
  evidence order and provenance.
- For `retrieval_dropped`, never add the withheld supporting document.
- For `contradiction`, preserve both contradictory documents before distractors.

Raise `ValueError` when there are not enough safe distractors. Store the source chunk's
domain, the level, context count, and context document IDs in each output row.

- [ ] **Step 5: Update the script adapter and summary**

Keep `build_labels(count, seed)` for compatibility, but reject any count other than 300
with an actionable message because the approved exact matrix is defined only for 300.
Emit top-level gold flags and add these payload fields:

```python
{
    "challenge_level": anchor.challenge_level,
    "domain": anchor.domain,
    "context_count": len(anchor.triple.context),
    "context_doc_ids": [fragment.doc_id for fragment in anchor.triple.context],
    "evidence_cluster_id": evidence_cluster_id(anchor),
}
```

Extend `summary()` with literal `mode_counts`, `challenge_level_counts`, `domains`, and
`audit_distribution_note`, plus `unique_evidence_clusters`, `prompt_variants`, and
`max_cluster_size`. The 300 rows are prompt variants; they are not independent evidence.

- [ ] **Step 6: Add failure, determinism, and metadata tests**

Add tests that:

- call `generate_exact({"contradiction": 1})` on a corpus without contradiction pairs
  and assert `ValueError("cannot satisfy exact quota")`;
- call `build_labels(300, seed=7)` twice and assert identical serialized rows;
- assert every row's `context_count` matches `len(context)` and `context_doc_ids`;
- assert every row has a non-empty domain and review basis; and
- assert `build_labels(299, ...)` fails instead of silently changing the ratio.

Replace the existing small-count calls with a module-scoped 300-row fixture; round-trip
and ledger-import tests may use `rows[:12]`. This preserves fast focused assertions
without asking the approved builder to invent a second, undefined ratio for 12 or 30
rows.

Add `test_the_committed_anchor_artifact_matches_the_approved_matrix`, which reads
`data/labels/manual_anchor_300.jsonl` directly and repeats the literal mode, level, gold
label, unique-ID, context metadata, and 300-row assertions. This catches a stale or
manually edited artifact even when the in-memory builder remains correct.

- [ ] **Step 7: Run GREEN, then regenerate and verify artifacts**

```bash
uv run pytest -q tests/unit/test_manual_anchor.py
uv run python scripts/build_manual_anchor.py --count 300 --seed 20260829
uv run pytest -q tests/unit/test_manual_anchor.py tests/unit/test_calibration.py \
  tests/unit/test_lanec_drift.py tests/unit/test_measure_efficacy.py
python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path
rows = [json.loads(line) for line in Path("data/labels/manual_anchor_300.jsonl").read_text().splitlines()]
assert len(rows) == 300
assert Counter(row["payload"]["failure_mode"] for row in rows)["clean"] == 200
assert sum(row["gold_ungrounded"] + row["gold_contradicted"] for row in rows) == 100
PY
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
```

The committed-artifact test must run after regeneration so the new example file itself,
not merely the generator, is under test before moving to OpenRouter compatibility.

- [ ] **Step 8: Commit the anchor**

```bash
git add interlock/eval/anchor.py interlock/eval/induce.py \
  scripts/build_manual_anchor.py tests/unit/test_manual_anchor.py \
  data/labels/manual_anchor_300.jsonl data/labels/manual_anchor_300.summary.json
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(eval): diversify the 300-item grounding anchor"
```

---

### Task 2: Implement the OpenRouter-compatible judge boundary

**Files:**
- Create: `interlock/eval/openrouter_judge.py`
- Create: `tests/unit/test_openrouter_judge.py`

**Interfaces:**
- Produces: `JudgeItem(item_id: str, question: str, context: tuple[str, ...], answer: str)`
- Produces: `JudgeUsage(prompt_tokens: int, completion_tokens: int, cost_usd: float | None)`
- Produces: `JudgeResult(item_id: str, status: str, label: str | None, confidence: float | None, rationale: str, usage: JudgeUsage, latency_ms: float, error: str | None)`
- Produces: `OpenRouterJudge(client: httpx.Client, *, base_url: str, api_key: str, sleep: Callable[[float], None])`
- Produces: `OpenRouterJudge.judge(model: str, items: Sequence[JudgeItem]) -> list[JudgeResult]`
- Consumed by: `interlock/eval/judge_run.py`.

- [ ] **Step 1: Write failing parser and payload tests**

Use `httpx.MockTransport`, not assertions on a mock object. The transport handler must
inspect the real outgoing request and return a complete OpenAI-shaped response:

```python
def test_openrouter_payload_uses_chat_completions_and_never_ollama_fields() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return openai_response(results=[{"item_id": "a", "label": "clean", "confidence": 0.9, "rationale": "supported"}])

    results = judge_with(handler).judge("openai/gpt-5-nano", [ITEM])
    assert seen["path"] == "/api/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-secret"
    assert "format" not in seen["body"]
    assert "options" not in seen["body"]
    assert results[0].label == "clean"
```

Add separate tests for wrapped `{"results": [...]}`, direct arrays inside content,
nullable content, `finish_reason="length"`, malformed JSON, unknown labels, refusals,
and a response missing one requested item. Add a regression case proving the gold label
is read from the anchor row's top-level `gold_ungrounded`, `gold_contradicted`, and
`gold_unsafe` fields; the existing Ollama script incorrectly looks for these flags inside
`payload`, which would score every row as clean.

- [ ] **Step 2: Run the tests and verify RED**

```bash
uv run pytest -q tests/unit/test_openrouter_judge.py
```

Expected: collection FAIL because `interlock.eval.openrouter_judge` does not exist.

- [ ] **Step 3: Implement normalized types, prompts, and parsing**

Build one user prompt per batch with stable item IDs and an explicit evidence-only rule.
Request a JSON object shaped as `{"results": [...]}` and use `max_tokens = 256 * n` so
reasoning models do not reproduce the previously observed null-content/length failure.

Normalize all outcomes to statuses:

```python
JudgeStatus = Literal[
    "valid", "invalid_json", "invalid_label", "missing_item", "refused",
    "truncated", "auth_error", "rate_limited", "provider_error", "timeout",
]
```

Bound rationale to 800 characters and raw error text to 500 characters. Never include
request headers or the API key in an error.

- [ ] **Step 4: Write failing retry-classification tests**

Add table-driven cases proving:

- 401 returns `auth_error` after one request;
- 429 then 200 retries once;
- 500 then 200 retries once;
- repeated 429 stops after three total attempts;
- timeout is normalized and retryable; and
- invalid JSON is not retried because the provider successfully answered.

Assert the returned behavior and request count observed by the handler; do not assert a
mock method was called.

- [ ] **Step 5: Implement bounded retry behavior and run GREEN**

Retry only 429, 5xx, and transport timeouts. Accept an injected `sleep` so tests use
`lambda _: None`. Honor integer `Retry-After` seconds up to a 30-second ceiling;
otherwise use exponential delays of 1, 2 seconds plus injected jitter.

```bash
uv run pytest -q tests/unit/test_openrouter_judge.py
uv run ruff format --check interlock/eval/openrouter_judge.py tests/unit/test_openrouter_judge.py
uv run ruff check interlock/eval/openrouter_judge.py tests/unit/test_openrouter_judge.py
uv run pytest -q tests console/tests/python
```

- [ ] **Step 6: Commit the provider boundary**

```bash
git add interlock/eval/openrouter_judge.py tests/unit/test_openrouter_judge.py
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(eval): add an OpenRouter judge boundary"
```

---

### Task 3: Add resumable, stratified, budget-capped paid runs

**Files:**
- Create: `interlock/eval/judge_run.py`
- Rewrite: `scripts/eval_manual_anchors.py`
- Create: `tests/unit/test_judge_run.py`

**Interfaces:**
- Produces: `ModelPrice(input_per_million: Decimal, output_per_million: Decimal)`
- Produces: `RunConfig(model: str, limit: int, batch_size: int, max_cost_usd: Decimal, allow_external_context: bool)`
- Produces: `stratified_prefix(rows: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]`
- Produces: `load_completed(path: Path, *, model: str, dataset_digest: str) -> set[str]`
- Produces: `run_judgments(config: RunConfig, rows: Sequence[dict[str, Any]], judge: OpenRouterJudge, output: Path) -> RunSummary`
- Consumes: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and repeatable `--model` CLI values.

- [ ] **Step 1: Write failing stratification, resume, and budget tests**

Use a literal 12-row fixture spanning all modes and levels. Assert that `limit=6`
contains clean and all five failure modes rather than the first six file rows.

For resume, prewrite two JSONL rows for the selected model/dataset digest and use a real
in-memory fake judge that records item IDs; assert only unfinished IDs are returned and
the output contains each `(model, item_id)` once.

For budget, use literal prices and usage:

```python
def test_run_stops_before_dispatching_a_batch_that_crosses_the_cap(tmp_path: Path) -> None:
    config = RunConfig(
        model="openai/gpt-5-mini",
        limit=10,
        batch_size=5,
        max_cost_usd=Decimal("0.001"),
        allow_external_context=True,
    )
    summary = run_judgments(config, ROWS, fixed_usage_judge(), tmp_path / "run.jsonl")
    assert summary.termination_reason == "budget_cap"
    assert summary.cost_usd <= Decimal("0.001")
    assert summary.completed < 10
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/unit/test_judge_run.py
```

Expected: collection FAIL because `judge_run.py` is absent.

- [ ] **Step 3: Implement run identity, stratification, and resume**

Compute a SHA-256 digest over canonical JSON rows and prompt version. Round-robin modes,
then levels within each mode, for every prefix. Resume only rows matching the exact
model, dataset digest, and prompt version; refuse to mix incompatible runs in one file.

Write each normalized result and `flush()` immediately. Write run metadata atomically to
`<output>.meta.json` using a same-directory temporary file and `Path.replace()`.

- [ ] **Step 4: Implement conservative cost control**

Provide built-in price entries for the approved models:

```python
MODEL_PRICES = {
    "openai/gpt-5-nano": ModelPrice(Decimal("0.05"), Decimal("0.40")),
    "openai/gpt-5-mini": ModelPrice(Decimal("0.25"), Decimal("2.00")),
}
```

Treat these as run-control estimates, timestamp and print them, and prefer provider
reported cost when present. Unknown models require explicit CLI input/output prices.
Before each batch, reserve cost using its prompt tokens plus the full configured output
ceiling; stop before dispatch if reserved cumulative cost crosses the cap.

- [ ] **Step 5: Rewrite the CLI and add security tests**

The CLI defaults to environment configuration and requires:

```bash
uv run python scripts/eval_manual_anchors.py \
  --model openai/gpt-5-mini \
  --limit 10 --batch-size 5 --max-cost-usd 1.50 \
  --resume --allow-external-context \
  --output artifacts/eval/manual_anchor_judgments_openai-gpt-5-mini.jsonl
```

Without `--allow-external-context`, print case count, dataset digest, model, estimated
maximum cost, and `network_calls=0`, then exit 2. Without a key, exit 2 before creating
the HTTP client. Add a subprocess test with a sentinel key and assert neither stdout,
stderr, JSONL, nor metadata contains the sentinel.

- [ ] **Step 6: Run GREEN and full Python gates**

```bash
uv run pytest -q tests/unit/test_judge_run.py tests/unit/test_openrouter_judge.py
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
```

- [ ] **Step 7: Commit the paid runner**

```bash
git add interlock/eval/judge_run.py scripts/eval_manual_anchors.py \
  tests/unit/test_judge_run.py
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(eval): make anchor runs resumable and budget capped"
```

---

### Task 4: Report judge strengths, failures, and uncertainty

**Files:**
- Create: `interlock/eval/anchor_report.py`
- Create: `scripts/report_manual_anchors.py`
- Create: `tests/unit/test_anchor_report.py`

**Interfaces:**
- Produces: `build_anchor_report(labels: Sequence[dict[str, Any]], judgments: Sequence[dict[str, Any]], *, model: str) -> dict[str, Any]`
- Produces: `render_anchor_markdown(report: Mapping[str, Any]) -> str`
- Consumed by: `scripts/build_product_report.py`.

- [ ] **Step 1: Write failing metric tests with hand-derived values**

Use six labeled rows: two clean, three ungrounded, one contradicted. Provide four valid
judgments (three correct, one clean→ungrounded error), one truncated result, and one
invalid JSON result. Assert literals:

```python
assert report["operational"]["attempted"] == 6
assert report["operational"]["valid"] == 4
assert report["operational"]["valid_rate"] == pytest.approx(4 / 6)
assert report["agreement"]["correct"] == 3
assert report["agreement"]["rate"] == pytest.approx(3 / 4)
assert report["confusion"]["clean"]["ungrounded"] == 1
assert report["failures"][0]["item_id"] == "clean-missed"
```

Also assert per-mode, per-level, and per-domain denominators, cluster-level effective
sample size, and that the cluster-level Wilson interval is not zero-width for a perfect
small slice. Repeated prompt variants from one `evidence_cluster_id` must not narrow the
interval.

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/unit/test_anchor_report.py
```

Expected: collection FAIL because the report module does not exist.

- [ ] **Step 3: Implement pure aggregation**

Join by item ID and fail on duplicate labels or judgments. Operational validity uses all
attempts; agreement excludes invalid statuses. Compute per-label precision/recall/F1,
confusion, Wilson intervals, latency/token percentiles, and slices by mode, level, and
domain. Collapse repeated evidence clusters before Wilson intervals while reporting both
prompt count and effective cluster count. Include every disagreement and invalid result
in `failures` with bounded text.

Mark aggregate agreement `diagnostic_distribution: true` and include the literal note
that 200/100 is not production prevalence.

- [ ] **Step 4: Implement JSON and Markdown CLI**

The CLI reads raw local JSONL and writes:

```bash
uv run python scripts/report_manual_anchors.py \
  --labels data/labels/manual_anchor_300.jsonl \
  --judgments artifacts/eval/manual_anchor_judgments_openai-gpt-5-mini.jsonl \
  --model openai/gpt-5-mini \
  --json artifacts/eval/manual_anchor_report.json \
  --markdown artifacts/eval/manual_anchor_report.md
```

The Markdown starts with attempted/valid/agreement and intervals, then confusion,
slices, cost/latency, and failed examples. Render model output as escaped plain text.

- [ ] **Step 5: Run GREEN and commit**

```bash
uv run pytest -q tests/unit/test_anchor_report.py
uv run ruff format --check .
uv run ruff check .
uv run pytest -q tests console/tests/python
git add interlock/eval/anchor_report.py scripts/report_manual_anchors.py \
  tests/unit/test_anchor_report.py
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(eval): report grounding strengths and failure slices"
```

---

### Task 5: Capture deterministic product gates and build the combined evidence report

**Files:**
- Create: `scripts/run_product_checks.py`
- Create: `interlock/eval/product_report.py`
- Create: `scripts/build_product_report.py`
- Create: `tests/unit/test_product_report.py`
- Modify: `scripts/eval.py`
- Modify: `tests/unit/test_eval_harness.py`

**Interfaces:**
- Produces: `CheckResult(name: str, command: tuple[str, ...], status: str, exit_code: int, duration_ms: float)`
- Produces: `run_checks(runner: CommandRunner, *, include_e2e: bool) -> dict[str, Any]`
- Produces: `build_product_report(anchor: Mapping[str, Any] | None, seeded: Mapping[str, Any] | None, checks: Mapping[str, Any] | None, artifacts: Mapping[str, Any]) -> dict[str, Any]`
- Produces: `render_product_markdown(report: Mapping[str, Any]) -> str`

- [ ] **Step 1: Write failing seeded-slice artifact test**

Extend the existing eval script's pure payload construction so a test can assert every
case outcome records `case_id`, category, action, caught/intervened, stakes, tier,
overhead, spend, saved tokens, and reasons. Assert the artifact contains slices for
category, stakes band, action, and domain with denominators summing to 200.

- [ ] **Step 2: Run and verify RED, then implement the seeded slices**

```bash
uv run pytest -q tests/unit/test_eval_harness.py -k "artifact or slice"
```

Expected: FAIL because `scripts/eval.py` currently writes only aggregate actions and
misses. Extract a pure `build_eval_payload()` and include outcomes/slices without
changing decision behavior or the six metric definitions.

- [ ] **Step 3: Write failing verification-manifest tests**

Inject a `CommandRunner` returning controlled exit codes. Assert that one failed check
is preserved as `miss`, skipped e2e is `not_run`, and no output/environment values are
copied into the manifest. The production command matrix is literal:

- `uv run ruff format --check .`
- `uv run ruff check .`
- strict mypy command from CI;
- `uv run pytest -q tests console/tests/python`;
- frontend unit, typecheck, build; and
- Playwright when `--include-e2e` is supplied.

- [ ] **Step 4: Implement the local check runner**

Use `subprocess.run` with an explicit repository cwd, inherited environment, no shell,
and captured bounded output digests rather than raw output. Write exit code, duration,
command, and status atomically to `artifacts/eval/verification.json`. A failed command
does not prevent later checks from running; the script exits 1 after writing all results.

- [ ] **Step 5: Write failing combined-report tests**

Use literal small artifacts and assert:

- a target miss stays `miss` even when other checks pass;
- absent economics stays `unavailable`, never zero;
- unknown OpenRouter price coverage sets `economics_accurate` false;
- missing anchor data is `not_run`, not pass;
- a confidence-bound miss cannot be promoted by a passing point estimate; and
- strength/failure lists link to the evidence artifact that established them.

- [ ] **Step 6: Implement JSON/Markdown combination**

Build evidence rows with statuses `pass`, `miss`, `inconclusive`, `unavailable`, and
`not_run`. Consume, but never rerun, the anchor report, seeded reports, verification,
fairness, calibration, latency, and ledger-summary artifacts. Render measured and
modelled values in separate sections and include an explicit failure appendix.

- [ ] **Step 7: Run GREEN and commit**

```bash
uv run pytest -q tests/unit/test_product_report.py tests/unit/test_eval_harness.py
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
git add scripts/run_product_checks.py interlock/eval/product_report.py \
  scripts/build_product_report.py tests/unit/test_product_report.py \
  scripts/eval.py tests/unit/test_eval_harness.py
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(eval): combine product evidence without hiding misses"
```

---

### Task 6: Add explicit-opt-in live OpenRouter gateway rehearsal

**Files:**
- Create: `interlock/eval/live_rehearsal.py`
- Create: `scripts/rehearse_openrouter.py`
- Create: `tests/unit/test_live_rehearsal.py`

**Interfaces:**
- Produces: `RehearsalCase(case_id: str, prompt: str, expected_events: tuple[str, ...], expected_terminal: str)`
- Produces: `rehearsal_plan(cases: Sequence[RehearsalCase], *, model_tiers: Mapping[str, str]) -> dict[str, Any]`
- Produces: `parse_rehearsal_stream(lines: Iterable[str]) -> RehearsalResult`
- Produces: `run_rehearsal(..., allow_external_context: bool) -> list[RehearsalResult]`

- [ ] **Step 1: Write failing opt-in and SSE tests**

Assert that `allow_external_context=False` returns a plan with dataset digest,
`network_calls=0`, and no prompt text. Feed real SSE text into the parser and assert
request ID, `interlock.stakes`, `interlock.signal`, `interlock.decision`, optional hold,
content ordering, and `[DONE]`. Add malformed-event and partial-output cases.

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/unit/test_live_rehearsal.py
```

Expected: collection FAIL because the live-rehearsal module is absent.

- [ ] **Step 3: Implement a small stratified rehearsal**

Define no more than 12 cases spanning clean grounded answer, L2 repair candidate, L4
irreversible tool hold, L5 canary/PII block, low/high stakes, malformed provider output,
and provider timeout. External calls use only the cases explicitly selected by the CLI.
Record event names, action, tier, request ID presence, partial output, latency, and token
usage; do not record the API key or authorization header.

The CLI refuses network execution unless both `--allow-external-context` and an API key
are present. It does not start or stop unrelated user processes. It accepts a gateway
URL and writes `artifacts/eval/live_rehearsal.json` atomically.

- [ ] **Step 4: Run GREEN and commit**

```bash
uv run pytest -q tests/unit/test_live_rehearsal.py
uv run ruff format --check .
uv run ruff check .
uv run pytest -q tests console/tests/python
git add interlock/eval/live_rehearsal.py scripts/rehearse_openrouter.py \
  tests/unit/test_live_rehearsal.py
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(eval): add an explicit-opt-in live rehearsal"
```

---

### Task 7: Document commands and run the staged evaluation

**Files:**
- Modify: `README.md`
- Modify: `docs/LIMITATIONS.md`
- Modify: `Makefile`
- Generate locally: `artifacts/eval/manual_anchor_judgments_openai-gpt-5-mini.jsonl` (ignored raw artifact)
- Generate: `artifacts/eval/manual_anchor_report.json`
- Generate: `artifacts/eval/manual_anchor_report.md`
- Generate: `artifacts/eval/verification.json`
- Generate: `artifacts/eval/product_report.json`
- Generate: `artifacts/eval/product_report.md`

**Interfaces:**
- Produces: `make eval-anchor-plan`, `make eval-product-local`, and documented paid-run commands.

- [ ] **Step 1: Update documentation and Make targets**

Document:

- the 200/100 diagnostic distribution and why it is not production prevalence;
- `.env` loading with `set -a; source .env; set +a`;
- dry-run planning without external-data permission;
- staged 10 → 50 → 300 resume commands;
- the USD 1.50 cap and remaining USD 0.50 reserve;
- raw judgment privacy/ignore behavior;
- report generation and interpretation;
- the difference between judge agreement and product effectiveness; and
- the internal OpenRouter price-book caveat.

Add Make targets that never hide the external-data flag inside the Makefile. The paid
target must require the caller to pass `ALLOW_EXTERNAL_CONTEXT=1`; otherwise it runs
plan-only mode.

- [ ] **Step 2: Run the complete local verification manifest**

```bash
uv run python scripts/run_product_checks.py \
  --include-e2e --json artifacts/eval/verification.json
```

Inspect every exit code. If a command fails, use systematic debugging and add a failing
regression test before changing production code.

- [ ] **Step 3: Run the 10-case paid smoke**

```bash
set -a; source .env; set +a
uv run python scripts/eval_manual_anchors.py \
  --model openai/gpt-5-mini --limit 10 --batch-size 5 \
  --max-cost-usd 1.50 --resume --allow-external-context \
  --output artifacts/eval/manual_anchor_judgments_openai-gpt-5-mini.jsonl
```

Generate the anchor report and require: 10 attempted, no auth errors, at least 9 valid,
no leaked key, and cumulative cost below USD 0.10. Stop and diagnose if any gate misses.

- [ ] **Step 4: Resume through 50**

Repeat with `--limit 50`. Require at least 95% operational validity, cost below USD
0.40, no duplicate `(model, item_id)` rows, and every failure mode represented. Stop and
diagnose before the full run if any condition fails.

- [ ] **Step 5: Resume through 300**

Repeat with `--limit 300`. The runner must stop automatically before USD 1.50. Do not
raise the cap during the run. Generate the final anchor JSON/Markdown report.

- [ ] **Step 6: Run the live rehearsal only with remaining budget**

Check OpenRouter key usage. If at least USD 0.20 remains, run the 12-case rehearsal with
`--allow-external-context`; otherwise record `not_run` with reason `budget_reserve`.
Never convert budget exhaustion into a pass or unavailable result.

- [ ] **Step 7: Build and inspect the combined report**

```bash
uv run python scripts/build_product_report.py \
  --anchor artifacts/eval/manual_anchor_report.json \
  --seeded artifacts/eval/report.json \
  --guaranteed artifacts/eval/report-guaranteed.json \
  --verification artifacts/eval/verification.json \
  --json artifacts/eval/product_report.json \
  --markdown artifacts/eval/product_report.md
```

Manually inspect that F-019 remains a visible miss, unavailable economics are not zero,
invalid judge rows are counted, intervals are present, and the strengths section does
not omit the failure appendix.

- [ ] **Step 8: Run final fresh verification**

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
npm --prefix console run test:unit -- --run
npm --prefix console run typecheck
npm --prefix console run build
npm --prefix console run test:e2e
git diff --check
```

- [ ] **Step 9: Commit documentation and non-secret aggregate evidence**

Do not stage the ignored raw judgment JSONL or `.env`.

```bash
git add README.md docs/LIMITATIONS.md Makefile \
  artifacts/eval/manual_anchor_report.json artifacts/eval/manual_anchor_report.md \
  artifacts/eval/verification.json \
  artifacts/eval/product_report.json artifacts/eval/product_report.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs(eval): publish the OpenRouter product evidence pack"
```

---

## Final Review Gate

After Task 7:

1. Run `find-bugs` over `b8a12a2..HEAD`, focusing on metric denominators, resume identity,
   cost cap races, malformed model output, and accidental external calls.
2. Run `security-review` over the same diff, focusing on API-key handling, prompt/data
   egress, error redaction, untrusted judge output, JSONL injection, and report rendering.
3. Run the two-axis `code-review` against repository standards and the approved spec.
4. Add one failing regression test per verified finding, fix in one focused commit, and
   rerun every final verification command with fresh output.
5. Inspect `git status`, commit history, and cached diffs to confirm no unrelated
   frontend, `.claude/`, `.gitignore`, `graphify-out/`, `tmp/`, `.env`, or raw paid
   judgment files were staged.
