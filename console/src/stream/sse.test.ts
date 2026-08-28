import { describe, expect, it, vi } from "vitest";

import { SseParser } from "./sse";

describe("SseParser", () => {
  it("parses arbitrarily split OpenAI and named Interlock frames", () => {
    const parser = new SseParser();
    const frames = [
      ...parser.push("event: interlock.stakes\r\ndata: {\"impact_inr\":40000,\"reversibility\":\"costly\",\"domain\":\"loan_terms\",\"mode\":\"buffered\"}\r"),
      ...parser.push("\n\r\ndata: {\"id\":\"chatcmpl-1\",\"choices\":[{\"delta\":{\"content\":\"Under your agreement\"}}]}\n\n"),
    ];

    expect(frames).toEqual([
      {
        kind: "interlock",
        event: "interlock.stakes",
        data: {
          impact_inr: 40000,
          reversibility: "costly",
          domain: "loan_terms",
          mode: "buffered",
        },
      },
      {
        kind: "openai",
        data: {
          id: "chatcmpl-1",
          choices: [{ delta: { content: "Under your agreement" } }],
        },
      },
    ]);
  });

  it("recognizes stream completion even when the final frame has no trailing blank line", () => {
    const parser = new SseParser();
    parser.push("data: [DO");
    expect(parser.finish("NE]\n")).toEqual([{ kind: "done" }]);
  });

  it("turns malformed JSON and unknown named events into diagnostics", () => {
    const parser = new SseParser();
    const frames = parser.push(
      "data: {bad}\n\nevent: interlock.future\ndata: {\"value\":1}\n\n",
    );

    expect(frames).toEqual([
      expect.objectContaining({ kind: "diagnostic", code: "malformed-json" }),
      expect.objectContaining({ kind: "diagnostic", code: "unknown-event" }),
    ]);
  });

  it("rejects structurally invalid named and OpenAI payloads", () => {
    const parser = new SseParser();
    const frames = parser.push(
      "event: interlock.decision\ndata: {}\n\ndata: {\"choices\":\"not-an-array\"}\n\n",
    );

    expect(frames).toEqual([
      expect.objectContaining({ kind: "diagnostic", code: "malformed-frame" }),
      expect.objectContaining({ kind: "diagnostic", code: "malformed-frame" }),
    ]);
  });

  it("captures and removes a hold token before returning ordinary event data", () => {
    const capture = vi.fn();
    const parser = new SseParser({ onResumeToken: capture });
    const [frame] = parser.push(
      "event: interlock.hold\ndata: {\"hold_id\":\"hld_1\",\"kind\":\"tool_call\",\"reason\":\"review\",\"resume_token\":\"secret-token\"}\n\n",
    );

    expect(capture).toHaveBeenCalledWith("hld_1", "secret-token");
    expect(frame).toEqual({
      kind: "interlock",
      event: "interlock.hold",
      data: { hold_id: "hld_1", kind: "tool_call", reason: "review" },
    });
    expect(JSON.stringify(frame)).not.toContain("secret-token");
  });
});
