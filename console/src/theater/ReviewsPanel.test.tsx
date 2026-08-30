import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewsPanel, shortHoldId, toHoldCard, type HoldCard } from "./ReviewsPanel";

const tokenlessHold: HoldCard = {
  id: "hld_tokenless",
  requestId: "req_tokenless",
  origin: null,
  kind: "response",
  title: "Review required",
  summary: "The initiating stream is no longer open.",
  tool: "—",
  sentence: "idx 0",
  impact: "₹10,000",
  domain: "insurance",
  heldCount: 2,
  created: "Created just now",
  sla: "SLA expired",
  slaExpired: true,
  evidence: [],
  flaggedSpan: null,
  hasToken: false,
};

function renderPanel(
  holds: HoldCard[],
  onReject = vi.fn(),
  onOpenSession = vi.fn(),
  onClearAll = vi.fn(),
) {
  render(
    <ReviewsPanel
      holds={holds}
      loading={false}
      error={null}
      resolvingHoldId={null}
      clearing={false}
      onApprove={vi.fn()}
      onReject={onReject}
      onRefresh={vi.fn()}
      onOpenSession={onOpenSession}
      onClearAll={onClearAll}
    />,
  );
  return { onOpenSession, onClearAll };
}

describe("ReviewsPanel", () => {
  it("renders an honest empty queue without actionable fixtures", () => {
    renderPanel([]);

    expect(screen.getByText("No pending holds.")).toBeInTheDocument();
    expect(screen.queryByText("HLD-4471")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve review" })).not.toBeInTheDocument();
  });

  it("keeps rejection available when approval has no token or the SLA expired", async () => {
    const onReject = vi.fn();
    renderPanel([tokenlessHold], onReject);

    const reject = screen.getByRole("button", { name: "Reject" });
    expect(reject).toBeEnabled();
    expect(screen.getByRole("button", { name: "Approve review" })).toBeDisabled();

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
    expect(screen.getByRole("button", { name: "Approve review" })).toBeEnabled();
  });

  it("elides a long hold id, keeps the full value and offers it for copying", async () => {
    const user = userEvent.setup();
    const clipboard = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: clipboard } });
    const longId = "hold_01M19NRJAWZYWKXSWTV3X6E6EK";
    renderPanel([{ ...tokenlessHold, id: longId }]);

    expect(screen.getByText(shortHoldId(longId))).toBeInTheDocument();
    expect(screen.getByTitle(longId)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: `Copy hold id ${longId}` }));
    expect(clipboard).toHaveBeenCalledWith(longId);
    vi.unstubAllGlobals();
  });

  it("links a hold back to the chat session that produced it", async () => {
    const user = userEvent.setup();
    const { onOpenSession } = renderPanel([
      { ...tokenlessHold, origin: { sessionId: "s_1", sessionTitle: "Insurance claim" } },
    ]);

    await user.click(screen.getByRole("button", { name: /Open chat session · Insurance claim/ }));
    expect(onOpenSession).toHaveBeenCalledWith("s_1");
  });

  it("keeps an unlinked request searchable", () => {
    renderPanel([tokenlessHold]);
    expect(screen.getByText("Chat unavailable in this browser")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy request id req_tokenless" })).toBeInTheDocument();
  });

  it("shows the real review scope on the card", () => {
    renderPanel([tokenlessHold]);
    expect(screen.getByText("insurance")).toBeInTheDocument();
    expect(screen.getByText("2 sentences")).toBeInTheDocument();
    expect(screen.getByText("Created just now")).toBeInTheDocument();
  });

  it("asks before clearing all pending reviews", async () => {
    const user = userEvent.setup();
    const { onClearAll } = renderPanel([tokenlessHold]);

    await user.click(screen.getByRole("button", { name: "Clear pending reviews" }));
    expect(onClearAll).not.toHaveBeenCalled();
    expect(screen.getByText(/reject all 1 pending review/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirm clearing pending reviews" }));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });
});
