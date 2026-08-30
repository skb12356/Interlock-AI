# Interlock — operator and developer reference

The [README](../README.md) is the short version: what Interlock is, how it decides, and what
it measured. This file keeps the operational detail that used to live there — dependencies,
every way to start the stack, the API surface, configuration, evaluation commands, the test
gate, the security model and the deployment shape.

For the architecture and the maths, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Dependencies

### Required tools

| Tool | Version/role |
| --- | --- |
| Python | **3.12** (`pyproject.toml` intentionally excludes 3.13) |
| [uv](https://docs.astral.sh/uv/) | Python environment, lockfile and command runner |
| Node.js | **22** recommended; Vite 7 requires Node 20.19+ or 22.12+ |
| npm | Installs the locked console dependencies |
| PowerShell (`pwsh`) | Native three-service supervisor used by `make up` / `scripts/up.ps1` |
| Ollama | Optional for live local generation; not needed for deterministic replay |

Docker is deliberately not part of this build. The native supervisor replaces Compose and
starts three localhost-bound processes with health checks.

### Python dependency tiers

- **Core runtime:** FastAPI/Uvicorn, HTTPX, Pydantic, PyYAML, OpenTelemetry, NumPy,
  scikit-learn, DuckDB, pysbd, pypdf, pyahocorasick, and sqlite-vec.
- **Development:** pytest, pytest-asyncio, Hypothesis, respx, Ruff, mypy, pre-commit, the
  OpenAI SDK, and HTTPX test support.
- **Optional ML extra:** PyTorch, Transformers, Sentence Transformers, ONNX Runtime,
  Optimum, Presidio, and Matplotlib.

### Console stack

- React 19 and React DOM
- TypeScript 5.9 and Vite 7
- Vitest, Testing Library, jsdom, and Playwright

## Getting started

### 1. Clone and configure

```bash
git clone https://github.com/skb12356/Interlock-AI.git
cd Interlock-AI
cp .env.example .env
```

The defaults use local Ollama and hash prompts before ledger storage. Do not commit `.env`,
provider keys, tenant canaries, production ledgers, or hold resume tokens.

### 2. Install dependencies

For the complete local profile:

```bash
uv sync --group dev --extra ml
npm --prefix console ci
```

For the lighter deterministic replay and standard test profile:

```bash
uv sync --group dev
npm --prefix console ci
```

### 3A. Run the deterministic console demo

This profile needs no model, provider key, corpus index, or observer weights. Run the two
commands in separate terminals:

```bash
uv run python scripts/replay_console.py --port 8099
```

```bash
npm --prefix console run dev -- --host 127.0.0.1
```

Open <http://127.0.0.1:5173>. The replay server provides four repeatable journeys:

- `clean` — L0 pass
- `scene1` — L2 sentence repair
- `held` — L4 durable review
- `blocked` — L5 with no assistant content

### 3B. Run the live local stack

Install and start Ollama, then make the two configured model tiers available:

```bash
ollama pull qwen3:4b
ollama pull qwen3:8b
uv run python scripts/build_index.py
```

Start the gateway, mock observer contract, and compiled console:

```powershell
./scripts/up.ps1 -MockObserver -TimeoutSeconds 120
```

On a shell with PowerShell available, `make up` invokes the same supervisor. It builds the
console on first start and exposes:

| Service | URL |
| --- | --- |
| Gateway | <http://127.0.0.1:8080> |
| Observer | <http://127.0.0.1:8081> |
| Console | <http://127.0.0.1:5173> |

Check health and stop the stack:

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:5173/health
pwsh -NoProfile -File scripts/down.ps1
```

Logs are written beneath `logs/` by service.

### 3C. Start services manually

This is useful when debugging one process at a time:

```bash
# Terminal 1: observer contract
uv run uvicorn interlock.observer.mock_server:app --host 127.0.0.1 --port 8081

# Terminal 2: gateway
uv run uvicorn interlock.gateway.app:app --host 127.0.0.1 --port 8080

# Terminal 3: production same-origin console
npm --prefix console run build
INTERLOCK_GATEWAY_URL=http://127.0.0.1:8080 \
  uv run uvicorn interlock.console.app:app --host 127.0.0.1 --port 5173
```

For Vite live-gateway development instead of the compiled console host:

```bash
CONSOLE_BACKEND_URL=http://127.0.0.1:8080 \
  npm --prefix console run dev -- --host 127.0.0.1
```

## Use the API

Point any OpenAI-compatible client at the gateway:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="local")
response = client.chat.completions.create(
    model="interlock-auto",
    messages=[
        {"role": "user", "content": "Can I prepay my floating-rate home loan?"}
    ],
)
print(response.choices[0].message.content)
```

The gateway supports standard OpenAI `data:` chunks plus named SSE events for stakes,
signals, decisions, and holds. Clients that need those events can read the raw stream;
ordinary OpenAI clients continue to consume assistant content.

Key routes:

| Route | Description |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible chat completion, streaming or buffered |
| `GET /v1/models` | Available Interlock/provider model view |
| `POST /v1/uploads` | Text/PDF extraction as explicitly untrusted fragments |
| `GET /v1/holds` | Pending durable holds |
| `POST /v1/holds/{id}/approve` | Resolve a hold; approval requires its resume token |
| `POST /v1/holds/{id}/reject` | Reject a hold without requiring the release secret |
| `GET /health` | Provider, observer, policy, calibration and retrieval health |
| `GET /admin/latency` | Gateway latency histogram |
| `GET /admin/governor` | Current degradation/governor state |
| `GET /admin/economics` | Ledger economics projection with provenance |
| `GET /admin/lanec` | Lane C evidence projection |
| `GET /admin/evidence/{request_id}.zip` | Request evidence pack |

> [!WARNING]
> Uploaded fragments participate in Interlock's risk/provenance analysis. Sending their
> extracted text onward as provider prompt context is intentionally deferred pending an
> explicit sensitive-data egress decision. Do not assume an uploaded document currently
> grounds the provider's generated answer.

## Configuration

Copy [`.env.example`](../.env.example) and adjust only what the deployment needs.

| Variable | Default | Purpose |
| --- | --- | --- |
| `INTERLOCK_OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` | Local OpenAI-compatible provider |
| `INTERLOCK_CHEAP_PROVIDER` / `INTERLOCK_CHEAP_MODEL` | `ollama` / `qwen3:4b` | Low-stakes tier |
| `INTERLOCK_STRONG_PROVIDER` / `INTERLOCK_STRONG_MODEL` | `ollama` / `qwen3:8b` | High-stakes tier |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | unset | Optional hosted providers |
| `INTERLOCK_OBSERVER_URL` | `http://127.0.0.1:8081` | Lane B observer service |
| `INTERLOCK_LANE_A_DEADLINE_MS` | `120` | Pre-flight detector deadline |
| `INTERLOCK_OBSERVE_DEADLINE_MS` | `800` | Per-sentence observer budget |
| `INTERLOCK_SENTENCE_WATCHDOG_S` | `8` | Flush stalled partial sentences |
| `INTERLOCK_SHADOW_SAMPLE_RATE` | `0` | Explicit opt-in fraction for approved cheap-tier shadow replay |
| `INTERLOCK_DB_PATH` | `data/interlock.db` | Append-oriented ledger |
| `INTERLOCK_CORPUS_INDEX_PATH` | `data/corpus.db` | Read-only retrieval index |
| `INTERLOCK_POLICY_PATH` | `policies/banking.yaml` | Versioned governance policy |
| `INTERLOCK_RISK_ENGINE` | `real` | `real` for traffic; `stub` only for tests/rehearsal |
| `INTERLOCK_CONFORMAL_FILTER` | `0` | Optional guaranteed mode; costly at current threshold |
| `INTERLOCK_VERIFIER` | `0` | Enable the heavier claim verifier |
| `INTERLOCK_STORE_PROMPTS` | `0` | Store raw prompts instead of hashes |
| `INTERLOCK_GATEWAY_URL` | `http://127.0.0.1:8080` | Console host's gateway upstream |
| `INTERLOCK_CONSOLE_ORIGINS` | unset | Comma-separated browser origins for an intentional direct-gateway console |
| `CONSOLE_BACKEND_URL` | `http://127.0.0.1:8099` | Vite development proxy target |

## Evaluation and evidence

The repository keeps claims attached to generated artifacts rather than README snapshots:

```bash
uv run python scripts/calibrate.py
uv run python scripts/eval.py --json artifacts/eval/report.json
uv run python scripts/eval.py --conformal-filter \
  --json artifacts/eval/report-guaranteed.json
uv run python scripts/sensitivity.py
uv run python scripts/measure_action_latency.py
uv run python scripts/compare_policy_methods.py
uv run python scripts/report_manual_anchors.py
uv run python scripts/build_product_report.py
```

The console only serves an explicit artifact allowlist. Confidence intervals remain next to
their estimates, replay evidence is labelled, and an empty ledger reports unavailable or
zero-observation state rather than fabricated economics.

Two results must always be read together: the conformal artifact certifies zero ungrounded
escapes at the selected threshold, and that threshold checks 100% of traffic. The guarantee
and its operational price belong in the same sentence.

The current `banking-v4` policy was selected from 216 bounded policy candidates over
three immutable seeds. It preserves the reference Hold/Repair/Pass behavior and keeps
pre-action catch at 100% with zero empirical grounding escapes. Clean traffic in the
generated ₹10,000+ stakes bucket is still intervened on, which is the impact-model
question recorded as finding F-019.

The separate GPT-4o Mini audit reports an 8.5% false-positive rate on 200 generated clean
anchors and a 20% grounding-escape rate on 100 generated defective anchors. Those are
offline judge-classification results, not product action rates, and the anchors are
explicitly unreviewed rather than human labels.

See [`artifacts/eval/report.json`](../artifacts/eval/report.json),
[`artifacts/eval/policy_comparison.json`](../artifacts/eval/policy_comparison.json),
[`artifacts/eval/manual_anchor_report.json`](../artifacts/eval/manual_anchor_report.json),
[`artifacts/eval/product_report.md`](../artifacts/eval/product_report.md),
[`artifacts/calibration/lambda.json`](../artifacts/calibration/lambda.json), and
[`docs/LIMITATIONS.md`](../docs/LIMITATIONS.md) for the committed evidence and caveats.

## Testing and quality gates

The implementation uses contract tests for frozen seams, property tests for the sentence
commit gate, unit/HTTP tests for the Python services, Vitest for the reducer/UI, and
Playwright for all four replay journeys at desktop and mobile viewports.

Run the complete non-network gate:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools
uv run pytest -q --ignore=tests/chaos -m "not slow" tests console/tests/python

npm --prefix console run test:unit -- --run
npm --prefix console run typecheck
npm --prefix console run build
npm --prefix console run test:e2e
```

Slow verifier/probe tests require the cached or downloadable
`cross-encoder/nli-distilroberta-base` weights. The deterministic replay tests do not.

## Security and privacy model

- The gateway, observer, and console bind to localhost in the supplied runbook. Put TLS,
  authentication, tenant authorization, rate limits, and request-size enforcement at the
  deployment edge before exposing them beyond a trusted host.
- Prompts are hashed by default. Raw storage requires `INTERLOCK_STORE_PROMPTS=1` and an
  explicit deployment decision.
- Retrieved/uploaded content is conservatively labelled untrusted and flows through the
  provenance lattice.
- Hold resume tokens appear only in the initiating browser's SSE event, live only in an
  in-memory vault, and are recursively removed from console projections and diagnostics.
- Semantic-cache hits require the same canonical full prompt/options, tenant, and trusted
  role scope as well as the question, retrieval context, stakes ceiling, clean prior
  decision, and policy version.
- Browser WebSockets enforce same-origin or an explicit origin allowlist. Hold mutations
  require JSON, preventing cross-site HTML forms while preserving tokenless rejection.
- Shadow replay is disabled by default because enabling it may create a second provider
  data-egress boundary; opt in only after provider/region authorization.
- Approval needs the secret token; rejection remains possible without it so losing a secret
  can never force execution.
- Evidence artifact paths are allowlisted and resolved beneath the artifact root.
- The React console does not render raw HTML and never stores secrets in browser storage,
  URLs, logs, or rendered state.
- Policy thresholds and action prices are reviewable files, not console controls.

## Deployment shape

The supported production shape is a single VM/process group behind a TLS reverse proxy:

```text
Browser ──TLS──> reverse proxy ──> console host :5173
                                   ├── static React assets
                                   ├── /gateway/* ──> gateway :8080
                                   └── /console/* ──> gateway :8080
Gateway :8080 ──> observer :8081
              ├──> configured model providers
              ├──> read-only corpus index
              └──> append-oriented ledger
```

Keep the browser on the console origin; do not expose a second browser-facing gateway
origin. The console proxy preserves streaming responses and WebSocket upgrades. Refer to
[`docs/05_deploy_runbook.md`](../docs/05_deploy_runbook.md) for rehearsal, health, load, and
rollback procedures.
