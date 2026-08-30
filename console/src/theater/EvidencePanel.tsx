import type { EvaluationMetric, EvidenceBundle } from "../domain/evidence";
import { MicroLabel } from "./primitives";
import { color, font, radius } from "./tokens";

const SIGNAL_LABELS: Record<string, string> = {
  "grounding.unsupported_content": "unsupported_content",
  "grounding.numeric_unsupported": "numeric_unsupported",
  "grounding.citation_unsupported": "citation_unsupported",
  "grounding.context_conflict": "context_conflict",
  "grounding.question_drift": "question_drift",
  "grounding.overconfidence": "overconfidence",
};

function signalTone(auroc: number): string {
  if (auroc >= 0.7) return color.accent;
  if (auroc >= 0.55) return color.warn;
  return color.fail;
}

function findMetric(metrics: EvaluationMetric[] | undefined, name: string): EvaluationMetric | undefined {
  return metrics?.find((metric) => metric.name.toLowerCase() === name.toLowerCase());
}

function metricText(metric: EvaluationMetric | undefined): string {
  if (!metric) return "Unavailable";
  if (metric.unit === "%") return `${(metric.value * 100).toFixed(metric.value === 1 ? 0 : 2)}%`;
  if (metric.unit === "ms") return `${Math.round(metric.value)} ms`;
  return `${metric.value}`;
}

function interval(metric: EvaluationMetric | undefined): string {
  if (!metric?.ci) return metric?.note ?? "No measured projection is available.";
  const scale = metric.unit === "%" ? 100 : 1;
  const suffix = metric.unit === "%" ? "%" : ` ${metric.unit}`;
  return `CI ${(metric.ci[0] * scale).toFixed(2)}–${(metric.ci[1] * scale).toFixed(2)}${suffix}`;
}

const cardStyle = {
  borderRadius: radius.card,
  border: `1px solid ${color.line}`,
  background: color.bgPanel,
  padding: "20px 22px",
};

export function EvidencePanel({
  bundle,
  loading,
  error,
}: {
  bundle: EvidenceBundle;
  loading: boolean;
  error: string | null;
}) {
  const metrics = bundle.evaluation?.metrics;
  const catchRate = findMetric(metrics, "Pre-Action Catch Rate");
  const addedLatency = findMetric(metrics, "Added p95 latency");
  const escapes = findMetric(metrics, "Ungrounded escapes");
  const falseInterventions = findMetric(metrics, "False interventions");
  const ledger = bundle.ledger;
  const economics = ledger?.economics;
  const conformal = bundle.conformal;
  const laneC = bundle.laneC;
  const signalAuroc = Object.entries(bundle.calibration?.signal_auroc ?? {});
  const strip = [
    { value: metricText(catchRate), label: "Pre-action catch rate", note: interval(catchRate), tone: catchRate?.met ? color.pass : color.text },
    { value: metricText(addedLatency), label: "Added p95 latency", note: interval(addedLatency), tone: addedLatency?.met ? color.pass : color.text },
    { value: ledger ? ledger.request_count.toLocaleString("en-IN") : "Unavailable", label: "Ledger requests", note: ledger ? `Actions: ${Object.entries(ledger.action_counts).map(([action, count]) => `${action} ${count}`).join(" · ") || "none"}` : "Ledger projection unavailable." },
    { value: ledger ? `₹${ledger.spend_inr.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "Unavailable", label: "Recorded spend", note: ledger?.overhead_ms.p95 == null ? "p95 overhead unavailable" : `p95 overhead ${Math.round(ledger.overhead_ms.p95)} ms` },
  ];
  const checks = [
    { name: "Ungrounded escapes", metric: escapes },
    { name: "Added p95 latency", metric: addedLatency },
    { name: "False interventions", metric: falseInterventions },
  ];

  return (
    <section aria-label="Evidence ledger" style={{ position: "relative", zIndex: 2, maxWidth: "1180px", margin: "0 auto", padding: "34px 34px 60px", display: "flex", flexDirection: "column", gap: "16px" }}>
      <div>
        <MicroLabel tone={color.accent}>Operator workspace / 03</MicroLabel>
        <h2 style={{ margin: "10px 0 0", font: `600 34px/1.05 ${font.sans}`, letterSpacing: "-.03em" }}>Evidence ledger</h2>
        <p style={{ margin: "12px 0 0", maxWidth: "660px", font: `400 14px/1.65 ${font.sans}`, color: color.textDim }}>
          Measured artifacts and live ledger projections. Missing observations stay unavailable; the console never substitutes demo results.
        </p>
      </div>

      {error ? <p role="alert" style={{ margin: 0, font: `400 12px ${font.mono}`, color: color.fail }}>{error}</p> : null}
      {loading ? <MicroLabel>Reading committed evidence…</MicroLabel> : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px,1fr))", borderRadius: radius.card, border: `1px solid ${color.line}`, background: color.bgPanel }}>
        {strip.map((cell, index) => (
          <div key={cell.label} style={{ padding: "18px 20px", borderLeft: index === 0 ? "none" : `1px solid ${color.lineSoft}` }}>
            <div style={{ font: `700 24px ${font.mono}`, color: cell.tone ?? color.text }}>{cell.value}</div>
            <MicroLabel style={{ marginTop: "8px" }}>{cell.label}</MicroLabel>
            <p style={{ margin: "8px 0 0", font: `400 10px/1.5 ${font.sans}`, color: color.textFaint }}>{cell.note}</p>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(330px,1fr))", gap: "16px" }}>
        <article style={cardStyle}>
          <MicroLabel tone={color.accent}>Calibration</MicroLabel>
          {bundle.calibration ? (
            <>
              <div style={{ display: "flex", gap: "26px", marginTop: "14px" }}>
                {[
                  { value: bundle.calibration.ece.toFixed(4), label: "ECE" },
                  { value: bundle.calibration.brier.toFixed(4), label: "Brier" },
                  { value: bundle.calibration.auroc.toFixed(3), label: "AUROC" },
                ].map((figure) => <div key={figure.label}><div style={{ font: `700 18px ${font.mono}` }}>{figure.value}</div><MicroLabel style={{ marginTop: "4px" }}>{figure.label}</MicroLabel></div>)}
              </div>
              {signalAuroc.length ? (
                <div style={{ marginTop: "18px", display: "flex", flexDirection: "column", gap: "9px" }}>
                  <MicroLabel>Per-signal AUROC</MicroLabel>
                  {signalAuroc.map(([name, auroc]) => (
                    <div key={name} style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                      <span style={{ width: "190px", font: `400 10px ${font.mono}`, color: color.textDim }}>{SIGNAL_LABELS[name] ?? name}</span>
                      <span aria-hidden="true" style={{ flex: 1, height: "8px", borderRadius: "4px", background: "rgba(230,225,215,.06)" }}><span style={{ display: "block", height: "100%", width: `${Math.round(auroc * 100)}%`, background: signalTone(auroc) }} /></span>
                      <span style={{ width: "44px", textAlign: "right", font: `500 10px ${font.mono}` }}>{auroc.toFixed(3)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : <p style={{ color: color.textDim }}>Unavailable</p>}
        </article>

        <article style={cardStyle}>
          <MicroLabel tone={color.accent}>Target checks</MicroLabel>
          <div style={{ marginTop: "10px" }}>
            {checks.map(({ name, metric }) => (
              <div key={name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", padding: "11px 0", borderBottom: `1px solid ${color.lineSoft}` }}>
                <span style={{ font: `400 12px ${font.sans}` }}>{name}</span>
                <span style={{ font: `500 11px ${font.mono}`, color: metric?.met === false ? color.fail : metric?.met ? color.pass : color.textDim }}>
                  {metric ? `${metricText(metric)} · ${metric.met === null ? "N/A" : metric.met ? "MET" : "MISS"}` : "Unavailable"}
                </span>
              </div>
            ))}
          </div>
        </article>

        <article style={cardStyle}>
          <MicroLabel tone={color.accent}>Certified grounding control</MicroLabel>
          <p style={{ font: `700 18px ${font.mono}`, color: conformal ? color.text : color.textDim }}>
            {conformal ? `${(conformal.escape_rate * 100).toFixed(2)}% escape / ${(conformal.intervention_rate * 100).toFixed(2)}% intervention` : "Unavailable"}
          </p>
          <p style={{ margin: 0, font: `400 11px/1.6 ${font.sans}`, color: color.textFaint }}>
            Escape and intervention are shown together because a zero escape rate at 100% intervention is not evidence of usable selectivity.
          </p>
        </article>

        <article style={cardStyle}>
          <MicroLabel tone={color.accent}>Live economics</MicroLabel>
          {economics?.available ? (
            <p style={{ font: `700 18px ${font.mono}` }}>
              Net ₹{economics.net_value_inr?.toLocaleString("en-IN")}
              {economics.net_value_ci_inr ? ` · CI ₹${economics.net_value_ci_inr[0].toLocaleString("en-IN")}–₹${economics.net_value_ci_inr[1].toLocaleString("en-IN")}` : ""}
            </p>
          ) : <p style={{ color: color.textDim }}>Unavailable{economics?.reason ? ` — ${economics.reason}` : ""}</p>}
        </article>

        <article style={cardStyle}>
          <MicroLabel tone={color.accent}>Lane C fairness monitor</MicroLabel>
          {laneC && laneC.n_pairs > 0 ? (
            <p style={{ font: `700 18px ${font.mono}` }}>{laneC.n_pairs} pairs · e-value {laneC.e_value.e_value?.toFixed(3) ?? "unavailable"}</p>
          ) : <p style={{ color: color.textDim }}>Unavailable — No live fairness observations.</p>}
        </article>

        <article style={cardStyle}>
          <MicroLabel tone={color.accent}>Measured action latency</MicroLabel>
          {bundle.latency?.length ? bundle.latency.slice(0, 3).map((row) => (
            <p key={`${row.action}-${row.model}`} style={{ margin: "12px 0 0", font: `500 11px ${font.mono}` }}>
              {row.action} · {(row.median_ms / 1000).toFixed(1)} s median · {row.runs} runs
            </p>
          )) : <p style={{ color: color.textDim }}>Unavailable</p>}
        </article>
      </div>
    </section>
  );
}
