import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { SESSION_STORAGE_KEY } from "./chat/types";

const CHUNK = {
  id: "chatcmpl_1",
  choices: [{ delta: { content: "The MG Road branch opens at 9:30 am." }, finish_reason: null }],
};

const STAKES = {
  impact_inr: 40,
  reversibility: "reversible",
  domain: "servicing",
  mode: "buffered",
  model_served: "qwen3:8b",
  route_reason: "low stakes",
};

const DECISION = {
  decision_id: "dec_1",
  sentence_idx: 0,
  action: "L0_pass",
  chosen_loss: 2,
  runner_up: "L1_annotate",
  margin: 12,
  counterfactual: null,
  hard_rule: null,
};

/** A gateway-shaped SSE body: the console must read the real wire format. */
function sseResponse(): Response {
  const frames = [
    `event: interlock.stakes\ndata: ${JSON.stringify(STAKES)}\n\n`,
    `data: ${JSON.stringify(CHUNK)}\n\n`,
    `event: interlock.signal\ndata: ${JSON.stringify({ sentence_idx: 0, name: "grounding.unsupported_content", prob: 0.04 })}\n\n`,
    `event: interlock.decision\ndata: ${JSON.stringify(DECISION)}\n\n`,
    "data: [DONE]\n\n",
  ];
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      frames.forEach((frame) => controller.enqueue(encoder.encode(frame)));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/chat/completions")) return Promise.resolve(sseResponse());
      if (url.includes("/console/status")) {
        return Promise.resolve(
          new Response(JSON.stringify({ source: "replay", replay: true, capabilities: { economics: { available: true } } }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
}

describe("console shell", () => {
  beforeEach(() => {
    localStorage.clear();
    window.location.hash = "";
    stubFetch();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("opens on an empty chat session with no mode switch", async () => {
    render(<App />);
    expect(await screen.findByLabelText("Empty session")).toBeInTheDocument();
    expect(screen.getByLabelText("Ask the bank assistant")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /demo trace/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /live backend/i })).not.toBeInTheDocument();
  });

  it("has no transport controls on the trace view", async () => {
    render(<App />);
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next stage" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Previous stage" })).not.toBeInTheDocument();
  });

  it("runs a prompt through the gateway and shows the stages beside the answer", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Ask the bank assistant"), "What time does the MG Road branch open?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/The MG Road branch opens at 9:30 am\./)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /See it live/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/checked in \d+\.\d+ s/)).toBeInTheDocument());
    expect(screen.getByText("L0 PASS")).toBeInTheDocument();
  });

  it("opens the trace view from the answer and can come back", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Ask the bank assistant"), "branch hours please");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByText(/checked in \d+\.\d+ s/)).toBeInTheDocument());

    // A finished turn reopens its stored trace from the first stage.
    await user.click(screen.getByRole("button", { name: /See it live/ }));
    expect(await screen.findByRole("heading", { name: "Pre-flight" })).toBeInTheDocument();
    expect(screen.getByText("Time taken")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back to chat" }));
    expect(await screen.findByLabelText("Session transcript")).toBeInTheDocument();
  });

  it("keeps sessions in the sidebar and starts new ones", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Ask the bank assistant"), "branch hours please");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /See it live/ })).toBeInTheDocument());

    const sidebar = screen.getByLabelText("Chat sessions");
    expect(within(sidebar).getByRole("button", { name: "branch hours please" })).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: /New chat session/ }));
    expect(await screen.findByLabelText("Empty session")).toBeInTheDocument();
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toContain("branch hours please");
  });

  it("sends on Enter", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Ask the bank assistant"), "branch hours please{Enter}");
    expect(await screen.findByText(/checked in \d+\.\d+ s/)).toBeInTheDocument();
  });

  it("deletes a session after asking first", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Ask the bank assistant"), "branch hours please");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByText(/checked in \d+\.\d+ s/)).toBeInTheDocument());

    const sidebar = screen.getByLabelText("Chat sessions");
    await user.click(within(sidebar).getByRole("button", { name: /Delete session branch hours please/ }));
    // Nothing is gone yet: the bin asks once.
    expect(within(sidebar).getByRole("button", { name: "branch hours please" })).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: "Keep session" }));
    expect(within(sidebar).getByRole("button", { name: "branch hours please" })).toBeInTheDocument();

    await user.click(within(sidebar).getByRole("button", { name: /Delete session branch hours please/ }));
    await user.click(within(sidebar).getByRole("button", { name: /Confirm deleting branch hours please/ }));

    expect(within(sidebar).queryByRole("button", { name: "branch hours please" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Empty session")).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem(SESSION_STORAGE_KEY)).not.toContain("branch hours please"));
  });

  it("explains itself in plain language and cites its sources", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "About" }));
    const about = await screen.findByLabelText("About Interlock");
    expect(within(about).getByRole("heading", { name: /control room for AI answers/i })).toBeInTheDocument();
    expect(within(about).getByText(/Farquhar, Kossen, Kuhn & Gal/)).toBeInTheDocument();
    expect(within(about).getByText(/MiniCheck/)).toBeInTheDocument();
    expect(within(about).getByText(/CaMeL/)).toBeInTheDocument();
  });

  it("reaches the reviews and evidence workspaces from the navbar", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Reviews" }));
    expect(await screen.findByRole("heading", { name: "Pending reviews" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Evidence" }));
    expect(await screen.findByRole("heading", { name: "Evidence ledger" })).toBeInTheDocument();
  });

  it("clears pending reviews through audited rejection requests", async () => {
    const user = userEvent.setup();
    let holdReads = 0;
    const holds = ["hld_1", "hld_2"].map((holdId) => ({
      hold_id: holdId,
      request_id: `req_${holdId}`,
      session_id: null,
      kind: "response",
      reason: "L4_hold",
      tool: null,
      sentence_idx: 0,
      payload: { held_count: 1, domain: "insurance", impact_inr: 10_000 },
      evidence: ["unsupported high-impact statement"],
      flagged_span: null,
      state: "pending",
      created_ts: Date.now() / 1000,
      sla_deadline_ts: Date.now() / 1000 + 900,
      expired: false,
    }));
    const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/console/status")) {
        return Promise.resolve(new Response(JSON.stringify({ source: "live", replay: false }), { status: 200 }));
      }
      if (url === "/console/holds") {
        const body = holdReads++ === 0 ? holds : [];
        return Promise.resolve(new Response(JSON.stringify({ holds: body }), { status: 200 }));
      }
      if (url.includes("/gateway/v1/holds/") && url.endsWith("/reject")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    });
    vi.stubGlobal("fetch", fetcher);
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Reviews" }));
    expect(await screen.findByText("2 holds are waiting on a human. Approval uses the initiating stream token; rejection never requires it.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear pending reviews" }));
    await user.click(screen.getByRole("button", { name: "Confirm clearing pending reviews" }));

    expect(await screen.findByText("No pending holds.")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([input]) => String(input).endsWith("/reject"))).toHaveLength(2);
  });
});
