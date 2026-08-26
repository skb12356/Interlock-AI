import type {
  ConsoleEnvelope,
  DecisionDetail,
  DecisionEvent,
  HoldEvent,
  ParsedFrame,
  SignalEvent,
  StakesEvent,
} from "../domain/contracts";

export interface RequestTrace {
  requestId: string;
  prompt: string;
  assistantText: string;
  status: "streaming" | "complete" | "failed";
  error: string | null;
  stakes: StakesEvent | null;
  signals: SignalEvent[];
  decisions: DecisionEvent[];
  decisionDetails: Record<string, DecisionDetail>;
  holds: HoldEvent[];
}

export interface ConsoleState {
  activeRequestId: string | null;
  requests: Record<string, RequestTrace>;
  projection: { streamId: string | null; lastSeq: number };
  diagnostics: Array<{ code: string; message: string }>;
}

export type ConsoleAction =
  | { type: "request.started"; requestId: string; prompt: string }
  | { type: "stream.frame"; requestId: string; frame: ParsedFrame }
  | { type: "request.failed"; requestId: string; message: string }
  | { type: "projection.received"; envelope: ConsoleEnvelope }
  | { type: "diagnostic.received"; code: string; message: string }
  | { type: "decision.loaded"; detail: DecisionDetail };

export const initialConsoleState: ConsoleState = {
  activeRequestId: null,
  requests: {},
  projection: { streamId: null, lastSeq: 0 },
  diagnostics: [],
};

function emptyTrace(requestId: string, prompt = ""): RequestTrace {
  return {
    requestId,
    prompt,
    assistantText: "",
    status: "streaming",
    error: null,
    stakes: null,
    signals: [],
    decisions: [],
    decisionDetails: {},
    holds: [],
  };
}

function applyFrame(state: ConsoleState, requestId: string, frame: ParsedFrame): ConsoleState {
  const trace = state.requests[requestId] ?? emptyTrace(requestId);
  let nextTrace = trace;
  let diagnostics = state.diagnostics;

  if (frame.kind === "openai") {
    const content = frame.data.choices?.[0]?.delta?.content;
    if (content) nextTrace = { ...trace, assistantText: trace.assistantText + content };
  } else if (frame.kind === "done") {
    nextTrace = { ...trace, status: "complete" };
  } else if (frame.kind === "diagnostic") {
    diagnostics = [...diagnostics, { code: frame.code, message: frame.message }];
  } else if (frame.event === "interlock.stakes") {
    nextTrace = { ...trace, stakes: frame.data };
  } else if (frame.event === "interlock.signal") {
    const duplicate = trace.signals.some((signal) =>
      signal.sentence_idx === frame.data.sentence_idx &&
      signal.name === frame.data.name &&
      signal.prob === frame.data.prob
    );
    if (!duplicate) nextTrace = { ...trace, signals: [...trace.signals, frame.data] };
  } else if (frame.event === "interlock.decision") {
    if (!trace.decisions.some((decision) => decision.decision_id === frame.data.decision_id)) {
      nextTrace = { ...trace, decisions: [...trace.decisions, frame.data] };
    }
  } else if (frame.event === "interlock.hold") {
    if (!trace.holds.some((hold) => hold.hold_id === frame.data.hold_id)) {
      nextTrace = { ...trace, holds: [...trace.holds, frame.data] };
    }
  }

  return {
    ...state,
    activeRequestId: state.activeRequestId ?? requestId,
    requests: { ...state.requests, [requestId]: nextTrace },
    diagnostics,
  };
}

function frameFromEnvelope(envelope: ConsoleEnvelope): ParsedFrame | null {
  if (
    envelope.event === "interlock.stakes" ||
    envelope.event === "interlock.signal" ||
    envelope.event === "interlock.decision" ||
    envelope.event === "interlock.hold"
  ) {
    return {
      kind: "interlock",
      event: envelope.event,
      data: envelope.data,
    } as ParsedFrame;
  }
  return null;
}

export function consoleReducer(state: ConsoleState, action: ConsoleAction): ConsoleState {
  if (action.type === "request.started") {
    return {
      ...state,
      activeRequestId: action.requestId,
      requests: { ...state.requests, [action.requestId]: emptyTrace(action.requestId, action.prompt) },
    };
  }

  if (action.type === "diagnostic.received") {
    return {
      ...state,
      diagnostics: [...state.diagnostics, { code: action.code, message: action.message }],
    };
  }

  if (action.type === "stream.frame") return applyFrame(state, action.requestId, action.frame);

  if (action.type === "request.failed") {
    const trace = state.requests[action.requestId] ?? emptyTrace(action.requestId);
    return {
      ...state,
      requests: {
        ...state.requests,
        [action.requestId]: { ...trace, status: "failed", error: action.message },
      },
    };
  }

  if (action.type === "decision.loaded") {
    const trace = state.requests[action.detail.request_id] ?? emptyTrace(action.detail.request_id);
    return {
      ...state,
      requests: {
        ...state.requests,
        [action.detail.request_id]: {
          ...trace,
          decisionDetails: {
            ...trace.decisionDetails,
            [action.detail.decision_id]: action.detail,
          },
        },
      },
    };
  }

  const envelope = action.envelope;
  const changedStream = state.projection.streamId !== envelope.stream_id;
  if (!changedStream && envelope.seq <= state.projection.lastSeq) return state;

  const projection = { streamId: envelope.stream_id, lastSeq: envelope.seq };
  const requestId = envelope.request_id;
  const frame = frameFromEnvelope(envelope);
  if (!requestId || !frame) return { ...state, projection };

  return { ...applyFrame(state, requestId, frame), projection };
}
