import { describe, expect, it, vi } from "vitest";

import { ConsoleApiError, getHolds, resolveHold } from "./consoleClient";

describe("console client hold operations", () => {
  it("reads the enriched secret-free review projection", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          holds: [
            {
              hold_id: "hld_1",
              request_id: "req_1",
              kind: "tool_call",
              reason: "external side effect",
              tool: "send_email",
              sentence_idx: null,
              payload: { recipient: "customer@example.test" },
              evidence: ["untrusted source"],
              flagged_span: "recipient",
              state: "pending",
              created_ts: 1,
              sla_deadline_ts: null,
              expired: false,
            },
          ],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const holds = await getHolds(fetcher);
    expect(holds[0].tool).toBe("send_email");
    expect(JSON.stringify(holds)).not.toContain("resume_token");
  });

  it("sends the secret only for approval and no request body for rejection", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ state: "approved" }), { status: 200 }),
    );

    await resolveHold("hld_1", "approved", "secret", fetcher);
    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      "/gateway/v1/holds/hld_1/approve",
      expect.objectContaining({ body: JSON.stringify({ resume_token: "secret" }) }),
    );

    await resolveHold("hld_1", "rejected", undefined, fetcher);
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/gateway/v1/holds/hld_1/reject",
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("exposes stale response status so the workspace can refresh", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ error: { message: "no pending hold" } }), { status: 409 }),
    );

    await expect(resolveHold("hld_1", "approved", "secret", fetcher)).rejects.toEqual(
      expect.objectContaining<Partial<ConsoleApiError>>({ status: 409 }),
    );
  });
});
