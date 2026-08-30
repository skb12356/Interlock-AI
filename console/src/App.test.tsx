import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function stubFetch(handler: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(handler(String(input)))));
}

const notFound = () => new Response("{}", { status: 404 });

describe("console shell", () => {
  beforeEach(() => stubFetch(notFound));
  afterEach(() => vi.unstubAllGlobals());

  it("opens on the hero with the four seeded scenes", async () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "Routing and guarding are the same decision." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Invented loan clause/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Canary leak/ })).toBeInTheDocument();
  });

  it("owns the projection WebSocket used for replay and reconnect history", () => {
    const urls: string[] = [];
    class FakeWebSocket {
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(url: string) { urls.push(url); }
      close() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);

    render(<App />);

    expect(urls).toEqual(["ws://localhost:3000/console/ws"]);
  });

  it("replaces the prompt when a scene chip is picked", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: /Branch hours/ }));
    expect(screen.getByLabelText("Prompt for the bank assistant")).toHaveValue(
      "What time does the MG Road branch open tomorrow?",
    );
  });

  it("enters the stage machine on submit", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: /Send through Interlock/ }));

    expect(await screen.findByRole("heading", { name: "Pre-flight" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Stage 04/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next stage" })).toBeEnabled();
  });

  it("jumps to a stage from the rail", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: /Send through Interlock/ }));
    await user.click(await screen.findByRole("button", { name: /Stage 06, Release/ }));
    expect(await screen.findByRole("heading", { name: "Release" })).toBeInTheDocument();
  });

  it("shows an honest empty review queue and the evidence ledger", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /Reviews/ }));
    const reviews = await screen.findByLabelText("Pending reviews");
    expect(within(reviews).getByText("No pending holds.")).toBeInTheDocument();
    expect(within(reviews).queryByText("HLD-4471")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Evidence/ }));
    const evidence = await screen.findByLabelText("Evidence ledger");
    await waitFor(() => expect(within(evidence).getByText("Pre-action catch rate")).toBeInTheDocument());
    expect(within(evidence).getAllByText("Unavailable").length).toBeGreaterThan(0);
  });

  it("reads real evidence artifacts when the projection answers", async () => {
    stubFetch((url) => {
      // The client percent-encodes the artifact name, so match the decoded path.
      if (decodeURIComponent(url).includes("calibration/report.json")) {
        return new Response(
          JSON.stringify({
            ece: 0.0037,
            brier: 0.0207,
            auroc: 0.9088,
            reliability: [],
            signal_auroc: { "grounding.unsupported_content": 0.8355 },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return notFound();
    });

    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: /Evidence/ }));
    expect(await screen.findByText("0.909")).toBeInTheDocument();
    expect(screen.getByText("0.836")).toBeInTheDocument();
  });
});
