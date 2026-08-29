# Interlock Deploy Runbook

## CPU-Only Judge Profile

Prerequisites:

- Python 3.12 and `uv`
- Local OpenAI-compatible upstream, defaulting to Ollama at `http://127.0.0.1:11434/v1`
- A built corpus index at `data/corpus.db`; rebuild with `uv run python scripts/build_index.py` if missing

Start the stack:

```powershell
.\scripts\up.ps1 -MockObserver -TimeoutSeconds 120
```

Services:

- Gateway: `http://127.0.0.1:8080`
- Observer: `http://127.0.0.1:8081`
- Console: `http://127.0.0.1:5173`

Smoke checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:5173/health
uv run python scripts/rehearse_gateway.py
```

Load pass (run against the supervised stack; this writes measured client latency and
the gateway's `/admin/latency` histogram to an evidence artifact):

```powershell
uv run python scripts/load_pass.py --duration-seconds 300 --concurrency 20
```

Run the local security/privacy sweep:

```powershell
uv run python scripts/security_sweep.py
```

## Measured Efficacy Import

The efficacy matrix requires human-reviewed outcomes after forcing an action. A
pre-action defect label is not an efficacy observation. Create one JSONL row per
forced action and reviewed result:

```json
{"item_id":"manual-anchor-001","action":"L2_repair","defect":"ungrounded","removed":true}
```

The importer validates IDs against the 300-item manual anchor set, rejects duplicate
cells and invalid booleans, and writes Wilson intervals only for observed cells:

```powershell
uv run python scripts/measure_efficacy.py post_action_outcomes.jsonl
```

Review `artifacts/eval/efficacy.json` before updating the versioned policy. An empty
or missing outcomes file is intentional until forced-action review has been performed.

Stop:

```powershell
.\scripts\down.ps1
```

## Deterministic Rehearsal Profile

Use this when Ollama or the strong tier is unavailable. It still runs the real gateway
and console; only the upstream model stream is a deterministic local fixture.

```powershell
uv run python scripts/replay_console.py --port 8099
$env:INTERLOCK_OLLAMA_BASE_URL = "http://127.0.0.1:8099/v1"
$env:INTERLOCK_DB_PATH = "data/rehearsal.db"
.\scripts\up.ps1 -RiskEngine stub -MockObserver -TimeoutSeconds 120
uv run python scripts/rehearse_gateway.py --strict-actions
```

The rehearsal writes `artifacts/rehearsal/gateway_rehearsal.json` with raw resume tokens
redacted.

## Single-VM Production Shape

Run the gateway and console as supervised processes behind a TLS reverse proxy:

- `uv run uvicorn interlock.gateway.app:app --host 127.0.0.1 --port 8080`
- `uv run uvicorn interlock.console.app:app --host 127.0.0.1 --port 5173`
- observer on `127.0.0.1:8081`, using `interlock.observer.server:app` in production or `interlock.observer.mock_server:app` only with `-MockObserver` for deterministic rehearsals
- Caddy/nginx routes `/v1/*`, `/admin/*`, `/console/*` to `:8080`; route `/` and `/api/artifacts/*` to `:5173`

Environment:

- `INTERLOCK_RISK_ENGINE=real` for serving traffic
- `INTERLOCK_OLLAMA_BASE_URL` or provider-specific API keys for upstreams
- `INTERLOCK_OBSERVER_URL=http://127.0.0.1:8081`
- `INTERLOCK_DB_PATH=/var/lib/interlock/interlock.db`
- `INTERLOCK_STORE_PROMPTS=0` unless the deployment has explicit approval to store prompts

Health and rollback:

- Gateway health: `/health`
- Console health: `/health` on the console port
- Stop or roll back by replacing the supervised process command and restarting; the ledger is append-only and should not be reset during rollback
