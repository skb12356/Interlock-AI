import { describe, expect, it, vi } from "vitest";

import type { DecisionDetail, ParsedFrame } from "../domain/contracts";
import { streamChat } from "./chatClient";

function chunkedResponse(parts: string[], headers: Record<string, string>): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const part of parts) controller.enqueue(encoder.encode(part));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers });
}

describe("streamChat", () => {
  it("streams redacted frames and loads complete decision details after DONE", async () => {
    const frames: ParsedFrame[] = [];
    const tokens: Array<[string, string]> = [];
    const details: DecisionDetail[] = [];
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(
        chunkedResponse(
          [
            "event: interlock.hold\ndata: {\"hold_id\":\"hld_1\",\"kind\":\"tool_call\",\"reason\":\"review\",\"resume_",
            "token\":\"secret\"}\n\nevent: interlock.decision\ndata: {\"decision_id\":\"dec_1\",\"sentence_idx\":0,\"action\":\"L4_hold\",\"chosen_loss\":2}\n\ndata: [DONE]\n\n",
          ],
          { "x-interlock-request-id": "req_1" },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            decision_id: "dec_1",
            request_id: "req_1",
            sentence_idx: 0,
            action: "L4_hold",
            loss_table: [],
            chosen_loss: 2,
            runner_up: null,
            margin: 0,
            probs: {},
            why: [],
            hard_rule: null,
            policy_version: "p1",
            calib_version: "c1",
            probe_version: "v1",
            inputs_digest: "abc",
            latency_ms: 5,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );

    const result = await streamChat(
      { prompt: "Review this", scenario: "held" },
      {
        onFrame: (frame) => frames.push(frame),
        onResumeToken: (holdId, token) => tokens.push([holdId, token]),
        onDecisionDetail: (detail) => details.push(detail),
      },
      fetcher,
    );

    expect(result).toEqual({ requestId: "req_1", replay: false });
    expect(tokens).toEqual([["hld_1", "secret"]]);
    expect(JSON.stringify(frames)).not.toContain("secret");
    expect(frames.at(-1)).toEqual({ kind: "done" });
    expect(details).toHaveLength(1);
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/console/decisions/dec_1",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("reports an HTTP failure without retrying or creating a second chat request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("unavailable", { status: 503 }),
    );

    await expect(
      streamChat({ prompt: "Hello", scenario: "clean" }, { onFrame: vi.fn() }, fetcher),
    ).rejects.toThrow("Chat request failed with 503");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
