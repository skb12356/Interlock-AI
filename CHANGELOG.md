# Changelog

## 2026-08-30 (console polish)

- Added an About workspace: what Interlock does in plain language, what happens to one
  question, what it can do about a bad answer, what the published numbers mean, and the
  research each mechanism comes from, cited.
- Fixed scrolling in the Reviews, Evidence and About workspaces — the shell no longer
  clips them.
- Hold cards now show a middle-elided id with the full value on hover, a copy control
  (with a selection-copy fallback), and a link to the chat session the hold came from
  when this browser has it.

## 2026-08-30 (later)

- Rebuilt the console front door as a chat workspace: sessions in a sidebar, one real
  gateway request per prompt, and the seven Interlock stages reported inline beside the
  answer with a **see it live** link into the full stage trace.
- Removed demo mode entirely — the mode switch, the seeded scene fixtures and the
  scripted choreography are gone, and the engine is live-only.
- Fixed the elapsed clock: it stops when the stream ends and reports the time the
  request took, instead of counting on afterwards.
- Removed the pause and step controls from the trace footer, and replaced them with
  back-to-chat and new-session actions.
- Redesigned the navbar around a segmented Chat / Trace / Reviews / Evidence control
  with the gateway health read-out and no mode switch.
- The decision loss table is now awaited before a trace is stored, so a reopened trace
  shows its priced ladder rather than an empty stage 04.

## 2026-08-30

- Compared 216 policy adjustments over three immutable seeds, added catch/escape and
  reference-action governance gates, and shipped `banking-v4` with a 0.015 probability
  deadband plus 20× false-intervention nuisance pricing. Worst-seed false/disruptive
  intervention fell from 92.36% to 64.97% while catch remained 100% and empirical
  grounding escapes remained zero.
- Added a request-deduplicated OpenRouter report over 300 generated/unreviewed anchors and
  fixed shared resume budgeting across multiple judge models. GPT-4o Mini produced 8.5%
  clean-anchor false positives and 20% grounding escapes for USD 0.02011 across 60 calls.
- Added a combined failure-preserving submission report. It keeps load latency and false
  intervention as misses, conformal/fairness as inconclusive, production economics as
  unavailable, and penetration testing as not run.
- Fixed the mobile console's 548 CSS px intrinsic width. The 390×844 project now renders
  at the true viewport with no shrink-to-fit strip, pinned by Playwright on both projects.

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
