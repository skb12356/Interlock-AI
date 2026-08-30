export interface ReliabilityBin {
  bin_lower: number;
  bin_upper: number;
  count: number;
  mean_predicted: number;
  observed_frequency: number;
}

export interface CalibrationReport {
  ece: number;
  brier: number;
  auroc: number;
  reliability: ReliabilityBin[];
  /** Per-signal discrimination, present in the committed calibration artifact. */
  signal_auroc?: Record<string, number>;
  n_items?: number;
  folds?: number;
}

export interface ConformalReport {
  threshold: number;
  alpha: number;
  delta: number;
  escape_rate: number;
  intervention_rate: number;
  n_eval: number;
}

export interface EvaluationMetric {
  name: string;
  value: number;
  unit: string;
  target: string;
  met: boolean | null;
  ci: [number, number] | null;
  numerator: number | null;
  denominator: number | null;
  note: string;
}

export interface EvaluationReport {
  metrics: EvaluationMetric[];
  notes: string[];
}

export interface ActionLatency {
  action: string;
  model: string;
  provider: string;
  runs: number;
  samples_ms: number[];
  median_ms: number;
  max_ms: number;
}

export interface ConsoleStatus {
  source: "live" | "replay";
  replay: boolean;
  capabilities: {
    economics: { available: boolean; reason?: string };
    lane_c?: { available: boolean; reason?: string };
    artifacts?: Record<string, boolean>;
  };
}

export interface EconomicsProjection {
  available: boolean;
  reason?: string;
  routing_savings_inr?: number;
  regret_inr?: number | null;
  regret_samples?: number;
  rework_inr?: number | null;
  rework_samples?: number;
  net_value_inr?: number | null;
  net_value_ci_inr?: [number, number] | null;
  net_value_samples?: number;
  upstream_spend_basis?: "recorded" | "imputed" | "unmeasured";
}

export interface LaneCAxis {
  n: number;
  disparate: number;
  rate: number;
}

export interface LaneCProjection {
  n_pairs: number;
  by_axis: Record<string, LaneCAxis>;
  e_value: {
    n?: number;
    e_value?: number;
    running_max_e?: number;
    alert_threshold?: number;
    alerted?: boolean;
    always_valid_p?: number;
  };
  series: {
    t?: number[];
    e_value?: number[];
    running_max_e?: number[];
    p_value?: number[];
    alert_line?: number[];
  };
  notes: string[];
}

export interface LedgerSummary {
  request_count: number;
  spend_inr: number;
  action_counts: Record<string, number>;
  overhead_ms: { mean: number | null; p95: number | null };
  economics: EconomicsProjection;
}

export interface EvidenceBundle {
  calibration: CalibrationReport | null;
  conformal: ConformalReport | null;
  evaluation: EvaluationReport | null;
  latency: ActionLatency[] | null;
  laneC: LaneCProjection | null;
  ledger: LedgerSummary | null;
}
