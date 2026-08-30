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
    await vi.waitFor(() => expect(details).toHaveLength(1));
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

  it("attaches uploaded fragments to live requests without replay-only scenario fields", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      chunkedResponse(["data: [DONE]\n\n"], { "x-interlock-request-id": "req_live" }),
    );
    const fragments = [{
      doc_id: "upload_abc",
      text: "hidden instruction",
      provenance: "retrieved_untrusted" as const,
      domain: "general",
      score: 1,
    }];

    await streamChat(
      { prompt: "Review this claim", scenario: "held", replay: false, fragments },
      { onFrame: vi.fn() },
      fetcher,
    );

    const body = JSON.parse(String(fetcher.mock.calls[0][1]?.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty("scenario");
    expect(body).toMatchObject({ interlock: { retrieved: fragments } });
  });

  it("persists the browser session id with a live request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      chunkedResponse(["data: [DONE]\n\n"], { "x-interlock-request-id": "req_live" }),
    );

    await streamChat(
      { prompt: "Review this claim", replay: false, sessionId: "session_42" },
      { onFrame: vi.fn() },
      fetcher,
    );

    const body = JSON.parse(String(fetcher.mock.calls[0][1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ session_id: "session_42" });
  });

  it("fails an interrupted body that closes before the DONE sentinel", async () => {
    const frames: ParsedFrame[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      chunkedResponse(["data: {\"choices\":[{\"delta\":{\"content\":\"Partial\"}}]}\n\n"], {
        "x-interlock-request-id": "req_partial",
      }),
    );

    await expect(streamChat(
      { prompt: "Hello", scenario: "clean" },
      { onFrame: (frame) => frames.push(frame) },
      fetcher,
    )).rejects.toThrow("before [DONE]");
    expect(frames).toContainEqual(expect.objectContaining({ kind: "openai" }));
  });

  it("keeps a completed stream successful when decision evidence is temporarily unavailable", async () => {
    const diagnostics: string[] = [];
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(chunkedResponse([
        "event: interlock.decision\ndata: {\"decision_id\":\"dec_1\",\"sentence_idx\":0,\"action\":\"L0_pass\",\"chosen_loss\":1}\n\ndata: [DONE]\n\n",
      ], { "x-interlock-request-id": "req_1" }))
      .mockResolvedValue(new Response("unavailable", { status: 503 }));

    await expect(streamChat(
      { prompt: "Hello", scenario: "clean" },
      { onFrame: vi.fn(), onDecisionDetail: vi.fn(), onDiagnostic: (message) => diagnostics.push(message) },
      fetcher,
    )).resolves.toEqual({ requestId: "req_1", replay: false });
    await vi.waitFor(() => expect(diagnostics).toEqual(["Decision detail request failed with 503"]));
  });

  it("does not keep stream completion waiting on eventual decision persistence", async () => {
    let resolveDetail!: (response: Response) => void;
    const detailResponse = new Promise<Response>((resolve) => { resolveDetail = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(chunkedResponse([
        "event: interlock.decision\ndata: {\"decision_id\":\"dec_late\",\"sentence_idx\":0,\"action\":\"L0_pass\",\"chosen_loss\":1}\n\ndata: [DONE]\n\n",
      ], { "x-interlock-request-id": "req_late" }))
      .mockReturnValueOnce(detailResponse)
      .mockResolvedValue(new Response("unavailable", { status: 503 }));

    const result = streamChat(
      { prompt: "Hello", scenario: "clean" },
      { onFrame: vi.fn(), onDecisionDetail: vi.fn() },
      fetcher,
    );
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    const settledBeforeDetail = await Promise.race([
      result.then(() => true),
      new Promise<false>((resolve) => globalThis.setTimeout(() => resolve(false), 0)),
    ]);
    resolveDetail(new Response("not ready", { status: 404 }));
    await result;

    expect(settledBeforeDetail).toBe(true);
  });

  it("does not hydrate request-level synthetic decisions that have no ledger row", async () => {
    const diagnostics: string[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(chunkedResponse([
      "event: interlock.decision\ndata: {\"decision_id\":\"dec_cache_hit\",\"sentence_idx\":-1,\"action\":\"L0_pass\",\"chosen_loss\":0,\"degraded\":false}\n\ndata: [DONE]\n\n",
    ], { "x-interlock-request-id": "req_cached" }));

    await streamChat(
      { prompt: "Hello", scenario: "clean", replay: false },
      { onFrame: vi.fn(), onDecisionDetail: vi.fn(), onDiagnostic: (message) => diagnostics.push(message) },
      fetcher,
    );

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(diagnostics).toEqual([]);
  });
});
