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
    artifacts?: Record<string, boolean>;
  };
}

export interface LedgerSummary {
  request_count: number;
  spend_inr: number;
  action_counts: Record<string, number>;
  overhead_ms: { mean: number | null; p95: number | null };
  economics: { available: boolean; reason: string };
}

export interface EvidenceBundle {
  calibration: CalibrationReport | null;
  conformal: ConformalReport | null;
  evaluation: EvaluationReport | null;
  latency: ActionLatency[] | null;
}
