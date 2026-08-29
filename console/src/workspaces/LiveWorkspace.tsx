import { useEffect, useState, type FormEvent } from "react";

import type { Action } from "../domain/contracts";
import type { RequestTrace } from "../state/consoleStore";

const actions: Action[] = [
  "L0_pass",
  "L1_annotate",
  "L2_repair",
  "L3_reroute",
  "L4_hold",
  "L5_block",
];

const actionCopy: Record<Action, { short: string; description: string }> = {
  L0_pass: { short: "L0 pass", description: "Ship unchanged" },
  L1_annotate: { short: "L1 annotate", description: "Attach evidence" },
  L2_repair: { short: "L2 repair", description: "Repair the sentence" },
  L3_reroute: { short: "L3 reroute", description: "Retrieve and regenerate" },
  L4_hold: { short: "L4 hold", description: "Require review" },
  L5_block: { short: "L5 block", description: "Stop release" },
};

interface LiveWorkspaceProps {
  trace: RequestTrace | null;
  prompt: string;
  scenario: "clean" | "scene1" | "held" | "blocked";
  busy: boolean;
  upload?: { filename: string; fragmentCount: number } | null;
  uploadBusy?: boolean;
  uploadError?: string | null;
  onUpload?: (file: File) => void;
  onClearUpload?: () => void;
  onPromptChange: (value: string) => void;
  onScenarioChange: (value: "clean" | "scene1" | "held" | "blocked") => void;
  onSubmit: () => void;
}

function money(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

function number(value: number): string {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
}

export function LiveWorkspace({
  trace,
  prompt,
  scenario,
  busy,
  upload = null,
  uploadBusy = false,
  uploadError = null,
  onUpload,
  onClearUpload,
  onPromptChange,
  onScenarioChange,
  onSubmit,
}: LiveWorkspaceProps) {
  const [selectedSentenceIdx, setSelectedSentenceIdx] = useState<number | null>(null);
  const sentenceKey = trace?.sentenceOrder.join(",") ?? "";
  const latestSentenceIdx = trace?.sentenceOrder.at(-1) ?? null;
  const effectiveSentenceIdx = selectedSentenceIdx !== null && trace?.sentenceOrder.includes(selectedSentenceIdx)
    ? selectedSentenceIdx
    : latestSentenceIdx;
  const sentence = effectiveSentenceIdx === null ? null : trace?.sentences[effectiveSentenceIdx] ?? null;
  const decision = sentence?.decisions.at(-1) ?? null;
  const detail = decision ? sentence?.decisionDetails[decision.decision_id] ?? null : null;

  useEffect(() => {
    setSelectedSentenceIdx(latestSentenceIdx);
  }, [trace?.requestId, sentenceKey, latestSentenceIdx]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (prompt.trim() && !busy) onSubmit();
  };

  return (
    <div className="live-grid">
      <section className="conversation-panel" aria-labelledby="conversation-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Bank support / guarded stream</p>
            <h2 id="conversation-title">Customer conversation</h2>
          </div>
          <span className={busy ? "stream-status live" : "stream-status"}>
            {busy ? "Streaming" : trace?.status ?? "Ready"}
          </span>
        </div>

        {trace?.degraded && (
          <p className="degraded-notice" role="status">
            Degraded checking · one or more calibrated detectors were unavailable.
          </p>
        )}

        {trace && trace.sentenceOrder.length > 0 && (
          <nav className="sentence-timeline" aria-label="Sentence timeline">
            {trace.sentenceOrder.map((sentenceIdx) => (
              <button
                type="button"
                key={sentenceIdx}
                onClick={() => setSelectedSentenceIdx(sentenceIdx)}
                aria-current={effectiveSentenceIdx === sentenceIdx ? "step" : undefined}
                aria-label={`Sentence ${sentenceIdx + 1}`}
              >
                <span aria-hidden="true">{String(sentenceIdx + 1).padStart(2, "0")}</span>
                Sentence {sentenceIdx + 1}
              </button>
            ))}
          </nav>
        )}

        <div className="transcript" aria-live="polite">
          {trace ? (
            <>
              <article className="message customer-message">
                <span>Customer</span>
                <p>{trace.prompt || prompt}</p>
              </article>
              <article className="message assistant-message">
                <span>Bank assistant · Interlock guarded</span>
                {trace.assistantText ? (
                  <p>{trace.assistantText}</p>
                ) : decision?.action === "L5_block" ? (
                  <div className="blocked-copy">
                    <strong>Blocked before release</strong>
                    <span>No response content reached the customer.</span>
                  </div>
                ) : (
                  <p className="muted">Waiting for model output…</p>
                )}
                {trace.error && <p className="inline-error">{trace.error} Partial output was preserved.</p>}
              </article>
            </>
          ) : (
            <div className="empty-state">
              <strong>Start with a recorded banking scene</strong>
              <span>The stream and its decision trail will arrive together.</span>
            </div>
          )}
        </div>

        <form className="composer" onSubmit={submit}>
          <label htmlFor="scenario">Recorded scene</label>
          <select
            id="scenario"
            value={scenario}
            onChange={(event) => onScenarioChange(event.target.value as LiveWorkspaceProps["scenario"])}
            disabled={busy}
          >
            <option value="scene1">Invented loan clause · L2 repair</option>
            <option value="clean">Branch hours · L0 pass</option>
            <option value="held">Untrusted claim · L4 hold</option>
            <option value="blocked">Canary leak · L5 block</option>
          </select>
          <div className="document-attachment">
            <label className="attachment-action" htmlFor="customer-document">
              {uploadBusy ? "Reading document…" : "Attach customer document"}
            </label>
            <input
              id="customer-document"
              type="file"
              accept=".txt,.md,.json,.pdf,text/plain,application/pdf,application/json"
              disabled={busy || uploadBusy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onUpload?.(file);
                event.target.value = "";
              }}
            />
            {upload && (
              <div className="attached-document">
                <span>
                  <strong>{upload.filename}</strong>
                  {upload.fragmentCount} untrusted context fragment
                  {upload.fragmentCount === 1 ? "" : "s"}
                </span>
                <button type="button" onClick={onClearUpload} disabled={busy}>Remove</button>
              </div>
            )}
            {uploadError && <p className="attachment-error" role="alert">{uploadError}</p>}
          </div>
          <label htmlFor="prompt">Customer message</label>
          <textarea
            id="prompt"
            value={prompt}
            onChange={(event) => onPromptChange(event.target.value)}
            rows={3}
          />
          <button className="primary-action" type="submit" disabled={busy || !prompt.trim()}>
            {busy ? "Watching decision…" : "Send through Interlock"}
          </button>
        </form>
      </section>

      <section className="decision-panel" aria-labelledby="decision-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Expected-loss control plane</p>
            <h2 id="decision-title">Decision trail</h2>
          </div>
          {decision && <span className={`action-stamp ${decision.action}`}>{actionCopy[decision.action].short}</span>}
        </div>

        {trace?.stakes ? (
          <>
            <div className="stakes-strip">
              <div><span>Impact</span><strong>{money(trace.stakes.impact_inr)}</strong></div>
              <div><span>Reversibility</span><strong>{trace.stakes.reversibility}</strong></div>
              <div><span>Domain</span><strong>{trace.stakes.domain}</strong></div>
              <div><span>Model served</span><strong>{trace.stakes.model_served ?? "Not reported"}</strong></div>
            </div>

            <div className="decision-body">
              <ol className="decision-rail" aria-label="Intervention ladder">
                {actions.map((action) => (
                  <li key={action} data-selected={decision?.action === action ? "true" : "false"}>
                    <span>{actionCopy[action].short}</span>
                    <small>{actionCopy[action].description}</small>
                  </li>
                ))}
              </ol>

              <div className="decision-evidence">
                <section className="signal-list" aria-labelledby="signals-title">
                  <div className="section-label" id="signals-title">Calibrated signals</div>
                  {sentence?.signals.length ? sentence.signals.map((signal, index) => (
                    <div className="signal-row" key={`${signal.sentence_idx}-${signal.name}-${index}`}>
                      <span>{signal.name.replaceAll(".", " / ")}</span>
                      <strong>{signal.prob === null ? "Unavailable" : `${Math.round(signal.prob * 100)}%`}</strong>
                      <i style={{ "--probability": `${(signal.prob ?? 0) * 100}%` } as React.CSSProperties} />
                    </div>
                  )) : <p className="muted">No calibrated signals were emitted.</p>}
                </section>

                {decision && (
                  <section className="decision-summary">
                    <div>
                      <span>Chosen loss</span>
                      <strong>{number(decision.chosen_loss)}</strong>
                    </div>
                    <div>
                      <span>Runner-up</span>
                      <strong>{decision.runner_up ? actionCopy[decision.runner_up].short : "None"}</strong>
                    </div>
                    <div>
                      <span>Margin</span>
                      <strong>{number(decision.margin ?? 0)}</strong>
                    </div>
                  </section>
                )}
                {decision?.hard_rule && <p className="hard-rule">Hard rule: {decision.hard_rule}</p>}
              </div>
            </div>

            {detail ? (
              <section className="loss-section">
                <div className="section-label">Complete expected-loss table · INR</div>
                <div className="table-scroll">
                  <table className="loss-table">
                    <thead><tr><th>Action</th><th>Residual harm</th><th>Nuisance</th><th>Compute</th><th>Latency</th><th>Total</th></tr></thead>
                    <tbody>
                      {detail.loss_table.map((row) => (
                        <tr key={row.action} className={!row.available ? "unavailable" : row.action === detail.action ? "chosen" : undefined}>
                          <th>{actionCopy[row.action].short}{row.unavailable_reason && <small>{row.unavailable_reason}</small>}</th>
                          <td>{number(row.residual_harm)}</td><td>{number(row.nuisance)}</td><td>{number(row.compute)}</td><td>{number(row.latency)}</td><td>{number(row.total)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {detail.why.length > 0 && <p className="why-line">Why: {detail.why.join(" · ")}</p>}
              </section>
            ) : decision ? (
              <p className="projection-wait">The complete loss table is being committed to the ledger.</p>
            ) : null}

            {decision && (
              <section className="before-after">
                <article>
                  <span>What would have shipped</span>
                  <p>{decision.counterfactual ?? "The original text was safe to ship unchanged."}</p>
                </article>
                <article>
                  <span>Shipped after Interlock</span>
                  <p>{trace.assistantText || "No content released."}</p>
                </article>
              </section>
            )}
          </>
        ) : (
          <div className="empty-state decision-empty">
            <strong>No decision yet</strong>
            <span>Stakes arrive before the first model token.</span>
          </div>
        )}
      </section>
    </div>
  );
}
