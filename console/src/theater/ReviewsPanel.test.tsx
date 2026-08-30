import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewsPanel, toHoldCard, type HoldCard } from "./ReviewsPanel";

const tokenlessHold: HoldCard = {
  id: "hld_tokenless",
  kind: "response",
  title: "Review required",
  summary: "The initiating stream is no longer open.",
  tool: "—",
  sentence: "idx 0",
  impact: "₹10,000",
  sla: "SLA expired",
  slaExpired: true,
  evidence: [],
  flaggedSpan: null,
  hasToken: false,
};

function renderPanel(holds: HoldCard[], onReject = vi.fn()) {
  render(
    <ReviewsPanel
      holds={holds}
      loading={false}
      error={null}
      resolvingHoldId={null}
      onApprove={vi.fn()}
      onReject={onReject}
      onRefresh={vi.fn()}
    />,
  );
}

describe("ReviewsPanel", () => {
  it("renders an honest empty queue without actionable fixtures", () => {
    renderPanel([]);

    expect(screen.getByText("No pending holds.")).toBeInTheDocument();
    expect(screen.queryByText("HLD-4471")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve release" })).not.toBeInTheDocument();
  });

  it("keeps rejection available when approval has no token or the SLA expired", async () => {
    const onReject = vi.fn();
    renderPanel([tokenlessHold], onReject);

    const reject = screen.getByRole("button", { name: "Reject" });
    expect(reject).toBeEnabled();
    expect(screen.getByRole("button", { name: "Approve release" })).toBeDisabled();

    await userEvent.click(reject);
    expect(onReject).toHaveBeenCalledWith("hld_tokenless");
  });

  it("keeps a token-bearing response hold approvable when no SLA deadline was assigned", () => {
    const card = toHoldCard({
      hold_id: "hld_response",
      request_id: "req_1",
      kind: "response",
      reason: "response review",
      tool: null,
      sentence_idx: 0,
      payload: {},
      evidence: [],
      flagged_span: null,
      state: "pending",
      created_ts: 1,
      sla_deadline_ts: null,
      expired: false,
    }, true);

    renderPanel([card]);
    expect(card.slaExpired).toBe(false);
    expect(screen.getByText("No SLA deadline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve release" })).toBeEnabled();
  });
});
