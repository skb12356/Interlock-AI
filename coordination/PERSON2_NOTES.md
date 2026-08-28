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

1. Create one process-lifetime `ConsoleHub` at `app.state.console_hub`, mount the router
   from `interlock.gateway.console_ws`, and publish stakes, calibrated signals, decisions,
   and holds with the request ID in every envelope. Set
   `app.state.console_publishers_integrated = True` only after all four publishers are
   wired; until then `/console/status` deliberately reports recent live events unavailable.
2. Add an optional resume token only to the initiating hold SSE event, including response
   holds; never publish that token to console projections.
3. Add production static/proxy serving and native console supervision.
4. Make `sqlite-vec` part of the non-heavy development/runtime installation, or align the
   retrieval tests with the declared dependency extra. Person 2 installs `sqlite-vec==0.1.9`
   locally only so the existing 653-test baseline can run.
5. Supply real Lane C economics, regret, rework, net-value, and confidence-interval data.

## Delivered implementation

The `console` branch now contains the complete Person 2 replay implementation:

- React 19, TypeScript, Vite, Vitest, Testing Library, Recharts, and Playwright tooling.
- Runtime-validated direct SSE and projection events, sentence-keyed state, partial-output
  preservation, eventually consistent decision-detail hydration, reconnect cursors, stream
  reset handling, duplicate suppression, and replay-gap buffering.
- Responsive Live, Reviews, and Evidence workspaces with keyboard focus behavior, reduced
  motion-safe charts, source provenance, action totals, confidence intervals, and explicit
  unavailable economics.
- Request-scoped replay decisions, holds, and resume tokens. The L4 response remains
  withheld until review; L5 emits no customer content. Expected-loss tables agree with the
  declared winner, runner-up, and margin.
- Recursive server-side token redaction, an allowlisted JSON artefact surface, no browser
  storage of secrets, no unsafe HTML injection, and no WebSocket mutation commands.

The standards, specification, independent code, bug, and security reviews were completed
over `002e8600cb8252f03d448610a8469228703648b5..HEAD`. All verified findings were covered by
regression tests and resolved in the focused review-fix commit. The security review found no
remaining high-confidence vulnerability in the owned Person 2 surface.

## Runbook

Install the frontend once:

```bash
npm --prefix console ci
```

Start the deterministic replay server and UI in separate terminals:

```bash
uv run python scripts/replay_console.py --port 8099
npm --prefix console run dev -- --host 127.0.0.1
```

For direct live-gateway development, point Vite at port 8080:

```bash
CONSOLE_BACKEND_URL=http://127.0.0.1:8080 npm --prefix console run dev -- --host 127.0.0.1
```

Run the complete quality gate:

```bash
npm --prefix console run test:unit -- --run
npm --prefix console run typecheck
npm --prefix console run build
uv run ruff check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
npm --prefix console run test:e2e
```

The final local gate on 26 August 2026 produced 43 passing frontend unit tests, 665 passing
Python tests, and 8 passing Playwright journeys: the four replay scenarios at desktop
1440×900 and mobile 390×844. TypeScript, Vite build, Ruff, and strict mypy also passed. The
production npm dependency audit reported zero vulnerabilities. The Vite build emits only a
non-blocking bundle-size warning, and its development WebSocket proxy can log `EPIPE` or
`ECONNRESET` when Playwright closes a page.

## Known unavailable data and integration

- Live `ConsoleHub` publishers, production router mounting, static/proxy serving, and native
  console supervision remain Person 1 work. Direct SSE still operates, while the status
  projection labels missing history honestly.
- A live initiating-stream resume token, especially for response holds, remains Person 1
  gateway integration. A browser without that token can reject but cannot approve.
- Lane C regret, rework, running net value, economics, and their confidence intervals do not
  exist and remain visibly unavailable.
- `sqlite-vec==0.1.9` is installed only in the local environment. The repository dependency
  declaration still does not provide it along the baseline retrieval-test path.

## Acceptance

The replay demo must deterministically cover clean L0 pass, L2 repair, L4 hold review, and
L5 block. Live direct-SSE mode must degrade honestly when Person 1 integrations are absent.
All frontend unit, type, build, Playwright, Python lint, strict type, and Python test gates
must pass. Commits must never stage `.gitignore`, `graphify-out/`, or `tmp/`.
