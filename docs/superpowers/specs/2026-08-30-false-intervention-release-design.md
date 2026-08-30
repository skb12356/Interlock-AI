# False-Intervention Reduction and Release Completion Design

## Goal

Reduce false and disruptive interventions as far as the measured evidence permits while
preserving the product's safety boundary, then close every remaining Person 1 and Person 2
release item that can be completed and verified locally before submission.

## Current Evidence

The three seeded product runs report false-intervention rates of 85.35%, 91.08%, and
90.45% over 157 non-defective cases per seed. Disruptive L2-L5 rates are 71.34%, 73.25%,
and 75.16%. Pre-action catch is 100% and empirical ungrounded escapes are 0% on these
sets. The failure is stakes-shaped: traffic below Rs.100 passes, while almost all traffic
above Rs.1,000 receives a disruptive action.

The separate 300-case OpenRouter audit is diagnostic evidence about an offline judge. It
does not replace the seeded product evaluation. Its generated anchor also contains
three-class taxonomy disagreements and remains explicitly unreviewed.

## Safety Constraints

- Hard canary, PII-egress, tool-provenance, monetary-cap, and irreversible-action rules
  remain unchanged and always run before optimization.
- A candidate policy is ineligible if aggregate pre-action catch falls below 90% on any
  seeded run or empirical ungrounded escapes increase above the baseline.
- Results must report both annotation-inclusive and disruptive false interventions.
- No candidate may be selected from one seed; all seeds 20260826, 20260827, and 20260828
  are required.
- No generated or replayed evidence may be described as human-reviewed or production
  traffic.
- The selected behavior must be represented in a versioned policy/configuration surface,
  covered by regression tests, and visible in the decision explanation.

## Candidate Methods

### A. Request-impact allocation

The current objective charges the full request impact to each sentence. Evaluate explicit
impact scales that represent a bounded sentence share of request harm while retaining the
original stakes estimate for routing, buffering, hard rules, ledger records, and console
display. Candidate scales are 1.0, 0.5, 0.25, 0.1, 0.05, and 0.025.

This method directly addresses the documented root cause but risks understating a single
catastrophic sentence. Therefore it is eligible only if the safety constraints hold and
the effective impact is disclosed in the loss-table explanation.

### B. Calibrated probability deadband

Evaluate subtractive detector noise floors of 0, 0.0025, 0.005, 0.01, 0.015, and 0.02
before expected-loss pricing, with probabilities clamped at zero. Hard rules do not use
the deadband. This tests whether the detector's clean-text floor can be separated from
actionable excess risk.

Because calibrated probabilities are intended to be literal probabilities, this method
is more statistically invasive than impact allocation. It is not selected unless it
dominates impact allocation on both safety and intervention metrics.

### C. Intervention-cost repricing

Evaluate nuisance and time-cost multipliers of 1, 2, 5, 10, 20, and 50 for L1-L5 while
leaving L0 at zero. This asks how highly the policy must price needless customer
disruption before pass becomes competitive. Human-review costs remain unconditional.

This method is governance-friendly but can hide an impact-model error behind inflated
nuisance prices. It is selected only when the prices remain defensible and it dominates
the more direct methods.

### D. Combined Pareto search

Evaluate bounded combinations of impact scale, deadband, and nuisance multiplier after
the single-family sweeps. A candidate is Pareto-dominated when another candidate has no
worse catch or escapes and lower or equal annotation-inclusive and disruptive false
interventions. Rank eligible non-dominated candidates by:

1. worst-seed disruptive false-intervention rate;
2. worst-seed annotation-inclusive false-intervention rate;
3. smallest departure from the current policy;
4. lower verification spend and added latency.

The report retains every candidate, including rejected ones, so the selected result is
auditable rather than a hidden threshold search.

## Implementation Architecture

Add a pure policy-experiment module that transforms effective probabilities, effective
impact, and action nuisance for an injected candidate without mutating the loaded policy.
An experiment engine reuses the real calibrator, grounding signals, expected-loss
objective, seeded cases, and metric definitions. The CLI runs the matrix over all three
seeds and writes JSON plus Markdown evidence.

The winning configuration is then added to the versioned banking policy through explicit
fields with conservative defaults. The production risk engine applies the selected
adjustment only to probabilistic expected-loss pricing; it never changes the original
stakes object or hard-rule inputs. Decision explanations record both original and
effective values when an adjustment is active.

## Remaining Person 1 Work

Locally completable release work is:

- statistically honest OpenRouter anchor reporting with confusion, validity, slices,
  evidence-cluster counts, cost, latency, and failed examples;
- correction of the resumed multi-model total-budget accounting edge case;
- a combined release report joining seeded, OpenRouter, latency, fairness, security,
  and unavailable evidence;
- deterministic chaos coverage for the remaining locally reproducible cases;
- refreshed limitations, checkpoint, changelog, and run commands.

Human-reviewed forced-action efficacy, production fairness/economics traffic, semantic
entropy generation on unavailable hardware, external penetration testing, and production
edge authorization cannot be fabricated. They remain explicit external gates.

## Remaining Person 2 Work

The console implementation is complete. Locally completable work is:

- synchronize Person 2 handoff documentation with the stage-flow console and current
  test counts;
- verify the four replay journeys on desktop and mobile;
- verify live-mode degradation and evidence/unavailable states;
- record the intentionally deferred upload-egress authorization and the removed upload
  control without silently claiming either is complete.

Per-check live Lane A timing cannot be rendered until the backend contract supplies it.
Real fairness/economics panels require real ledger observations but already handle empty
states honestly.

## Verification and Commit Strategy

Use test-first changes and focused commits:

1. experiment harness and complete candidate evidence;
2. selected policy behavior and regression tests;
3. evaluation-report and budget-integrity completion;
4. console/release documentation and end-to-end verification;
5. final measured artifacts and handoff.

Every production change runs targeted tests, Ruff format/lint, strict mypy for the
declared core packages, and the complete Python suite. Console changes additionally run
unit tests, typecheck, build, and Playwright. Only explicit owned files are staged; local
OpenRouter metadata and unrelated files are never included accidentally.

## Acceptance

The work is accepted when the repository contains a reproducible comparison of all
candidate methods, a selected non-dominated configuration satisfying the safety
constraints on every seed, regenerated product evidence, green local quality gates, and
an honest final handoff that distinguishes completed code from external evidence still
requiring humans, production traffic, hardware, or deployment authority.
