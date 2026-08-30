import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EvidenceBundle } from "../domain/evidence";
import { EvidencePanel } from "./EvidencePanel";

const unavailable: EvidenceBundle = {
  calibration: null,
  conformal: null,
  evaluation: null,
  latency: null,
  laneC: null,
  ledger: null,
};

describe("EvidencePanel", () => {
  it("reports unavailable projections instead of substituting seeded figures", () => {
    render(<EvidencePanel bundle={unavailable} loading={false} error={null} />);

    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.queryByText("100%", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("−0.20%", { exact: true })).not.toBeInTheDocument();
  });

  it("shows conformal intervention beside escapes and exposes ledger/Lane C availability", () => {
    render(
      <EvidencePanel
        loading={false}
        error={null}
        bundle={{
          ...unavailable,
          conformal: { threshold: 0.015, alpha: 0.01, delta: 0.1, escape_rate: 0, intervention_rate: 1, n_eval: 840 },
          ledger: {
            request_count: 4,
            spend_inr: 1.25,
            action_counts: { L0_pass: 4 },
            overhead_ms: { mean: 10, p95: 13 },
            economics: { available: false, reason: "regret and rework are unavailable" },
          },
          laneC: { n_pairs: 0, by_axis: {}, e_value: {}, series: {}, notes: ["no observations"] },
        }}
      />,
    );

    expect(screen.getByText("0.00% escape / 100.00% intervention")).toBeInTheDocument();
    expect(screen.getByText(/regret and rework are unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/No live fairness observations/)).toBeInTheDocument();
  });

  it("shows every measured live-economics component and its sample support", () => {
    render(
      <EvidencePanel
        loading={false}
        error={null}
        bundle={{
          ...unavailable,
          ledger: {
            request_count: 20,
            spend_inr: 80,
            action_counts: { L0_pass: 20 },
            overhead_ms: { mean: 10, p95: 13 },
            economics: {
              available: true,
              routing_savings_inr: 120,
              regret_inr: 25,
              regret_samples: 8,
              rework_inr: 10,
              rework_samples: 6,
              net_value_inr: 85,
              net_value_ci_inr: [60, 100],
              net_value_samples: 14,
            },
          },
        }}
      />,
    );

    expect(screen.getByText("Routing savings · ₹120")).toBeInTheDocument();
    expect(screen.getByText("Regret · ₹25 · 8 samples")).toBeInTheDocument();
    expect(screen.getByText("Rework · ₹10 · 6 samples")).toBeInTheDocument();
    expect(screen.getByText("Net ₹85 · CI ₹60–₹100 · 14 samples")).toBeInTheDocument();
  });
});
