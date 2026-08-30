export type Action =
  | "L0_pass"
  | "L1_annotate"
  | "L2_repair"
  | "L3_reroute"
  | "L4_hold"
  | "L5_block";

export type Reversibility = "reversible" | "costly" | "irreversible";
export type GateMode = "buffered" | "unbuffered";

export interface StakesEvent {
  impact_inr: number;
  reversibility: Reversibility;
  domain: string;
  mode: GateMode;
  stakes_id?: string;
  route_reason?: string | null;
  model_served?: string | null;
}

export interface SignalEvent {
  sentence_idx: number;
  name: string;
  prob: number | null;
}

export interface DecisionEvent {
  decision_id: string;
  sentence_idx: number;
  action: Action;
  chosen_loss: number;
  runner_up?: Action | null;
  margin?: number;
  counterfactual?: string | null;
  hard_rule?: string | null;
  degraded?: boolean;
}

export interface HoldEvent {
  hold_id: string;
  kind: "response" | "tool_call";
  reason: string;
  tool?: string | null;
  sentence_idx?: number | null;
}

export interface InterlockEventMap {
  "interlock.stakes": StakesEvent;
  "interlock.signal": SignalEvent;
  "interlock.decision": DecisionEvent;
  "interlock.hold": HoldEvent;
}

export type InterlockEventName = keyof InterlockEventMap;

export interface OpenAIChunk {
  id?: string;
  choices?: Array<{
    delta?: { content?: string | null };
    finish_reason?: string | null;
  }>;
}

export type StreamDiagnosticCode = "malformed-json" | "unknown-event" | "malformed-frame";

export type ParsedFrame =
  | { kind: "openai"; data: OpenAIChunk }
  | {
      [Name in InterlockEventName]: {
        kind: "interlock";
        event: Name;
        data: InterlockEventMap[Name];
      };
    }[InterlockEventName]
  | { kind: "done" }
  | { kind: "diagnostic"; code: StreamDiagnosticCode; message: string };

export interface ConsoleEnvelope<T = unknown> {
  stream_id: string;
  seq: number;
  event: string;
  data: T;
  ts: number;
  request_id?: string;
  replayed: boolean;
}

export interface LossRow {
  action: Action;
  residual_harm: number;
  nuisance: number;
  compute: number;
  latency: number;
  total: number;
  available: boolean;
  unavailable_reason: string | null;
}

export interface DecisionDetail {
  decision_id: string;
  request_id: string;
  sentence_idx: number | null;
  action: Action;
  loss_table: LossRow[];
  chosen_loss: number;
  runner_up: Action | null;
  margin: number;
  probs: Record<string, number>;
  why: string[];
  hard_rule: string | null;
  policy_version: string;
  calib_version: string;
  probe_version: string;
  inputs_digest: string;
  latency_ms: number;
}

export interface HoldProjection extends HoldEvent {
  request_id: string;
  session_id?: string | null;
  payload: Record<string, unknown>;
  evidence: string[];
  flagged_span: string | null;
  state: "pending";
  created_ts: number;
  sla_deadline_ts: number | null;
  expired: boolean;
}

export interface RetrievedFragment {
  doc_id: string;
  text: string;
  provenance: "retrieved_untrusted" | "retrieved_verified";
  domain: string;
  score: number;
}

export interface UploadedDocument {
  upload_id: string;
  filename: string;
  content_type: string;
  fragments: RetrievedFragment[];
  security: {
    provenance: "retrieved_untrusted";
    requires_explicit_interlock_context: true;
  };
}
