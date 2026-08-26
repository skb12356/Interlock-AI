import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RequestTrace } from "../state/consoleStore";
import { LiveWorkspace } from "./LiveWorkspace";

const actions = [
  "L0_pass",
  "L1_annotate",
  "L2_repair",
  "L3_reroute",
  "L4_hold",
  "L5_block",
] as const;

function trace(): RequestTrace {
  return {
    requestId: "req_1",
    prompt: "What are the prepayment charges?",
    assistantText: "You may prepay without a foreclosure charge.",
    status: "complete",
    error: null,
    stakes: {
      impact_inr: 40000,
      reversibility: "costly",
      domain: "prepayment",
      mode: "buffered",
      model_served: "qwen3:8b",
    },
    signals: [
      { sentence_idx: 0, name: "grounding.citation_unsupported", prob: 0.94 },
    ],
    decisions: [
      {
        decision_id: "dec_1",
        sentence_idx: 0,
        action: "L2_repair",
        chosen_loss: 494.36,
        runner_up: "L4_hold",
        margin: 88.46,
        counterfactual: "A 2% charge applies under Clause 7.4.",
      },
    ],
    decisionDetails: {
      dec_1: {
        decision_id: "dec_1",
        request_id: "req_1",
        sentence_idx: 0,
        action: "L2_repair",
        loss_table: actions.map((action, index) => ({
          action,
          residual_harm: index + 1,
          nuisance: 1,
          compute: 1,
          latency: 1,
          total: index + 4,
          available: action !== "L3_reroute",
          unavailable_reason: action === "L3_reroute" ? "deadline exhausted" : null,
        })),
        chosen_loss: 494.36,
        runner_up: "L4_hold",
        margin: 88.46,
        probs: { ungrounded: 0.94 },
        why: ["The cited clause is absent from retrieved evidence"],
        hard_rule: null,
        policy_version: "banking-v3",
        calib_version: "calib-v1",
        probe_version: "probe-v1",
        inputs_digest: "abc",
        latency_ms: 14,
      },
    },
    holds: [],
  };
}

describe("LiveWorkspace", () => {
  it("renders the complete intervention argument beside shipped text", () => {
    render(
      <LiveWorkspace
        trace={trace()}
        prompt=""
        scenario="scene1"
        busy={false}
        onPromptChange={vi.fn()}
        onScenarioChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText("₹40,000")).toBeInTheDocument();
    expect(screen.getByText("94%", { exact: false })).toBeInTheDocument();
    const ladder = screen.getByRole("list", { name: "Intervention ladder" });
    expect(within(ladder).getByText("L2 repair").closest("li")).toHaveAttribute(
      "data-selected",
      "true",
    );
    expect(screen.getAllByRole("row")).toHaveLength(7);
    expect(screen.getByText("deadline exhausted")).toBeInTheDocument();
    expect(screen.getByText("What would have shipped")).toBeInTheDocument();
    expect(screen.getByText("A 2% charge applies under Clause 7.4.")).toBeInTheDocument();
    expect(screen.getByText("Shipped after Interlock")).toBeInTheDocument();
    expect(screen.getAllByText("You may prepay without a foreclosure charge.")).toHaveLength(2);
  });

  it("explains an L5 block even when no assistant content exists", () => {
    const blocked = trace();
    blocked.assistantText = "";
    blocked.decisions[0] = { ...blocked.decisions[0], action: "L5_block", hard_rule: "canary_leak" };
    blocked.decisionDetails = {};

    render(
      <LiveWorkspace
        trace={blocked}
        prompt=""
        scenario="blocked"
        busy={false}
        onPromptChange={vi.fn()}
        onScenarioChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText("Blocked before release")).toBeInTheDocument();
    expect(screen.getByText("Hard rule: canary_leak")).toBeInTheDocument();
  });

  it("keeps the submitted customer message stable while the next draft changes", () => {
    render(
      <LiveWorkspace
        trace={trace()}
        prompt="A different draft for the next request"
        scenario="scene1"
        busy={false}
        onPromptChange={vi.fn()}
        onScenarioChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText("What are the prepayment charges?")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Customer message" })).toHaveValue(
      "A different draft for the next request",
    );
  });
});
