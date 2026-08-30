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
supervisor, CI, dependency declaration, and their tests. Frozen core contracts remain
unchanged; `sqlite-vec` moved from the heavy ML extra to the core runtime because baseline
hybrid retrieval imports it.

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
6. Pre-provider hard blocks and agent-loop cuts publish request-scoped live decisions, and
   the UI explains request-level L5 outcomes even when no sentence or customer content exists.
7. Custom gateway ports propagate to the production console, and CI runs console Python
   projections plus desktop/mobile Playwright journeys instead of checking only the build.
8. Per-client projection queues preserve replay/live sequence order under backpressure;
   unknown projection events become diagnostics, and incomplete regret/rework measurements
   keep the entire net-value result unavailable instead of presenting zero-valued evidence.

Remaining backend/environment follow-up is narrower:

- Text-layer PDFs use the locked `pypdf` parser. OCR, encrypted files, and arbitrary layout
  fidelity remain outside the claim.
- Run the final rehearsal with the real local model; the deterministic fixture rehearsal is
  complete, but Ollama did not respond on this machine.
- Populate live fairness observations and production economics through normal traffic; an
  empty ledger correctly reports zero observations rather than sample evidence.
- Keep provider-bound use of uploaded text disabled until the deferred sensitive-data egress
  authorization is explicitly designed and approved.

## Delivered implementation

The integration branch contains the complete Person 2 console and its live backend seam:

- React 19, TypeScript, Vite, Vitest, Testing Library, and Playwright tooling.
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
  host, and frontend unit/type/build/browser CI.

Standards, specification, bug, dependency, and security reviews were run over the complete
`master...console-master-integration` diff. Verified findings received regression tests and
the focused `fix(console): preserve ordered and honest projections` commit.

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
uv run ruff format --check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q tests console/tests/python
npm --prefix console run test:e2e
```

The final integration gate on 30 August 2026 produced 67 passing frontend unit tests and 14
passing Playwright journeys across desktop 1440×900 and mobile 390×844. Visual QA found
and fixed a mobile shrink-to-fit regression; the browser suite now pins both layout and
content width to the configured viewport. The clean core/dev Python gate produced 1,023
passing tests and 2 optional-ML skips. TypeScript,
Vite build, Ruff lint/format, strict mypy, dependency locking, and the 36-test retrieval
suite also passed. The Vite build emits a non-blocking bundle-size warning, and its
development proxy can log `EPIPE` or `ECONNRESET` when Playwright closes a page.

Person 2 implementation and integration are complete. Remaining items below are release
evidence or environment follow-up, not missing console behavior.

## Known limits

- Text-layer PDFs are parsed with `pypdf`; OCR, encrypted PDFs, and arbitrary layout fidelity
  remain unsupported.
- The required live-model rehearsal remains environment-dependent because Ollama was not
  available on this machine. Deterministic replay and real-gateway fixture rehearsal pass.
- Two optional-ML unit cases are skipped in the light core/dev profile: the observer encoder
  and verifier cases require PyTorch. The complete deterministic suite otherwise runs
  without downloading model weights.
- `sqlite-vec` is a locked core dependency, so the light core/dev profile supports baseline
  hybrid retrieval without installing PyTorch or the rest of the ML extra.
- A fresh ledger has no real fairness or economics samples. The UI labels zero observations
  and does not substitute replay values.
- Provider-bound use of uploaded text remains deferred until the separate sensitive-data
  egress authorization step is approved.

## Acceptance

The replay demo must deterministically cover clean L0 pass, L2 repair, L4 hold review, and
L5 block. Live direct-SSE mode must degrade honestly when Person 1 integrations are absent.
All frontend unit, type, build, Playwright, Python lint, strict type, and Python test gates
must pass. Commits must never stage `.gitignore`, `graphify-out/`, or `tmp/`.
