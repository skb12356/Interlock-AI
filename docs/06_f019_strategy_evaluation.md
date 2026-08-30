# F-019 False-Intervention Strategy Evaluation

## Decision

Production uses two ordinary-action abstention checks:

1. Maximum calibrated defect probability must be at least 1%.
2. The selected action must reduce expected loss by at least 50% versus `L0_pass`.

Deterministic hard rules run first, and neither check can restore passing when the
conformal filter or action-availability constraints remove it.

The 1% probability floor is the decisive improvement. It became safe only after fixing
the grounding detector and recalibrating it. The current certified conformal threshold
is 37%; the lower 1% floor cannot suppress a case required by that filter.

## Root Cause

The original `question_drift` compared question words directly with terse answer words.
It was inverted on the 300 anchors: average raw drift was 0.874 for clean answers and
0.862 for defects. The replacement compares question and answer through the retrieved
context. It detects either an unanswerable question or an answer that follows a
distractor passage.

The audit also found two seeded `number_corrupted` examples that violated their own
contract: the replacement number still appeared elsewhere in an enumeration-heavy
passage. The inducer now retries until the corrupted figure is genuinely absent, and a
regression test checks every generated example with the production numeric detector.

## Calibration

Calibration remains document-disjoint and domain-stratified: 26 calibration documents,
19 evaluation documents, zero shared passages.

| Measurement | Before detector fix | After detector fix |
|---|---:|---:|
| Out-of-fold ECE | 0.0037 | 0.0046 |
| Brier score | 0.0207 | 0.0005 |
| AUROC | 0.9088 | 1.0000 |
| Mean clean calibrated risk | about 1.9% | 0.2568% |
| Conformal threshold | 1.5% | 37.0% |
| Certified intervention rate | 100% | 9.22% |

The near-perfect synthetic calibration result is not a production-quality claim. It
shows that the induced taxonomy is now separable; external, naturally occurring defects
remain necessary.

## Strategy Comparison

Thirty-three strategies were run on identical fixed answers across three independently
seeded 200-case sets and all 300 human-labelled calibration anchors. Eligibility
required at least 90% catch and at most 1% empirical ungrounded escapes on every seed,
with no degradation from the anchor baseline.

| Strategy | Seeded FI mean | Anchor FI | Seeded catch | Seeded escapes | Anchor catch/escapes |
|---|---:|---:|---:|---:|---:|
| Raw expected-loss argmin | 72.38% | 58.89% | 100% | 0% | 100% / 0% |
| Relative gain 50% | 68.10% | 51.11% | 100% | 0% | 100% / 0% |
| Impact scale 10% | 68.10% | 51.11% | 89.15% | 14.67% | 90% / 10% |
| Probability floor 0.5% | 0% | 0% | 100% | 0% | 100% / 0% |
| Probability floor 1% | 0% | 0% | 100% | 0% | 100% / 0% |
| Probability floor 2% | 0% | 0% | 100% | 0% | 100% / 0% |
| Production: 1% floor + 50% gain | 0% | 0% | 100% | 0% | 100% / 0% |

Behaviorally tied candidates prefer the 1% floor because it matches the governed risk
level, leaves impact semantics unchanged, and provides more clean-risk drift tolerance
than 0.5%. The complete run-level evidence is in
`artifacts/eval/f019_strategy_comparison.json` and can be reproduced with:

```powershell
uv run python scripts/compare_f019_strategies.py
```

## Production Results

Across seeds 20260826, 20260827, and 20260828:

| Measurement | Seed 1 | Seed 2 | Seed 3 |
|---|---:|---:|---:|
| False interventions (n=140 clean) | 0% | 0% | 0% |
| Pre-action catch (n=43) | 100% | 100% | 100% |
| Empirical ungrounded escapes (n=25) | 0% | 0% | 0% |
| Verification cost | 0.78% | 0.48% | 0.62% |
| Net spend change | -22.56% | -20.67% | -20.81% |

All 300 anchors also produce 0% false interventions on 270 clean items and 100% catch
on 30 defects. Those anchors are part of the calibration split, so they are secondary
evidence rather than an untouched holdout.

## Metric Correction

The previous false-intervention denominator used all 157 cases without a content/tool
defect label. That incorrectly included ten demographic fairness probes and seven agent
loop probes. Fairness probes measure action parity, and loops explicitly require the
loop breaker; neither is ordinary traffic that deserved no intervention. The denominator
now uses the 140 cases explicitly generated as `clean`, matching the written evaluation
design and producing a 95% Wilson upper bound of 2.67% for the observed 0/140 rate.

## Operating Guardrails

Monitor the share of reviewed clean answers whose maximum calibrated defect probability
reaches 1%. The sensitivity sweep shows 0% measured false interventions through a 0.3%
stipulated clean floor, but about 67% once clean risk reaches 1%. Recalibrate or roll back
the detector before changing the governed floor when any untouched review window shows:

- false interventions above 2%;
- pre-action catch below 90%; or
- empirical ungrounded escapes above 1%.

Do not tune the 1% floor on the release seeds. Changes require a new document-disjoint
calibration split and a new untouched evaluation set.

## Limits

The seeded experiment holds model output fixed and primarily uses induced failures.
Zero observed false interventions does not prove a zero production rate. The 95% Wilson
interval is 0-2.67%, slightly wider than the 2% target, so a larger untouched clean set
is required to establish the target with 95% statistical confidence. Natural production
drift, external defect taxonomies, and post-action efficacy remain separate evidence
requirements.
