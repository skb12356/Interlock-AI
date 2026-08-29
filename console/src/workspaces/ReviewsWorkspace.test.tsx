import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { HoldProjection } from "../domain/contracts";
import { ReviewsWorkspace } from "./ReviewsWorkspace";

const hold: HoldProjection = {
  hold_id: "hld_1",
  request_id: "req_1",
  kind: "tool_call",
  reason: "External side effect influenced by untrusted retrieval",
  tool: "send_email",
  sentence_idx: null,
  payload: { recipient: "customer@example.test", amount: 12000 },
  evidence: ["retrieved_untrusted content", "recipient was not supplied by customer"],
  flagged_span: "customer@example.test",
  state: "pending",
  created_ts: 1_700_000_000,
  sla_deadline_ts: null,
  expired: false,
};

describe("ReviewsWorkspace", () => {
  it("shows the durable evidence while disabling approval without the initiating secret", () => {
    render(
      <ReviewsWorkspace
        holds={[hold]}
        loading={false}
        error={null}
        resolvingHoldId={null}
        hasToken={() => false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("send_email")).toBeInTheDocument();
    expect(screen.getByText("retrieved_untrusted content")).toBeInTheDocument();
    expect(screen.getAllByText("customer@example.test", { exact: false })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Approve hold" })).toBeDisabled();
    expect(screen.getByText(/approval unavailable/i)).toBeInTheDocument();
  });

  it("allows approve and reject when their required capabilities are present", async () => {
    const user = userEvent.setup();
    const approve = vi.fn();
    const reject = vi.fn();
    render(
      <ReviewsWorkspace
        holds={[hold]}
        loading={false}
        error={null}
        resolvingHoldId={null}
        hasToken={() => true}
        onApprove={approve}
        onReject={reject}
        onRefresh={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Approve hold" }));
    await user.click(screen.getByRole("button", { name: "Reject and stop" }));
    expect(approve).toHaveBeenCalledWith("hld_1");
    expect(reject).toHaveBeenCalledWith("hld_1");
    expect(screen.getByText("Approval secret captured")).toBeInTheDocument();
  });

  it("directs the operator to refresh an expired hold", () => {
    render(
      <ReviewsWorkspace
        holds={[{ ...hold, expired: true }]}
        loading={false}
        error={null}
        resolvingHoldId={null}
        hasToken={() => true}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("SLA expired · refresh queue before acting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve hold" })).toBeDisabled();
  });
});
