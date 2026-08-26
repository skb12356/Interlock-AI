import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("console shell", () => {
  it("switches between the three operator workspaces", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole("heading", { name: /live decision desk/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reviews/i }));
    expect(screen.getByRole("heading", { name: /pending reviews/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /evidence/i }));
    expect(screen.getByRole("heading", { name: /evidence ledger/i })).toBeInTheDocument();
  });
});
