import type { HoldProjection } from "../domain/contracts";
import { MicroLabel } from "./primitives";
import { color, font, radius } from "./tokens";

/** Reviews workspace. Every card comes from the durable gateway projection. */

export interface HoldCard {
  id: string;
  kind: "response" | "tool_call";
  title: string;
  summary: string;
  tool: string;
  sentence: string;
  impact: string;
  sla: string;
  slaExpired: boolean;
  evidence: string[];
  flaggedSpan: string | null;
  hasToken: boolean;
}

function formatSla(hold: HoldProjection): { text: string; expired: boolean } {
  if (hold.expired) return { text: "SLA expired", expired: true };
  if (hold.sla_deadline_ts === null) return { text: "No SLA deadline", expired: false };
  const remainingMs = hold.sla_deadline_ts * 1000 - Date.now();
  if (remainingMs <= 0) return { text: "SLA expired", expired: true };
  const hours = Math.floor(remainingMs / 3_600_000);
  const minutes = Math.floor((remainingMs % 3_600_000) / 60_000);
  return { text: `SLA ${hours}h ${minutes}m`, expired: false };
}

export function toHoldCard(hold: HoldProjection, hasToken: boolean): HoldCard {
  const sla = formatSla(hold);
  const impact = hold.payload?.["impact_inr"];
  return {
    id: hold.hold_id,
    kind: hold.kind,
    title: hold.reason,
    summary: typeof hold.payload?.["summary"] === "string" ? (hold.payload["summary"] as string) : hold.reason,
    tool: hold.tool ?? "—",
    sentence: hold.sentence_idx === null || hold.sentence_idx === undefined ? "—" : `idx ${hold.sentence_idx}`,
    impact: typeof impact === "number" ? `₹${impact.toLocaleString("en-IN")}` : "—",
    sla: sla.text,
    slaExpired: sla.expired,
    evidence: hold.evidence,
    flaggedSpan: hold.flagged_span,
    hasToken,
  };
}

export function ReviewsPanel({
  holds,
  loading,
  error,
  resolvingHoldId,
  onApprove,
  onReject,
  onRefresh,
}: {
  holds: HoldCard[];
  loading: boolean;
  error: string | null;
  resolvingHoldId: string | null;
  onApprove: (holdId: string) => void;
  onReject: (holdId: string) => void;
  onRefresh: () => void;
}) {
  return (
    <section
      style={{
        position: "relative",
        zIndex: 2,
        maxWidth: "1180px",
        margin: "0 auto",
        padding: "34px 34px 60px",
        display: "flex",
        flexDirection: "column",
        gap: "16px",
      }}
      aria-label="Pending reviews"
    >
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "16px" }}>
        <div>
          <MicroLabel tone={color.accent}>Operator workspace / 02</MicroLabel>
          <h2 style={{ margin: "10px 0 0", font: `600 34px/1.05 ${font.sans}`, letterSpacing: "-.03em" }}>
            Pending reviews
          </h2>
          <p style={{ margin: "12px 0 0", maxWidth: "600px", font: `400 14px/1.65 ${font.sans}`, color: color.textDim }}>
            {holds.length === 0
              ? "No holds are waiting on a human. The queue is a live durable projection."
              : `${holds.length} hold${holds.length === 1 ? " is" : "s are"} waiting on a human. Approval uses the initiating stream token; rejection never requires it.`}
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          style={{
            flex: "none",
            padding: "9px 16px",
            borderRadius: radius.button,
            border: `1px solid ${color.line}`,
            background: "transparent",
            color: color.text,
            cursor: "pointer",
            font: `500 9px ${font.mono}`,
            letterSpacing: ".14em",
            textTransform: "uppercase",
          }}
        >
          {loading ? "Refreshing…" : "Refresh queue"}
        </button>
      </div>

      {error ? (
        <p role="alert" style={{ margin: 0, font: `400 12px ${font.mono}`, color: color.fail }}>
          {error}
        </p>
      ) : null}

      {holds.length === 0 && !loading ? (
        <p style={{ margin: 0, padding: "22px", border: `1px solid ${color.line}`, borderRadius: radius.card, color: color.textDim }}>
          No pending holds.
        </p>
      ) : null}

      {holds.map((card, index) => (
        <HoldCardView
          key={card.id}
          card={card}
          dimmed={index > 0}
          resolving={resolvingHoldId === card.id}
          onApprove={() => onApprove(card.id)}
          onReject={() => onReject(card.id)}
        />
      ))}
    </section>
  );
}

function HoldCardView({
  card,
  dimmed,
  resolving,
  onApprove,
  onReject,
}: {
  card: HoldCard;
  dimmed: boolean;
  resolving: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const approvalAvailable = card.hasToken && !card.slaExpired;
  const facts = [
    { label: "Hold id", value: card.id },
    { label: "Tool", value: card.tool },
    { label: "Sentence", value: card.sentence },
    { label: "Impact", value: card.impact },
  ];

  return (
    <article
      style={{
        borderRadius: radius.card,
        border: `1px solid ${card.slaExpired ? color.line : "rgba(217,165,92,.28)"}`,
        background: color.bgPanel,
        opacity: dimmed ? 0.92 : 1,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: "16px",
          padding: "18px 20px",
          borderBottom: `1px solid ${color.lineSoft}`,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <MicroLabel tone={card.slaExpired ? color.textFaint : color.warn}>
            Hold · {card.kind === "tool_call" ? "tool_call" : "response"}
          </MicroLabel>
          <h3 style={{ margin: "10px 0 0", font: `600 20px ${font.sans}` }}>{card.title}</h3>
          <p style={{ margin: "10px 0 0", maxWidth: "640px", font: `400 13px/1.6 ${font.sans}`, color: color.textDim }}>
            {card.summary}
          </p>
        </div>
        <span
          style={{
            flex: "none",
            padding: "6px 10px",
            borderRadius: radius.stamp,
            border: `1px solid ${card.slaExpired ? "rgba(217,112,95,.5)" : "rgba(154,209,127,.4)"}`,
            color: card.slaExpired ? color.fail : color.pass,
            font: `500 9px ${font.mono}`,
            letterSpacing: ".12em",
            textTransform: "uppercase",
          }}
        >
          {card.sla}
        </span>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px,1fr))" }}>
        {facts.map((fact, index) => (
          <div
            key={fact.label}
            style={{
              padding: "13px 20px",
              borderLeft: index === 0 ? "none" : `1px solid ${color.lineSoft}`,
              borderBottom: `1px solid ${color.lineSoft}`,
            }}
          >
            <MicroLabel>{fact.label}</MicroLabel>
            <div style={{ marginTop: "6px", font: `500 11px ${font.mono}` }}>{fact.value}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px,1fr))" }}>
        <div style={{ padding: "16px 20px" }}>
          <MicroLabel tone={color.accent}>Evidence</MicroLabel>
          <ul style={{ margin: "10px 0 0", paddingLeft: "18px", font: `400 12px/1.8 ${font.sans}`, color: color.textDim }}>
            {card.evidence.length > 0 ? (
              card.evidence.map((line) => <li key={line}>{line}</li>)
            ) : (
              <li>no evidence lines were recorded on this hold</li>
            )}
          </ul>
        </div>
        <div style={{ padding: "16px 20px", borderLeft: `1px solid ${color.lineSoft}` }}>
          <MicroLabel>Flagged span</MicroLabel>
          <pre
            style={{
              margin: "10px 0 0",
              padding: "12px",
              borderRadius: radius.button,
              background: color.bgBase,
              font: `400 11px/1.6 ${font.mono}`,
              color: color.textSoft,
              whiteSpace: "pre-wrap",
            }}
          >
            {card.flaggedSpan ?? "— no span was captured —"}
          </pre>
        </div>
      </div>

      <footer
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "16px",
          padding: "14px 20px",
          borderTop: `1px solid ${color.lineSoft}`,
          background: "rgba(230,225,215,.02)",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: "9px" }}>
          <span
            aria-hidden="true"
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              background: card.hasToken ? color.pass : color.fail,
            }}
          />
          <span style={{ font: `400 10px ${font.mono}`, color: card.hasToken ? color.pass : color.fail }}>
            {card.hasToken ? "resume token held" : "no resume token in this session"}
          </span>
        </span>
        <span style={{ display: "flex", gap: "10px" }}>
          <button
            type="button"
            onClick={onReject}
            disabled={resolving}
            style={{
              padding: "9px 16px",
              borderRadius: radius.button,
              cursor: resolving ? "not-allowed" : "pointer",
              border: `1px solid ${resolving ? color.line : "rgba(217,112,95,.5)"}`,
              background: "transparent",
              color: resolving ? color.textFaint : color.fail,
              font: `600 12px ${font.sans}`,
            }}
          >
            Reject
          </button>
          <button
            type="button"
            onClick={onApprove}
            disabled={!approvalAvailable || resolving}
            style={{
              padding: "9px 16px",
              borderRadius: radius.button,
              cursor: approvalAvailable && !resolving ? "pointer" : "not-allowed",
              border: `1px solid ${approvalAvailable ? color.pass : color.line}`,
              background: approvalAvailable ? color.pass : "transparent",
              color: approvalAvailable ? color.onAccent : color.textFaint,
              font: `600 12px ${font.sans}`,
            }}
          >
            {resolving ? "Working…" : "Approve release"}
          </button>
        </span>
      </footer>
    </article>
  );
}
