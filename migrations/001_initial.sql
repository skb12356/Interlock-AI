-- 001_initial -- the system of record (Implementation02 §3).
--
-- SQLite in WAL mode. DuckDB attaches this same file READ_ONLY for the console's
-- analytics, so a slow ledger query can never block a writer (ADR-004).
--
-- Everything here is an append-only fact table. Nothing is updated in place except the
-- lifecycle columns on `holds` and `tool_calls`, which genuinely have a lifecycle: a
-- pending hold becomes approved or rejected, and that transition is the product.
--
-- Idempotent: every statement is IF NOT EXISTS, and migrations are applied at boot.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version     TEXT PRIMARY KEY,
  applied_ts  REAL NOT NULL
);

-- One row per proxied request. `overhead_ms` is MEASURED, not estimated -- it is the
-- number the p95 latency claim is made from, so it must never be computed by hand.
CREATE TABLE IF NOT EXISTS requests (
  request_id           TEXT PRIMARY KEY,
  trace_id             TEXT NOT NULL,
  tenant_id            TEXT NOT NULL,
  session_id           TEXT,
  ts                   REAL NOT NULL,
  model_requested      TEXT,
  model_served         TEXT,
  route_reason         TEXT,           -- 'stakes_high' | 'stakes_low' | 'preflight_flag' | 'cache_hit' | 'pinned'
  stakes_id            TEXT,           -- ties the router and the risk engine to ONE estimate
  stakes_impact_inr    REAL,
  stakes_reversibility TEXT,
  stakes_domain        TEXT,
  stakes_confidence    REAL,
  gate_mode            TEXT,           -- 'buffered' | 'unbuffered'
  prompt_tokens        INTEGER DEFAULT 0,
  completion_tokens    INTEGER DEFAULT 0,
  upstream_ms          INTEGER DEFAULT 0,
  overhead_ms          REAL DEFAULT 0, -- our added latency, measured
  lane_a_ms            REAL DEFAULT 0,
  ttft_ms              REAL DEFAULT 0,
  cache_hit            INTEGER DEFAULT 0,
  degraded             INTEGER DEFAULT 0,
  dropped_detectors    TEXT,           -- JSON array; never silently empty
  -- Prompts are stored HASHED by default. Full text only when
  -- INTERLOCK_STORE_PROMPTS=1. Five lines that answer the enterprise-privacy question.
  prompt_hash          TEXT,
  prompt_text          TEXT,
  finish_reason        TEXT
);

-- One row per (request, sentence, signal). `raw` is what the detector emitted; `prob`
-- is that value after isotonic calibration. Both are kept: a raw score with no `prob`
-- is exactly how the console shows "we did not calibrate this", and `calib_version`
-- is what lets a past decision be re-priced when calibration changes.
CREATE TABLE IF NOT EXISTS signals (
  request_id    TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  sentence_idx  INTEGER,
  name          TEXT NOT NULL,
  raw           REAL,
  prob          REAL,                  -- NULL means uncalibrated or dropped
  calib_version TEXT,
  latency_ms    REAL DEFAULT 0,
  span_start    INTEGER,
  span_end      INTEGER,
  PRIMARY KEY (request_id, seq, name)
);

-- The full six-row expected-loss table is stored, not just the chosen action. The table
-- IS the explanation, and an auditor asking "why not hold?" needs the row that says.
CREATE TABLE IF NOT EXISTS decisions (
  decision_id     TEXT PRIMARY KEY,
  request_id      TEXT NOT NULL,
  sentence_idx    INTEGER,
  action          TEXT NOT NULL,       -- L0..L5
  loss_table_json TEXT NOT NULL,
  chosen_loss     REAL,
  runner_up       TEXT,
  margin          REAL,                -- how close the call was
  probs_json      TEXT,
  why_json        TEXT,
  hard_rule       TEXT,                -- set when a deterministic rule fired
  policy_version  TEXT,
  calib_version   TEXT,
  probe_version   TEXT,
  inputs_digest   TEXT,                -- sha256 of the exact inputs -> replayable (F9)
  latency_ms      REAL DEFAULT 0,
  ts              REAL NOT NULL
);

-- Spend per component. The verification-cost ratio is
-- sum(observer+verifier+judge+repair) / sum(upstream), computed from these rows.
CREATE TABLE IF NOT EXISTS spend (
  request_id TEXT NOT NULL,
  component  TEXT NOT NULL,            -- 'upstream'|'observer'|'verifier'|'judge'|'repair'|'reroute'
  model      TEXT,
  tokens     INTEGER DEFAULT 0,
  inr        REAL DEFAULT 0,
  ts         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  tool_call_id  TEXT PRIMARY KEY,
  request_id    TEXT NOT NULL,
  tool_name     TEXT NOT NULL,
  args_json     TEXT,
  reversibility TEXT,                  -- 'reversible' | 'costly' | 'irreversible'
  taint         TEXT,                  -- max provenance over the influencing context
  taint_reason  TEXT,                  -- which fragment, and why we think it influenced
  verdict       TEXT,                  -- 'allow' | 'hold' | 'block'
  hold_id       TEXT,
  resolved_by   TEXT,
  resolved_ts   REAL,
  ts            REAL NOT NULL
);

-- Durable pending state. The whole point is that it survives a restart (F6/F7): kill
-- the process mid-hold, start it again, and the review card is still there.
CREATE TABLE IF NOT EXISTS holds (
  hold_id         TEXT PRIMARY KEY,
  request_id      TEXT NOT NULL,
  kind            TEXT NOT NULL,       -- 'response' | 'tool_call'
  payload_json    TEXT,
  flagged_span    TEXT,
  evidence_json   TEXT,
  state           TEXT NOT NULL,       -- 'pending' | 'approved' | 'rejected' | 'expired'
  resume_token    TEXT,
  reason          TEXT,
  created_ts      REAL NOT NULL,
  sla_deadline_ts REAL,
  resolved_by     TEXT,
  resolved_ts     REAL
);

-- The attribution graph. `confidence` lives on the edge so rework can be reported as a
-- range rather than a false point estimate.
CREATE TABLE IF NOT EXISTS rework_edges (
  child_request_id  TEXT NOT NULL,
  parent_request_id TEXT NOT NULL,
  kind              TEXT NOT NULL,     -- 'retry' | 'regenerate' | 'human_escalation'
  confidence        REAL,
  inr_charged       REAL,
  ts                REAL NOT NULL,
  PRIMARY KEY (child_request_id, parent_request_id, kind)
);

CREATE TABLE IF NOT EXISTS shadow_runs (
  request_id            TEXT NOT NULL,
  cheaper_model         TEXT NOT NULL,
  verdict               TEXT,          -- 'parity' | 'worse' | 'better'
  judged_by             TEXT,
  inr_saved_if_switched REAL,
  ts                    REAL NOT NULL,
  PRIMARY KEY (request_id, cheaper_model)
);

CREATE TABLE IF NOT EXISTS fairness_pairs (
  pair_id         TEXT PRIMARY KEY,
  base_request_id TEXT NOT NULL,
  twin_request_id TEXT NOT NULL,
  attribute       TEXT,                -- the marker that was varied
  decision_field  TEXT,                -- 'approved' | 'amount_quoted' | 'hedge_count'
  base_value      TEXT,
  twin_value      TEXT,
  delta           REAL,
  ts              REAL NOT NULL
);

-- The human-labelled anchor set. Drives calibration AND the meta-monitor, which is why
-- it must stay disjoint from the eval set -- `split` is what makes that provable.
CREATE TABLE IF NOT EXISTS labels (
  item_id           TEXT PRIMARY KEY,
  source            TEXT,
  split             TEXT,              -- 'calibration' | 'eval' -- provably disjoint
  payload_json      TEXT,
  gold_ungrounded   INTEGER,
  gold_contradicted INTEGER,
  gold_unsafe       INTEGER,
  labeller          TEXT,
  ts                REAL NOT NULL
);

-- OpenTelemetry spans exported here rather than to Jaeger: one less service to fail
-- on stage, and the console reads them with the same DuckDB attachment (ADR-004).
CREATE TABLE IF NOT EXISTS spans (
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,
  parent_span_id TEXT,
  name           TEXT NOT NULL,
  start_ts       REAL NOT NULL,
  end_ts         REAL,
  duration_ms    REAL,
  status         TEXT,
  attributes_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_request     ON signals(request_id);
CREATE INDEX IF NOT EXISTS idx_decisions_request   ON decisions(request_id);
CREATE INDEX IF NOT EXISTS idx_spend_request       ON spend(request_id);
CREATE INDEX IF NOT EXISTS idx_rework_parent       ON rework_edges(parent_request_id);
CREATE INDEX IF NOT EXISTS idx_requests_tenant_ts  ON requests(tenant_id, ts);
CREATE INDEX IF NOT EXISTS idx_holds_state         ON holds(state, created_ts);
CREATE INDEX IF NOT EXISTS idx_tool_calls_request  ON tool_calls(request_id);
CREATE INDEX IF NOT EXISTS idx_spans_trace         ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_labels_split        ON labels(split);
