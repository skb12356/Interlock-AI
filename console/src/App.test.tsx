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
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
