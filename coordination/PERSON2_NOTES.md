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
  latency, ledger totals, live cost-regret/rework economics, and Lane C evidence. Replay
  mode uses explicit unavailable or zero-observation states instead of invented values.

## Integration scope

The original Person 2 branch was limited to `console/**`,
`interlock/gateway/console_ws.py`, `scripts/replay_console.py`, and this handoff. After
Person 1 completed the backend on `master`, the approved integration branch merged that
work and connected the two surfaces. The integration therefore also contains focused
changes to the gateway publishers, ledger projections, production console host,
supervisor, CI, and their tests. Frozen core contracts and dependency metadata remain
unchanged.

## Architecture

The browser posts chat requests through `/gateway/v1/chat/completions`. Vite rewrites the
`/gateway` prefix to either the replay gateway on port 8099 or the live gateway on port
8080. The production console service serves the compiled React application and proxies
both HTTP streams and `/console/ws` to the gateway on the same origin. A custom streaming
parser consumes OpenAI `data:` chunks and the four named Contract 3 events:
`interlock.stakes`, `interlock.signal`, `interlock.decision`, and `interlock.hold`.

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
- `GET /console/lanec` returns the current fairness/e-value projection.
- `GET /console/artifacts/{name:path}` serves only the explicit JSON allowlist.

The artefact allowlist is `action_latency.json`, `calibration/report.json`,
`calibration/lambda.json`, `eval/report.json`, `eval/report-guaranteed.json`,
`eval/sensitivity.json`, and `probes/curve.json`.

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
- Live regret, rework, running net value, confidence intervals, and Lane C observations are
  read from the ledger. Replay mode exposes deterministic zero-observation/unavailable
  projections; the UI never fabricates evidence.

## Person 1 integration status

The merged integration now completes the browser/backend seam:

1. The gateway owns one process-lifetime `ConsoleHub`, mounts the projection router, and
   publishes stakes, signals, decisions, and holds with request IDs.
2. Tool and response hold tokens appear only in the initiating SSE stream. Recursive hub
   redaction keeps them out of WebSocket/recent projections.
3. The production console service hosts `console/dist`, proxies gateway HTTP/SSE and
   WebSocket traffic on one origin, and is supervised by `scripts/up.ps1`.
4. Live ledger projections supply economics and Lane C data. The semantic-cache path also
   records rework attribution.
5. The upload endpoint and UI connect Scene 2 with explicitly untrusted retrieved text.

Remaining backend/environment follow-up is narrower:

- Replace the conservative printable-text PDF extractor with a parser-backed worker before
  claiming arbitrary-PDF support.
- Run the final rehearsal with the real local model; the deterministic fixture rehearsal is
  complete, but Ollama did not respond on this machine.
- Decide whether `sqlite-vec` belongs in the core/dev installation. It is currently declared
  only by the `ml` extra even though baseline retrieval tests exercise it.
- Populate live fairness observations and production economics through normal traffic; an
  empty ledger correctly reports zero observations rather than sample evidence.

## Delivered implementation

The integration branch contains the complete Person 2 console and its live backend seam:

- React 19, TypeScript, Vite, Vitest, Testing Library, Recharts, and Playwright tooling.
- Runtime-validated direct SSE and projection events, sentence-keyed state, partial-output
  preservation, eventually consistent decision-detail hydration, reconnect cursors, stream
  reset handling, duplicate suppression, and replay-gap buffering.
- Responsive Live, Reviews, and Evidence workspaces with keyboard focus behavior, reduced
  motion-safe charts, source provenance, action totals, confidence intervals, live
  economics, Lane C e-values, and honest empty/unavailable states.
- Request-scoped replay decisions, holds, and resume tokens. The L4 response remains
  withheld until review; L5 emits no customer content. Expected-loss tables agree with the
  declared winner, runner-up, and margin.
- Recursive server-side token redaction, an allowlisted JSON artefact surface, no browser
  storage of secrets, no unsafe HTML injection, and no WebSocket mutation commands.
- Text/PDF upload, explicit untrusted-context attachment, a compiled same-origin production
  host, and frontend unit/type/build CI.

Final standards, specification, bug, and security reviews are run over the complete
`master...console-master-integration` diff before the branch is proposed for merge. Any
verified finding receives a regression test and a focused fix commit.

## Runbook

Install the frontend once:

```bash
npm --prefix console ci
```

Build and run the production same-origin console:

```bash
npm --prefix console run build
uv run uvicorn interlock.console.app:app --host 127.0.0.1 --port 5173
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
uv run pytest -q --ignore=tests/chaos -m "not slow" tests console/tests/python
npm --prefix console run test:e2e
```

The latest targeted gate on 29 August 2026 produced 48 passing frontend unit tests and 10
passing Playwright journeys across desktop 1440×900 and mobile 390×844. The pre-documentation
Python gate produced 898 passing non-slow tests with 7 slow tests deselected. TypeScript,
Vite build, Ruff lint/format, and strict mypy also passed. Exact final counts are refreshed
after the complete review gate. The Vite build emits a non-blocking bundle-size warning,
and its development proxy can log `EPIPE` or `ECONNRESET` when Playwright closes a page.

## Known limits

- Arbitrary PDFs need a parser-backed extraction worker; the current endpoint intentionally
  uses a conservative dependency-free extractor.
- The required live-model rehearsal remains environment-dependent because Ollama was not
  available on this machine. Deterministic replay and real-gateway fixture rehearsal pass.
- Seven slow verifier/probe tests need the cached or downloadable
  `cross-encoder/nli-distilroberta-base` model. They cannot run in a network-isolated
  environment without that model; the non-slow suite remains the CI gate.
- `sqlite-vec` is still declared only in the `ml` extra while baseline retrieval tests use
  it. The integration environment includes version 0.1.9 without changing dependency files.
- A fresh ledger has no real fairness or economics samples. The UI labels zero observations
  and does not substitute replay values.

## Acceptance

The replay demo must deterministically cover clean L0 pass, L2 repair, L4 hold review, and
L5 block. Live direct-SSE mode must degrade honestly when Person 1 integrations are absent.
All frontend unit, type, build, Playwright, Python lint, strict type, and Python test gates
must pass. Commits must never stage `.gitignore`, `graphify-out/`, or `tmp/`.
