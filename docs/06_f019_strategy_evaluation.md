# F-019 False-Intervention Strategy Evaluation

## Decision

Production requires an ordinary intervention to reduce expected loss by at least 50%
relative to `L0_pass`. Deterministic hard rules still run first. The rule also cannot
restore passing when conformal or action-availability constraints remove it.

The comparison artifact is `artifacts/eval/f019_strategy_comparison.json`. It contains
every run, action count, escape diagnostic, the selection rule, and the exact policy
hash. Reproduce it with:

```powershell
uv run python scripts/compare_f019_strategies.py
```

## Method

Thirty strategies were run on the same fixed answers and calibrated probabilities for
three independently seeded 200-case sets. Each was also checked against all 300 human-
labelled anchors. Candidate methods included impact scaling and caps, absolute
probability gates, evidence-aware gates, stakes-band gates, relative-gain margins, and
combined rules.

A strategy was eligible only if every seeded run retained at least 90% pre-action catch
and at most 1% empirical ungrounded escapes. It also had to preserve the anchor
baseline's 96.67% catch and 3.33% escape rate. Among eligible strategies, selection
minimized the worst false-intervention rate across seeded and anchor sets.

## Results

| Strategy | Seeded FI mean | Anchor FI | Seeded catch | Seeded escapes | Anchor catch/escapes | Decision |
|---|---:|---:|---:|---:|---:|---|
| Baseline argmin | 88.96% | 94.81% | 100% | 0% | 96.67% / 3.33% | Too many interventions |
| Impact scale 25% | 73.25% | 58.89% | 100% | 0% | 96.67% / 3.33% | Eligible, changes harm semantics |
| Probability gate 2% | 0% | 0% | 98.45% | 2.67% | 73.33% / 26.67% | Rejected: unsafe escapes |
| Stakes gate below Rs.40k | 30.79% | 40.74% | 100% | 0% | 90% / 10% | Rejected: anchor degradation |
| Relative gain 50% | 68.79% | 51.11% | 100% | 0% | 96.67% / 3.33% | Selected |
| Relative gain 60% | 68.79% | 51.11% | 100% | 0% | 96.67% / 3.33% | Same result, less headroom |
| Relative gain 70% | 68.79% | 51.11% | 89.92% | 13.33% | 90% / 10% | Rejected: safety boundary crossed |

The 50% rule lowers mean seeded false interventions by 20.17 percentage points, a
22.7% relative reduction. It also lowers anchor false interventions by 43.70 points.
Production seed results are 68.15%, 68.79%, and 69.43%, with 100% catch and 0%
empirical ungrounded escapes on each seed.

## Interpretation

Absolute probability gates produced the lowest false-intervention rate but missed
number-corrupted high-stakes answers and performed poorly on the labelled anchors. A
percentage alone ignores impact and evidence. Impact scaling preserved measured safety
at 25%, but silently changes the meaning of the governed monetary stakes. The relative-
gain rule instead asks whether an action materially improves the existing objective.

This does not meet the global 2% false-intervention target. Every clean seeded case above
Rs.10,000 still receives an intervention because its modelled gain exceeds 50%. Lowering
that further requires either better independent risk estimates, reviewed request-level
impact accounting, or acceptance of a measured increase in missed defects.

## Evidence Limits

The seeded experiment holds generated answers fixed, so it tests the control-plane
decision and not whether repair changes model output. The 300 anchors come from the
calibration split and are a secondary check, not an untouched holdout. The current
harness makes one decision per case, so request-versus-sentence impact charging cannot
be compared here. Confidence-bound or sigma rules were not tested because no calibrated
per-case uncertainty estimate currently exists.
