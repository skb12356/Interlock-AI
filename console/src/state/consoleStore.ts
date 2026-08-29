import type {
  ConsoleEnvelope,
  DecisionDetail,
  DecisionEvent,
  HoldEvent,
  ParsedFrame,
  SignalEvent,
  StakesEvent,
} from "../domain/contracts";
import { parseInterlockEvent } from "../domain/eventValidation";

export interface RequestTrace {
  requestId: string;
  prompt: string;
  assistantText: string;
  status: "streaming" | "complete" | "failed";
  error: string | null;
  degraded?: boolean;
  stakes: StakesEvent | null;
  sentenceOrder: number[];
  sentences: Record<number, SentenceTrace>;
  systemDecisions: DecisionEvent[];
  holds: HoldEvent[];
}

export interface SentenceTrace {
  sentenceIdx: number;
  signals: SignalEvent[];
  decisions: DecisionEvent[];
  decisionDetails: Record<string, DecisionDetail>;
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
    degraded: false,
    stakes: null,
    sentenceOrder: [],
    sentences: {},
    systemDecisions: [],
    holds: [],
  };
}

function updateSentence(
  trace: RequestTrace,
  sentenceIdx: number,
  update: (sentence: SentenceTrace) => SentenceTrace,
): RequestTrace {
  const sentence = trace.sentences[sentenceIdx] ?? {
    sentenceIdx,
    signals: [],
    decisions: [],
    decisionDetails: {},
  };
  return {
    ...trace,
    sentenceOrder: trace.sentenceOrder.includes(sentenceIdx)
      ? trace.sentenceOrder
      : [...trace.sentenceOrder, sentenceIdx].sort((left, right) => left - right),
    sentences: { ...trace.sentences, [sentenceIdx]: update(sentence) },
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
    const sentence = trace.sentences[frame.data.sentence_idx];
    const duplicate = sentence?.signals.some((signal) =>
      signal.sentence_idx === frame.data.sentence_idx &&
      signal.name === frame.data.name &&
      signal.prob === frame.data.prob
    ) ?? false;
    if (!duplicate) {
      nextTrace = updateSentence(trace, frame.data.sentence_idx, (current) => ({
        ...current,
        signals: [...current.signals, frame.data],
      }));
    }
  } else if (frame.event === "interlock.decision") {
    if (frame.data.sentence_idx < 0) {
      nextTrace = {
        ...trace,
        degraded: trace.degraded || frame.data.degraded === true,
        systemDecisions: trace.systemDecisions.some(
          (decision) => decision.decision_id === frame.data.decision_id,
        )
          ? trace.systemDecisions
          : [...trace.systemDecisions, frame.data],
      };
    } else {
      const decisionTrace = frame.data.degraded ? { ...trace, degraded: true } : trace;
      const sentence = decisionTrace.sentences[frame.data.sentence_idx];
      if (!sentence?.decisions.some((decision) => decision.decision_id === frame.data.decision_id)) {
        nextTrace = updateSentence(decisionTrace, frame.data.sentence_idx, (current) => ({
          ...current,
          decisions: [...current.decisions, frame.data],
        }));
      }
    }
  } else if (frame.event === "interlock.hold") {
    if (!trace.holds.some((hold) => hold.hold_id === frame.data.hold_id)) {
      nextTrace = { ...trace, holds: [...trace.holds, frame.data] };
    }
  }

  return {
    ...state,
    activeRequestId: requestId,
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
    return parseInterlockEvent(envelope.event, envelope.data);
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
    const existingSentence = trace.sentenceOrder.find((sentenceIdx) =>
      trace.sentences[sentenceIdx].decisions.some(
        (decision) => decision.decision_id === action.detail.decision_id,
      ),
    );
    const sentenceIdx = action.detail.sentence_idx ?? existingSentence ?? 0;
    const nextTrace = updateSentence(trace, sentenceIdx, (sentence) => ({
      ...sentence,
      decisionDetails: {
        ...sentence.decisionDetails,
        [action.detail.decision_id]: action.detail,
      },
    }));
    return {
      ...state,
      requests: {
        ...state.requests,
        [action.detail.request_id]: nextTrace,
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
