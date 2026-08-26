import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("console shell", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("switches between the three operator workspaces", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole("heading", { name: /live decision desk/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reviews/i }));
    expect(screen.getByRole("heading", { name: /pending reviews/i })).toBeInTheDocument();
    expect(document.querySelector("main")).toHaveFocus();

    await user.click(screen.getByRole("button", { name: /evidence/i }));
    expect(screen.getByRole("heading", { name: /evidence ledger/i })).toBeInTheDocument();
  });

  it("surfaces a failed chat before a request id exists without resubmitting", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("unavailable", { status: 503 }));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Send through Interlock" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Chat request failed with 503");
    expect(fetcher.mock.calls.filter(([url]) => url === "/gateway/v1/chat/completions")).toHaveLength(1);
  });

  it("hydrates replayed decision history and shows provenance in every workspace", async () => {
    let socket: {
      onopen: (() => void) | null;
      onmessage: ((event: MessageEvent<string>) => void) | null;
      onclose: (() => void) | null;
      onerror: (() => void) | null;
      close: () => void;
    } | null = null;
    class FakeWebSocket {
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor() { socket = this; }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const decision = {
      decision_id: "dec_history",
      sentence_idx: 0,
      action: "L0_pass",
      chosen_loss: 1,
      runner_up: "L1_annotate",
      margin: 2,
    };
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/console/status") return new Response(JSON.stringify({
        source: "replay", replay: true, capabilities: { economics: { available: false } },
      }), { status: 200 });
      if (url.startsWith("/console/recent")) return new Response(JSON.stringify({
        stream_id: "epoch", latest_seq: 2, events: [
          { stream_id: "epoch", seq: 1, event: "interlock.stakes", data: { impact_inr: 50, reversibility: "reversible", domain: "branch_info", mode: "buffered" }, ts: 1, request_id: "req_history", replayed: true },
          { stream_id: "epoch", seq: 2, event: "interlock.decision", data: decision, ts: 2, request_id: "req_history", replayed: true },
        ],
      }), { status: 200 });
      if (url === "/console/decisions/dec_history") return new Response(JSON.stringify({
        ...decision, request_id: "req_history", loss_table: [], probs: {}, why: [], hard_rule: null,
        policy_version: "p", calib_version: "c", probe_version: "v", inputs_digest: "digest", latency_ms: 1,
      }), { status: 200 });
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetcher);

    render(<App />);
    expect(await screen.findByText(/REPLAY · connecting projection/i)).toBeInTheDocument();
    (socket as unknown as { onopen: (() => void) | null }).onopen?.();

    expect(await screen.findByText("Complete expected-loss table · INR")).toBeInTheDocument();
    expect(screen.queryByText(/being committed/)).not.toBeInTheDocument();
  });
});
