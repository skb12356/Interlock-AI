import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConsoleStatus, EvidenceBundle, LedgerSummary } from "../domain/evidence";
import { EvidenceWorkspace } from "./EvidenceWorkspace";

const bundle: EvidenceBundle = {
  calibration: {
    ece: 0.0037,
    brier: 0.0206,
    auroc: 0.8944,
    reliability: [
      { bin_lower: 0, bin_upper: 0.1, count: 9209, mean_predicted: 0.0247, observed_frequency: 0.0227 },
      { bin_lower: 0.9, bin_upper: 1, count: 790, mean_predicted: 0.9776, observed_frequency: 1 },
    ],
  },
  conformal: {
    threshold: 0.015,
    alpha: 0.01,
    delta: 0.1,
    escape_rate: 0,
    intervention_rate: 1,
    n_eval: 840,
  },
  evaluation: {
    metrics: [
      { name: "Pre-Action Catch Rate", value: 1, unit: "%", target: ">= 90%", met: true, ci: [0.918, 1], numerator: 43, denominator: 43, note: "stopped before action" },
      { name: "False interventions", value: 1, unit: "%", target: "<= 2%", met: false, ci: [0.976, 1], numerator: 157, denominator: 157, note: "clean cases only" },
    ],
    notes: ["Generation is held fixed across both arms."],
  },
  latency: [
    { action: "L2_repair", model: "qwen3:8b", provider: "ollama", runs: 3, samples_ms: [13704, 14250, 13640], median_ms: 13704, max_ms: 14250 },
  ],
  laneC: null,
};

const status: ConsoleStatus = {
  source: "replay",
  replay: true,
  capabilities: { economics: { available: false, reason: "Lane C is not produced" } },
};

const ledger: LedgerSummary = {
  request_count: 12,
  spend_inr: 2,
  action_counts: { L0_pass: 8, L2_repair: 4 },
  overhead_ms: { mean: 12, p95: 18 },
  economics: { available: false, reason: "Lane C economics have not been produced" },
};

describe("EvidenceWorkspace", () => {
  it("keeps guarantees, intervention cost, and metric intervals attached", () => {
    render(<EvidenceWorkspace bundle={bundle} status={status} ledger={ledger} loading={false} error={null} />);

    expect(screen.getByText("REPLAY")).toBeInTheDocument();
    const guarantee = screen.getByRole("region", { name: "Certified guarantee" });
    expect(within(guarantee).getByText("0.0%", { exact: true })).toBeInTheDocument();
    expect(within(guarantee).getByText("100.0% intervention rate", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("91.8%–100.0% CI")).toBeInTheDocument();
    expect(screen.getByText("97.6%–100.0% CI")).toBeInTheDocument();
    expect(screen.getByText("MISS")).toBeInTheDocument();
  });

  it("shows measured ledger and latency without inventing economics", () => {
    render(<EvidenceWorkspace bundle={bundle} status={status} ledger={ledger} loading={false} error={null} />);

    expect(screen.getByText("12 requests")).toBeInTheDocument();
    expect(screen.getByText("₹2.00 spend")).toBeInTheDocument();
    expect(screen.getByText("13,704 ms")).toBeInTheDocument();
    expect(screen.getByText("Lane C economics have not been produced")).toBeInTheDocument();
    expect(screen.getAllByText("Unavailable", { exact: true })).toHaveLength(2);
    expect(screen.getByText("L0 pass")).toBeInTheDocument();
    expect(screen.getByText("8 decisions")).toBeInTheDocument();
  });

  it("labels missing committed evidence as unavailable", () => {
    render(
      <EvidenceWorkspace
        bundle={{ calibration: null, conformal: null, evaluation: null, latency: null, laneC: null }}
        status={status}
        ledger={ledger}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(3);
  });

  it("renders live net value with its interval and Lane C observations", () => {
    const liveLedger = {
      ...ledger,
      economics: {
        available: true,
        routing_savings_inr: 20,
        regret_inr: 2,
        rework_inr: 1,
        net_value_inr: 17,
        net_value_ci_inr: [12, 21],
        net_value_samples: 8,
      },
    } as unknown as LedgerSummary;
    const liveBundle = {
      ...bundle,
      laneC: {
        n_pairs: 12,
        by_axis: { language: { n: 12, disparate: 1, rate: 1 / 12 } },
        e_value: {
          n: 12,
          e_value: 1.4,
          running_max_e: 1.5,
          alert_threshold: 20,
          alerted: false,
          always_valid_p: 2 / 3,
        },
        series: { t: [1, 2], e_value: [1, 1.4], running_max_e: [1, 1.5], p_value: [1, 2 / 3], alert_line: [20, 20] },
        notes: ["observational projection"],
      },
    } as unknown as EvidenceBundle;

    render(<EvidenceWorkspace bundle={liveBundle} status={status} ledger={liveLedger} loading={false} error={null} />);

    expect(screen.getByText("₹17.00")).toBeInTheDocument();
    expect(screen.getByText("₹12.00–₹21.00 95% CI")).toBeInTheDocument();
    expect(screen.getByText("12 observed pairs")).toBeInTheDocument();
    expect(screen.getByText("8.3% disparity rate")).toBeInTheDocument();
    expect(screen.getByText(/below 20.00 alert threshold/i)).toBeInTheDocument();
  });
});
