# Changelog

## 2026-08-30

- Resolved F-019 with a calibrated 1% minimum defect-probability floor plus the reviewed
  50% minimum relative action-gain rule; hard rules, conformal filtering, and unavailable
  actions retain precedence.
- Fixed context-aware question-drift detection and prevented induced numeric corruptions
  from accidentally remaining supported elsewhere in their source passage.
- Compared 33 decision strategies, including the exact production composite, across
  three 200-case seeds and 300 labelled anchors.
  The selected rule reduced measured false interventions from a 72.38% seeded baseline
  to 0/140 on every seed while retaining 100% catch and 0% empirical ungrounded escapes.
- Corrected the false-intervention denominator to the 140 explicitly clean cases instead
  of including fairness and mandatory loop-break probes.
- Recalibrated on 10,000 document-disjoint examples: AUROC 1.0000, ECE 0.0046, and a
  37% conformal threshold at a 9.22% intervention rate.
- Regenerated evaluation evidence against policy hash
  `banking-v3@sha256:4b63a6b7b5416683`; verification cost and the false-intervention
  point target pass, while net spend remains below its target.
- Fixed explicit streaming regenerations incorrectly taking the semantic-cache path and
  bypassing live rework attribution.

## 2026-08-29

- Completed Person 1 console, live economics, Lane C, supervision, upload, telemetry,
  labeling, evidence-pack, and deterministic four-scenario rehearsal work.
- Regenerated evaluation artifacts against policy hash
  `banking-v3@sha256:0aeb8a4b17fe218c`.
- Recorded remaining release gates: real forced-action efficacy outcomes, live-model
  rehearsal, true observer KV reuse, and the measured latency target.
