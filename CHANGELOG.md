# Changelog

## 2026-08-30

- Rebuilt the operator console as the animated stage-flow decision theatre from the
  design handoff: hero, seven-stage trace machine, split-flap stage board, priced
  intervention ladder, commit gate, release and Lane C, plus the Reviews and Evidence
  workspaces in the same dark instrument-panel theme.
- Added a framework-free `TraceEngine` that owns the choreography, so every timing in
  the design is unit-tested with fake timers rather than eyeballed.
- Wired `mode=live` onto the existing gateway SSE contract (`interlock.stakes`,
  `.signal`, `.decision`, `.hold` plus the decision loss table); live lane A states that
  per-check latencies are not itemised in the contract instead of inventing them.
- Removed the superseded light-theme workspaces and the `recharts` dependency; rewrote
  the Playwright suite around the new UI (7 journeys × desktop and mobile).
- Recorded deviations D-012 (Vite instead of Next.js), D-013 (no fabricated lane A
  latencies in live mode) and D-014 (document upload dropped from the console UI).

## 2026-08-29

- Completed Person 1 console, live economics, Lane C, supervision, upload, telemetry,
  labeling, evidence-pack, and deterministic four-scenario rehearsal work.
- Regenerated evaluation artifacts against policy hash
  `banking-v3@sha256:0aeb8a4b17fe218c`.
- Recorded remaining release gates: real forced-action efficacy outcomes, live-model
  rehearsal, true observer KV reuse, and the measured latency target.
