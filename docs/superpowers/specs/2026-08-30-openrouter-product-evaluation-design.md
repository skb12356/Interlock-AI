# OpenRouter Product Evaluation Design

## Purpose

Build a reproducible evaluation system that shows where Interlock succeeds, where it
fails, and how confident each conclusion is. The evaluation must exercise the product
as a control plane rather than treating one LLM score as proof of the whole system.

The result is a layered evidence pack:

1. a 300-item response-grounding anchor judged through OpenRouter;
2. deterministic control-plane evaluation for routing, interventions, holds, tools,
   fairness, loops, privacy, and failure modes;
3. live provider and gateway rehearsals with explicit external-data consent;
4. latency, cost, console, protocol, and accessibility gates; and
5. one combined report that publishes passes, misses, confidence intervals, invalid
   judgments, unavailable measurements, and inspectable failed examples.

The 300-item anchor does not replace the deterministic suites. Generative judges are
appropriate for auditing answer labels offline, but they are not authoritative for
deterministic security properties or hot-path behavior.

## Anchor Composition

The anchor contains exactly 300 cases from the calibration document split. It changes
from the current 270 clean / 30 defective distribution to exactly 200 clean / 100
grounding failures.

| Failure mode | Gold defect | Count |
| --- | --- | ---: |
| `clean` | none | 200 |
| `retrieval_dropped` | `ungrounded` | 20 |
| `number_corrupted` | `ungrounded` | 20 |
| `clause_swapped` | `ungrounded` | 20 |
| `unanswerable` | `ungrounded` | 20 |
| `contradiction` | `contradicted` | 20 |
| **Total** | 200 clean / 80 ungrounded / 20 contradicted | **300** |

This is a deliberately diagnostic audit distribution, not an estimate of production
prevalence. Reports must state that distinction beside every aggregate accuracy number.
Calibration fitting continues to use its separately declared production base-rate
assumption; the 200/100 audit ratio must not silently alter calibrated probabilities.

### Challenge levels

Every anchor row carries `challenge_level` with these meanings:

- **L1 — direct:** one relevant passage and a direct answer or visibly corrupted claim.
- **L2 — distractor:** the relevant evidence is mixed with plausible irrelevant
  passages, or the unsupported answer remains plausible from general knowledge.
- **L3 — conflict:** multi-passage evidence, superseded clauses, subtle numerical or
  citation changes, or a question the supplied evidence cannot settle.

The clean rows are distributed 67 L1, 67 L2, and 66 L3. Each 20-row failure mode is
distributed 7 L1, 7 L2, and 6 L3. Generation is deterministic for a fixed seed, and the
builder fails rather than silently substituting clean rows when it cannot meet a quota.

Each row also records the banking `domain`, context count, source document IDs, failure
mode, gold defect, offending span, and a human-readable review basis. Coverage reports
must show domain and challenge-level counts so an aggregate cannot hide a missing cell.

The 300 rows are prompt variants, not 300 independent evidence cases. Every row carries
an `evidence_cluster_id` derived only from answer, context, gold label, failure mode, and
challenge level; question wording and row IDs are excluded. Dataset summaries report
`unique_evidence_clusters`, `prompt_variants`, and `max_cluster_size`. Agreement reports
must publish both prompt count and cluster-level effective sample size, collapse repeated
evidence before Wilson intervals, and never present a row-level interval as independent.

## OpenRouter-Compatible Judge

The judge runner supports the OpenAI-compatible contract used by OpenRouter:

- base URL from `OPENAI_BASE_URL`, defaulting to `https://openrouter.ai/api/v1`;
- bearer token from `OPENAI_API_KEY` without logging or persisting it;
- model IDs such as `openai/gpt-5-nano` and `openai/gpt-5-mini`;
- `POST /chat/completions` with JSON-only output instructions;
- nullable content, token-limit finishes, malformed JSON, refusals, 429s, timeouts, and
  provider 5xx responses represented explicitly rather than scored as disagreement;
- bounded retries for retryable failures only, with exponential backoff and jitter;
- resumable JSONL output keyed by `(run_id, model, item_id)` so an interrupted paid run
  never pays twice for a completed row;
- atomic run metadata containing model, dataset digest, prompt version, start/end time,
  request counts, token usage, reported cost where available, and termination reason;
- `--limit`, `--batch-size`, `--max-cost-usd`, and `--resume` controls; and
- no automatic fallback to a different model because that would corrupt model-specific
  measurements.

The runner accepts an injected HTTP transport in tests. Unit tests use recorded or
synthetic responses and never make paid network calls.

### Paid-run policy

The default paid sequence is:

1. validate configuration without printing the key;
2. run 10 cases spanning clean/failure modes and challenge levels;
3. inspect validity, token use, cost, and parsing;
4. resume through 50 total cases;
5. inspect the same gates; and
6. resume through all 300 only while cumulative reported or conservatively estimated
   cost remains below the configured cap.

The default cap is USD 1.50, leaving at least USD 0.50 of the stated USD 2.00 budget for
live gateway rehearsals and unexpected retries. A run stops before dispatching a batch
whose conservative projected cost would cross the cap.

## Evaluation Layers

### Layer 1: Response-grounding audit

The OpenRouter judge compares answer text only with supplied context. It returns one of
`clean`, `ungrounded`, or `contradicted`, plus confidence and rationale. Unsafe actions,
PII, canaries, tool provenance, and product intervention decisions are intentionally not
delegated to this judge.

The report includes:

- overall valid-judgment agreement with a cluster-level Wilson confidence interval;
- per-label precision, recall, and F1;
- confusion matrix;
- agreement by failure mode, challenge level, and domain;
- invalid, refused, timed-out, truncated, and unparseable counts;
- latency and token distributions by model; and
- every disagreement with item ID, gold label, judge label, rationale, and prompt/data
  digests sufficient to reproduce it without exposing the API key.

Invalid judgments stay in the denominator of operational reliability reporting but are
excluded from label-agreement arithmetic. Both denominators are printed together.

### Layer 2: Deterministic control-plane evaluation

The existing paired seeded evaluation remains the authority for Interlock behavior. Its
report is extended rather than replaced and is sliced by category, stakes band, action,
and domain. It covers:

- clean pass-through and false interventions;
- missing retrieval, corrupted figures, contradictions, and unavailable answers;
- L0 pass through L5 block reachability and correctness;
- prompt injection and poisoned-document provenance;
- PII and tenant-canary egress hard stops;
- reversible and irreversible tool-call decisions;
- durable L4 holds, expiry, approval/rejection, restart recovery, and token secrecy;
- demographic twins and anytime-valid fairness monitoring;
- repeated tool loops and saved-token accounting;
- low-stakes fail-open and high-stakes fail-closed behavior under deadlines; and
- cache eligibility, routing tiers, shadow sampling, and ledger attribution.

These checks run locally and do not consume OpenRouter credits.

### Layer 3: Live gateway rehearsal

A small stratified rehearsal verifies the real OpenAI-compatible streaming path for both
configured tiers. It checks request IDs, named SSE events, partial-output behavior,
sentence ordering, decision persistence, holds, and final `[DONE]` framing.

Because the gateway may send retrieved repository passages to an external provider, the
rehearsal requires an explicit `--allow-external-context` flag. Without the flag it
prints the dataset digest and planned case count, then exits without network calls. The
flag is recorded in local run metadata but no credential is recorded.

### Layer 4: Product and operator experience

Existing Python, contract, property, frontend unit, type, build, and Playwright suites
remain mandatory. The four deterministic console journeys cover clean pass, repair,
hold, and block at desktop and mobile sizes. Browser-console errors, token leakage,
keyboard navigation, reconnect behavior, artifact honesty, and unavailable economics
are failures, not visual-review notes.

### Layer 5: Performance and economics

The combined report distinguishes measured from modelled quantities. It shows p50/p95
decision overhead, provider time-to-first-token where live data exists, action latency,
observed token usage, OpenRouter-reported cost when available, internal price-book
coverage, routing mix, verification share, and ledger totals.

An OpenRouter model absent from Interlock's internal price book is reported as
`economics_accurate: false`; the report never presents fallback pricing as an invoice.
Regret, rework, Lane C net value, and confidence intervals remain unavailable until real
outcome data exists.

## Combined Report

The aggregator consumes immutable JSON/JSONL artifacts and writes both machine-readable
JSON and a readable Markdown summary. It does not rerun paid work implicitly.

The summary begins with an evidence table containing:

- requirement or metric;
- target;
- measured result and confidence interval;
- sample size and exclusions;
- status: `pass`, `miss`, `inconclusive`, `unavailable`, or `not_run`;
- evidence artifact; and
- caveat.

It then presents strengths, verified failures, reliability/degradation findings, cost
and latency, coverage gaps, and a failure appendix. A target is never marked passed from
a point estimate alone when its required confidence bound misses the target.

## Security and Data Handling

- `.env`, API keys, resume tokens, tenant canaries, raw authorization headers, and
  production prompts are never written to artifacts or logs.
- Paid commands fail closed when `OPENAI_API_KEY` is absent.
- External-context transmission is opt-in and visible in command output.
- Judge output is untrusted data: it is parsed as JSON, length bounded, stored as text,
  and never rendered as HTML.
- Prompt and dataset versions are represented by digests; reports do not need secrets
  to be reproducible.
- Existing user changes under `console/`, `.claude/`, `.gitignore`, `graphify-out/`, and
  `tmp/` are outside this work and must not be staged.

## Acceptance Criteria

The work is accepted when:

1. the anchor builder deterministically produces exactly 200 clean and 100 defective
   rows with all five 20-row failure quotas and declared challenge-level quotas;
2. quota failure is loud and cannot fall back to a different label;
3. the OpenRouter runner passes unit tests for success, structured output variants,
   nullable content, truncation, invalid JSON, refusal, 401, 429 retry, 5xx retry,
   timeout, resume, duplicate suppression, budget stop, and secret redaction;
4. no unit or CI test performs a paid network call;
5. the combined report exposes per-mode strengths and failures, confidence intervals,
   invalid judgments, coverage, cost, and unavailable evidence;
6. deterministic suites continue to own security and control-plane claims;
7. live external-context runs require explicit opt-in;
8. Ruff format/lint, strict mypy, the complete Python suite, frontend unit tests,
   typecheck, build, and Playwright all pass with fresh output; and
9. the paid 10/50/300 run is resumable and cannot exceed its configured cost cap.
