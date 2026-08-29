import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type {
  ConsoleStatus,
  EvaluationMetric,
  EvidenceBundle,
  LedgerSummary,
} from "../domain/evidence";

interface EvidenceWorkspaceProps {
  bundle: EvidenceBundle;
  status: ConsoleStatus | null;
  ledger: LedgerSummary | null;
  loading: boolean;
  error: string | null;
}

const percentage = (value: number) => `${(value * 100).toFixed(1)}%`;
const integer = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const money = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
});

function metricValue(metric: EvaluationMetric): string {
  if (metric.unit === "%") return percentage(metric.value);
  if (metric.unit === "ms") return `${integer.format(metric.value)} ms`;
  return `${integer.format(metric.value)} ${metric.unit}`.trim();
}

function metricInterval(metric: EvaluationMetric): string {
  if (!metric.ci) return "Interval unavailable";
  if (metric.unit === "%") return `${percentage(metric.ci[0])}–${percentage(metric.ci[1])} CI`;
  return `${integer.format(metric.ci[0])}–${integer.format(metric.ci[1])} ${metric.unit} CI`;
}

function Unavailable({ detail }: { detail?: string }) {
  return (
    <div className="evidence-unavailable">
      <strong>Unavailable</strong>
      {detail && <span>{detail}</span>}
    </div>
  );
}

export function EvidenceWorkspace({ bundle, status, ledger, loading, error }: EvidenceWorkspaceProps) {
  const reliability = bundle.calibration?.reliability.map((bin) => ({
    confidence: Number((((bin.bin_lower + bin.bin_upper) / 2) * 100).toFixed(1)),
    predicted: Number((bin.mean_predicted * 100).toFixed(1)),
    observed: Number((bin.observed_frequency * 100).toFixed(1)),
  })) ?? [];
  const latency = bundle.latency?.map((item) => ({
    action: item.action.replace("_", " "),
    median: item.median_ms,
    max: item.max_ms,
  })) ?? [];
  const laneSeries = (bundle.laneC?.series.t ?? []).map((point, index) => ({
    observation: point,
    eValue: bundle.laneC?.series.e_value?.[index] ?? 1,
    threshold: bundle.laneC?.series.alert_line?.[index] ?? null,
  }));
  const laneCounts = Object.values(bundle.laneC?.by_axis ?? {}).reduce(
    (total, axis) => ({ n: total.n + axis.n, disparate: total.disparate + axis.disparate }),
    { n: 0, disparate: 0 },
  );
  const disparityRate = laneCounts.n ? laneCounts.disparate / laneCounts.n : 0;
  const economics = ledger?.economics;

  if (loading) {
    return <div className="empty-state evidence-loading"><strong>Reading committed evidence</strong><span>Intervals stay attached to their measurements.</span></div>;
  }

  return (
    <section className="evidence-desk" aria-label="Evidence ledger">
      <header className="evidence-overview">
        <div>
          <p className="eyebrow">Projection provenance</p>
          <h2>Measured, bounded, attributable</h2>
        </div>
        <span className="source-badge">{status?.source.toUpperCase() ?? "UNKNOWN"}</span>
      </header>
      {error && <p className="queue-error" role="alert">{error}</p>}

      <div className="ledger-strip" aria-label="Ledger totals">
        {ledger ? (
          <>
            <div><span>Requests</span><strong>{integer.format(ledger.request_count)} requests</strong></div>
            <div><span>Measured spend</span><strong>{money.format(ledger.spend_inr)} spend</strong></div>
            <div><span>Mean overhead</span><strong>{ledger.overhead_ms.mean === null ? "Unavailable" : `${integer.format(ledger.overhead_ms.mean)} ms`}</strong></div>
            <div><span>P95 overhead</span><strong>{ledger.overhead_ms.p95 === null ? "Unavailable" : `${integer.format(ledger.overhead_ms.p95)} ms`}</strong></div>
          </>
        ) : <Unavailable detail="Ledger projection could not be read" />}
      </div>

      {ledger && (
        <div className="action-counts" aria-label="Decision action counts">
          {Object.entries(ledger.action_counts).map(([action, count]) => (
            <span key={action}><strong>{action.replace("_", " ")}</strong>{integer.format(count)} decisions</span>
          ))}
        </div>
      )}

      <div className="evidence-grid">
        <article className="evidence-card guarantee-card" aria-label="Certified guarantee" role="region">
          <header><span className="section-label">Certified ungrounded escape</span></header>
          {bundle.conformal ? (
            <>
              <div className="guarantee-number">{percentage(bundle.conformal.escape_rate)}</div>
              <p>certified escape rate at λ {bundle.conformal.threshold.toFixed(3)}</p>
              <div className="intervention-cost">
                <strong>{percentage(bundle.conformal.intervention_rate)} intervention rate</strong>
                <span>{integer.format(bundle.conformal.n_eval)} evaluation cases · α {bundle.conformal.alpha} · δ {bundle.conformal.delta}</span>
              </div>
            </>
          ) : <Unavailable detail="Certified calibration artifact is absent" />}
        </article>

        <article className="evidence-card calibration-card">
          <header><span className="section-label">Reliability calibration</span></header>
          {bundle.calibration ? (
            <>
              <dl className="calibration-summary">
                <div><dt>ECE</dt><dd>{bundle.calibration.ece.toFixed(4)}</dd></div>
                <div><dt>Brier</dt><dd>{bundle.calibration.brier.toFixed(4)}</dd></div>
                <div><dt>AUROC</dt><dd>{bundle.calibration.auroc.toFixed(4)}</dd></div>
              </dl>
              <div className="chart-frame" aria-label="Predicted and observed reliability chart">
                <ResponsiveContainer width="100%" height={210}>
                  <LineChart data={reliability} margin={{ top: 12, right: 12, bottom: 4, left: -18 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(23,54,56,.16)" />
                    <XAxis dataKey="confidence" unit="%" tick={{ fontSize: 9 }} />
                    <YAxis unit="%" domain={[0, 100]} tick={{ fontSize: 9 }} />
                    <Tooltip />
                    <Line type="monotone" dataKey="predicted" stroke="#276079" strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="observed" stroke="#c47a28" strokeWidth={2} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : <Unavailable detail="Calibration report is absent" />}
        </article>

        <article className="evidence-card evaluation-card">
          <header><span className="section-label">Evaluation intervals</span></header>
          {bundle.evaluation ? (
            <div className="metric-list">
              {bundle.evaluation.metrics.map((metric) => (
                <div className="metric-row" key={metric.name}>
                  <div><strong>{metric.name}</strong><span>{metric.target}</span></div>
                  <div><strong>{metricValue(metric)}</strong><span>{metricInterval(metric)}</span></div>
                  <b className={metric.met === false ? "metric-miss" : "metric-pass"}>{metric.met === false ? "MISS" : metric.met === true ? "PASS" : "INFO"}</b>
                </div>
              ))}
            </div>
          ) : <Unavailable detail="Guaranteed evaluation report is absent" />}
        </article>

        <article className="evidence-card latency-card">
          <header><span className="section-label">Action latency</span></header>
          {bundle.latency ? (
            <>
              <div className="latency-values">
                {bundle.latency.map((item) => <span key={`${item.action}-${item.model}`}><strong>{integer.format(item.median_ms)} ms</strong>{item.action.replace("_", " ")} median</span>)}
              </div>
              <div className="chart-frame" aria-label="Median and maximum action latency chart">
                <ResponsiveContainer width="100%" height={190}>
                  <BarChart data={latency} margin={{ top: 10, right: 12, bottom: 4, left: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(23,54,56,.16)" />
                    <XAxis dataKey="action" tick={{ fontSize: 9 }} />
                    <YAxis tick={{ fontSize: 9 }} />
                    <Tooltip />
                    <Bar dataKey="median" fill="#276079" isAnimationActive={false} />
                    <Bar dataKey="max" fill="#c47a28" isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : <Unavailable detail="Latency measurements are absent" />}
        </article>

        <article className="evidence-card economics-card">
          <header><span className="section-label">Live economics</span></header>
          {economics?.available && economics.net_value_inr !== undefined ? (
            <>
              <div className="economics-hero">
                <div>
                  <span>Net value</span>
                  <strong>{money.format(economics.net_value_inr)}</strong>
                  <small>
                    {economics.net_value_ci_inr
                      ? `${money.format(economics.net_value_ci_inr[0])}–${money.format(economics.net_value_ci_inr[1])} 95% CI`
                      : "Interval unavailable"}
                  </small>
                </div>
                <dl>
                  <div><dt>Routing savings</dt><dd>{money.format(economics.routing_savings_inr ?? 0)}</dd></div>
                  <div><dt>Measured regret</dt><dd>{money.format(economics.regret_inr ?? 0)}</dd></div>
                  <div><dt>Attributed rework</dt><dd>{money.format(economics.rework_inr ?? 0)}</dd></div>
                </dl>
              </div>
              <p className="evidence-note">{integer.format(economics.net_value_samples ?? 0)} measured net-value samples; interval stays attached to the estimate.</p>
            </>
          ) : (
            <Unavailable detail={economics?.reason ?? status?.capabilities.economics.reason ?? "Regret, rework, and net-value projections have not been produced"} />
          )}
        </article>

        <article className="evidence-card lane-c-card">
          <header><span className="section-label">Lane C monitoring</span></header>
          {bundle.laneC ? (
            <>
              <div className="lane-c-summary">
                <div><span>Coverage</span><strong>{integer.format(bundle.laneC.n_pairs)} observed pairs</strong></div>
                <div><span>Observed disparity</span><strong>{percentage(disparityRate)} disparity rate</strong></div>
                <div><span>Anytime-valid state</span><strong>{bundle.laneC.e_value.alerted ? "Alerted" : `Below ${(bundle.laneC.e_value.alert_threshold ?? 0).toFixed(2)} alert threshold`}</strong></div>
              </div>
              {laneSeries.length > 0 && (
                <div className="chart-frame" aria-label="Lane C anytime-valid e-value chart">
                  <ResponsiveContainer width="100%" height={190}>
                    <LineChart data={laneSeries} margin={{ top: 10, right: 12, bottom: 4, left: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(23,54,56,.16)" />
                      <XAxis dataKey="observation" tick={{ fontSize: 9 }} />
                      <YAxis tick={{ fontSize: 9 }} />
                      <Tooltip />
                      <Line type="monotone" dataKey="eValue" stroke="#276079" strokeWidth={2} dot={false} isAnimationActive={false} />
                      <Line type="monotone" dataKey="threshold" stroke="#a43f42" strokeDasharray="5 4" dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
              {bundle.laneC.notes.length > 0 && <p className="evidence-note">{bundle.laneC.notes.join(" · ")}</p>}
            </>
          ) : (
            <Unavailable detail={status?.capabilities.lane_c?.reason ?? "Lane C observations are unavailable"} />
          )}
        </article>
      </div>
    </section>
  );
}
