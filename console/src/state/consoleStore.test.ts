import { describe, expect, it } from "vitest";

import type { ConsoleEnvelope } from "../domain/contracts";
import { consoleReducer, initialConsoleState } from "./consoleStore";

describe("consoleReducer", () => {
  it("keeps partial assistant text when the transport fails", () => {
    let state = consoleReducer(initialConsoleState, { type: "request.started", requestId: "req_1", prompt: "Question" });
    state = consoleReducer(state, {
      type: "stream.frame",
      requestId: "req_1",
      frame: {
        kind: "openai",
        data: { choices: [{ delta: { content: "The current balance is" } }] },
      },
    });
    state = consoleReducer(state, {
      type: "request.failed",
      requestId: "req_1",
      message: "Connection interrupted",
    });

    expect(state.requests.req_1.assistantText).toBe("The current balance is");
    expect(state.requests.req_1.error).toBe("Connection interrupted");
    expect(state.requests.req_1.status).toBe("failed");
  });

  it("applies known Interlock events to the matching request", () => {
    let state = consoleReducer(initialConsoleState, { type: "request.started", requestId: "req_2", prompt: "Question" });
    state = consoleReducer(state, {
      type: "stream.frame",
      requestId: "req_2",
      frame: {
        kind: "interlock",
        event: "interlock.signal",
        data: { sentence_idx: 1, name: "grounding.support", prob: 0.82 },
      },
    });

    expect(state.requests.req_2.signals).toEqual([
      { sentence_idx: 1, name: "grounding.support", prob: 0.82 },
    ]);
  });

  it("suppresses the same named event arriving over direct SSE and projections", () => {
    let state = consoleReducer(initialConsoleState, { type: "request.started", requestId: "req_2", prompt: "Question" });
    const frame = {
      kind: "interlock" as const,
      event: "interlock.signal" as const,
      data: { sentence_idx: 1, name: "grounding.support", prob: 0.82 },
    };
    state = consoleReducer(state, { type: "stream.frame", requestId: "req_2", frame });
    state = consoleReducer(state, {
      type: "projection.received",
      envelope: {
        stream_id: "epoch-a",
        seq: 1,
        event: frame.event,
        data: frame.data,
        ts: 10,
        request_id: "req_2",
        replayed: false,
      },
    });

    expect(state.requests.req_2.signals).toHaveLength(1);
  });

  it("deduplicates projection envelopes and resets the cursor for a new stream", () => {
    const first: ConsoleEnvelope = {
      stream_id: "epoch-a",
      seq: 7,
      event: "interlock.stakes",
      data: { impact_inr: 100, reversibility: "reversible", domain: "general", mode: "buffered" },
      ts: 10,
      request_id: "req_a",
      replayed: false,
    };
    let state = consoleReducer(initialConsoleState, { type: "projection.received", envelope: first });
    state = consoleReducer(state, { type: "projection.received", envelope: first });

    expect(state.requests.req_a.stakes?.impact_inr).toBe(100);
    expect(state.projection.lastSeq).toBe(7);

    state = consoleReducer(state, {
      type: "projection.received",
      envelope: {
        ...first,
        stream_id: "epoch-b",
        seq: 1,
        request_id: "req_b",
        data: {
          impact_inr: 900,
          reversibility: "reversible",
          domain: "general",
          mode: "buffered",
        },
      },
    });

    expect(state.projection).toEqual({ streamId: "epoch-b", lastSeq: 1 });
    expect(state.requests.req_b.stakes?.impact_inr).toBe(900);
  });
});
