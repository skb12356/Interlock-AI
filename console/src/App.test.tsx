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
    expect(await screen.findByLabelText("New session")).toBeInTheDocument();
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
    expect(await screen.findByLabelText("New session")).toBeInTheDocument();
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toContain("branch hours please");
  });

  it("sends on Enter", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Ask the bank assistant"), "branch hours please{Enter}");
    expect(await screen.findByText(/checked in \d+\.\d+ s/)).toBeInTheDocument();
  });

  it("reaches the reviews and evidence workspaces from the navbar", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Reviews" }));
    expect(await screen.findByRole("heading", { name: "Pending reviews" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Evidence" }));
    expect(await screen.findByRole("heading", { name: "Evidence ledger" })).toBeInTheDocument();
  });
});
