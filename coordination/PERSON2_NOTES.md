# Person 2 Console Design and Handoff

## Goal

Person 2 owns the demo-facing console: a React/TypeScript application that explains
decisions already made by Interlock. It does not tune thresholds or bypass the audited
hold-resolution routes.

The console has three workspaces:

- **Live** streams a bank-support response beside stakes, calibrated signals, the L0-L5
  decision rail, the complete expected-loss table, and counterfactual versus shipped text.
- **Reviews** presents durable response and tool-call holds with evidence and explicit
  approve/reject actions.
- **Evidence** presents calibration, evaluation confidence intervals, measured action
  latency, ledger totals, and honest unavailable states for missing Lane C economics.

## Ownership

Person 2 may change only:

- `console/**`
- `interlock/gateway/console_ws.py`
- `scripts/replay_console.py`
- `coordination/PERSON2_NOTES.md`

`interlock/gateway/app.py`, frozen core contracts, ledger internals, dependency metadata,
and native supervision remain Person 1 responsibilities.

## Architecture

The browser posts chat requests through `/gateway/v1/chat/completions`. Vite rewrites the
`/gateway` prefix to either the replay gateway on port 8099 or the live gateway on port
8080. A custom streaming parser consumes OpenAI `data:` chunks and the four named Contract
3 events: `interlock.stakes`, `interlock.signal`, `interlock.decision`, and
`interlock.hold`.

Immediate chat and event state lives in a pure reducer keyed by request and sentence.
Read-only REST and WebSocket projections under `/console` provide recent history, complete
decision records, durable holds, committed evidence artefacts, and ledger summaries. The
replay gateway implements this projection contract completely; live mode exposes what the
current backend can prove and marks unavailable integrations explicitly.

## Projection contract

Every WebSocket and recent-history event has this envelope:

```json
{
  "stream_id": "process epoch",
  "seq": 1,
  "event": "interlock.decision",
  "data": {},
  "ts": 0.0,
  "request_id": "req_optional",
  "replayed": false
}
```

`stream_id` changes after a server restart. `seq` is monotonic within one stream. Replayed
events set `replayed` to true without changing their sequence.

- `WS /console/ws` is push-only and replays the current bounded buffer on connection.
- `GET /console/recent?after=<seq>&stream_id=<id>` returns cursor-safe non-secret events.
- `GET /console/status` reports live/replay source and capability availability.
- `GET /console/decisions/{decision_id}` returns the six loss rows and persisted rationale.
- `GET /console/holds` returns enriched pending holds without resume tokens.
- `GET /console/ledger/summary` returns spend, traffic, action, and overhead summaries.
- `GET /console/artifacts/{name:path}` serves only the explicit JSON allowlist.

The artefact allowlist is `action_latency.json`, `calibration/report.json`,
`calibration/lambda.json`, `eval/report.json`, and `eval/report-guaranteed.json`.

## Secret and safety rules

An optional hold `resume_token` may appear only on the initiating browser's named SSE
event. The browser captures it into an in-memory map, removes it before normal event
handling, never renders it, never logs it, and never stores it in browser storage, URLs,
the WebSocket buffer, recent history, or the review queue. Approval sends the token to the
existing audited REST endpoint. Rejection needs no token. Tokens are deleted on resolution,
stream teardown, and reload.

The console never uses `dangerouslySetInnerHTML`. Artefact paths are allowlisted and
resolved below the committed artefact root. Unknown or malformed events become diagnostics
rather than crashes. Network failure preserves partial text and never resubmits a request.

## Evidence rules

- Confidence intervals render as ranges or chart bands rather than bare point estimates.
- The certified ungrounded-escape result is always displayed beside its 100% intervention
  rate.
- Replay data is visibly labelled `REPLAY`.
- Regret, rework, running net, and Lane C economics remain unavailable until real artefacts
  exist; the UI never fabricates them.

## Person 1 integration handoff

Person 1 must:

1. Publish stakes, calibrated signals, decisions, and holds to `ConsoleHub`, including the
   request ID in the envelope.
2. Add an optional resume token only to the initiating hold SSE event, including response
   holds; never publish that token to console projections.
3. Add production static/proxy serving and native console supervision.
4. Make `sqlite-vec` part of the non-heavy development/runtime installation, or align the
   retrieval tests with the declared dependency extra. Person 2 installs `sqlite-vec==0.1.9`
   locally only so the existing 653-test baseline can run.
5. Supply real Lane C economics, regret, rework, net-value, and confidence-interval data.

## Acceptance

The replay demo must deterministically cover clean L0 pass, L2 repair, L4 hold review, and
L5 block. Live direct-SSE mode must degrade honestly when Person 1 integrations are absent.
All frontend unit, type, build, Playwright, Python lint, strict type, and Python test gates
must pass. Commits must never stage `.gitignore`, `graphify-out/`, or `tmp/`.
