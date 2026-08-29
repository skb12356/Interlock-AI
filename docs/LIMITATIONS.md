# Limitations

## Current Scorecard Misses

The latest regenerated evaluation still misses three release targets:

- Verification cost: 5.20%
- Net spend: -18.96%
- False interventions: 85.35%

These are not formatting issues in the report. The current objective prices high-stakes
requests so strongly that clean high-stakes answers still receive interventions. Finding
F-019 remains a product/policy decision about the impact model and false-intervention
definition, not a detector tuning task.

## Impact Model

The policy charges the full request impact to each sentence-level decision. That is
conservative, but it can over-price multi-sentence answers and makes high-stakes clean
traffic difficult to pass. The code now exposes the loss table, regret, rework and net
value so this tradeoff is visible, but the policy decision itself is still explicit.

## Efficacy

The efficacy matrix is still policy-backed rather than fully re-measured from live
forced-action outcomes with confidence intervals. The ledger can expose real regret and
rework when those rows exist, and the runbook records when those values are unmeasured.
Do not present assumed repair efficacy as a measured production result.

## Observer

The observer probe is an optional in-process signal. On a clean CPU-only checkout the
gateway runs on deterministic signals and reports the missing probe on `/health`.
Context-key caching is now visible in probe health, but the current cross-encoder does
not support true autoregressive KV reuse.

## Provenance

The tool interlock uses a conservative provenance lattice over retrieved fragments and
tool arguments. It is strong against hidden untrusted instructions, but paraphrase and
ambiguous influence can over-block legitimate tool calls. Review-card evidence should be
shown to operators rather than treating every freeze as a confirmed attack.

## Single Vertical

The corpus, policy and seeded eval set are banking-specific. The policy loader supports
tenant/vertical policies, but the measured artifacts in this repository should not be
claimed as evidence for healthcare, insurance, telecom or public-sector deployments
without rebuilding the corpus, labels, calibrator and evaluation split.

## Lane C

Lane C projections are live and exposed, but the current rehearsal database contains no
fairness pairs. The endpoint correctly reports that no bet has been placed when there
are too few observations. A fairness claim needs real counterfactual pairs in the ledger,
not just endpoint availability. Completed offline observations can be imported with
`uv run python scripts/import_fairness_pairs.py pairs.jsonl`; incomplete or self-pairs
are rejected and writes use the ledger lock.

## Rehearsal Environment

Ollama did not respond on this machine during the final local run, so the four-scenario
rehearsal was run through the real gateway and console against a deterministic local
OpenAI-compatible fixture upstream. That validates gateway streaming, ConsoleHub
publishing, holds and metrics endpoints; it is not a live-model quality rehearsal.

## Upload Extraction

The upload contract accepts text and PDF bytes and preserves the result as
`retrieved_untrusted`. The minimal clean-checkout runtime extracts printable PDF strings
without a heavyweight parser; this covers the included white-text fixture but does not
claim arbitrary-PDF layout, OCR, or encrypted-document support.
