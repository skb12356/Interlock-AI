# Changelog

## 2026-08-30

- Resolved F-019 with a reviewed 50% minimum relative action-gain rule; hard rules and
  unavailable-action constraints retain precedence.
- Compared 30 decision strategies across three 200-case seeds and 300 labelled anchors.
  The selected rule reduced mean seeded false interventions from 88.96% to 68.79%
  without reducing seeded catch or increasing empirical ungrounded escapes.
- Regenerated evaluation evidence against policy hash
  `banking-v3@sha256:218a79e602a71ac3` and recorded the remaining 2% target miss.
- Fixed explicit streaming regenerations incorrectly taking the semantic-cache path and
  bypassing live rework attribution.

## 2026-08-29

- Completed Person 1 console, live economics, Lane C, supervision, upload, telemetry,
  labeling, evidence-pack, and deterministic four-scenario rehearsal work.
- Regenerated evaluation artifacts against policy hash
  `banking-v3@sha256:0aeb8a4b17fe218c`.
- Recorded remaining release gates: real forced-action efficacy outcomes, live-model
  rehearsal, true observer KV reuse, and the measured latency target.
