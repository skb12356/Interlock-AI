import { describe, expect, it, vi } from "vitest";

import {
  ConsoleApiError,
  getEvidenceBundle,
  getHolds,
  getLedgerSummary,
  getStatus,
  resolveHold,
} from "./consoleClient";

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

describe("console client evidence projections", () => {
  it("loads measured status, ledger, and every evidence artifact", async () => {
    const payloads: Record<string, unknown> = {
      "/console/status": {
        source: "replay",
        replay: true,
        capabilities: { economics: { available: false, reason: "not produced" } },
      },
      "/console/ledger/summary": {
        request_count: 4,
        spend_inr: 1.25,
        action_counts: { L0_pass: 4 },
        overhead_ms: { mean: 10, p95: 13 },
        economics: { available: false, reason: "not produced" },
      },
      "/console/artifacts/calibration%2Freport.json": { ece: 0.01, reliability: [] },
      "/console/artifacts/calibration%2Flambda.json": { escape_rate: 0, intervention_rate: 1 },
      "/console/artifacts/eval%2Freport-guaranteed.json": {
        metrics: { metrics: [], notes: ["Generation is held fixed."] },
      },
      "/console/artifacts/action_latency.json": [],
    };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = String(input);
      return new Response(JSON.stringify(payloads[url]), {
        status: url in payloads ? 200 : 404,
        headers: { "content-type": "application/json" },
      });
    });

    await expect(getStatus(fetcher)).resolves.toMatchObject({ source: "replay" });
    await expect(getLedgerSummary(fetcher)).resolves.toMatchObject({ request_count: 4 });
    await expect(getEvidenceBundle(fetcher)).resolves.toMatchObject({
      calibration: { ece: 0.01 },
      conformal: { escape_rate: 0 },
      evaluation: { metrics: [], notes: ["Generation is held fixed."] },
      latency: [],
    });
  });

  it("marks an unavailable artifact without hiding the available evidence", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("calibration%2Freport.json")) {
        return new Response("missing", { status: 404 });
      }
      return new Response(JSON.stringify(url.endsWith("action_latency.json") ? [] : {}), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });

    const evidence = await getEvidenceBundle(fetcher);
    expect(evidence.calibration).toBeNull();
    expect(evidence.conformal).toEqual({});
    expect(evidence.latency).toEqual([]);
  });
});
