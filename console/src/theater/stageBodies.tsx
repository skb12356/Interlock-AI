import { LADDER, type Level } from "./stages";
import { LANE_C_CARDS, type Scene } from "./scenes";
import { color, font, radius, rgba, TONE, type SurfaceTone } from "./tokens";
import {
  MicroLabel,
  dotStyle,
  latencyStyle,
  metaStyle,
  nodeBoxStyle,
  nodeValueText,
  panelStyle,
  valueStyle,
} from "./primitives";
import {
  formatMoney,
  type EngineSettings,
  type LadderRowState,
  type NodeRuntimeState,
  type TraceUiState,
} from "./traceEngine";

interface StageProps {
  scene: Scene;
  state: TraceUiState;
  settings: EngineSettings;
}

/* ---------- 01 · Lane A ---------- */

export function LaneAStage({ scene, state }: StageProps) {
  const strip = [
    { label: "Stakes", value: `₹${scene.stakes.impact.toLocaleString("en-IN")}` },
    { label: "Reversibility", value: scene.stakes.rev },
    { label: "Domain", value: scene.stakes.domain },
    { label: "Route", value: scene.stakes.model, tone: color.accent },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px,1fr))", gap: "12px" }}>
        {scene.laneA.map((node, index) => {
          const nodeState = state.nodeSt[`a${index}`];
          return (
            <div key={node.label} style={nodeBoxStyle(nodeState)}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px" }}>
                <span style={{ font: `600 12px ${font.sans}` }}>{node.label}</span>
                <span aria-hidden="true" style={dotStyle(nodeState)} />
              </div>
              <div
                style={{
                  marginTop: "10px",
                  display: "flex",
                  alignItems: "baseline",
                  justifyContent: "space-between",
                  gap: "10px",
                }}
              >
                <span style={valueStyle(nodeState)}>{nodeValueText(nodeState, node.v)}</span>
                {nodeState && nodeState !== "active" && node.ms !== null ? (
                  <span style={latencyStyle()}>{node.ms.toFixed(1)} ms</span>
                ) : null}
              </div>
              <p style={{ ...metaStyle(), margin: "8px 0 0" }}>{node.meta}</p>
            </div>
          );
        })}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px,1fr))",
          border: `1px solid ${color.line}`,
          borderRadius: radius.panel,
        }}
      >
        {strip.map((cell, index) => (
          <div
            key={cell.label}
            style={{
              padding: "13px 16px",
              borderLeft: index === 0 ? "none" : `1px solid ${color.lineSoft}`,
            }}
          >
            <MicroLabel>{cell.label}</MicroLabel>
            <div style={{ marginTop: "6px", font: `700 15px ${font.mono}`, color: cell.tone ?? color.text }}>
              {cell.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- 02 · Generation ---------- */

export function GenStage({ scene, state }: StageProps) {
  const tokens = Math.round(state.genText.length / 4);
  const totalTokens = Math.round(scene.gen.length / 4);
  const footer = [
    { label: "gate mode", value: scene.stakes.rev === "reversible" ? "unbuffered" : "buffered" },
    { label: "tokens", value: `${tokens} / ${totalTokens}` },
    { label: "release", value: "nothing released yet — the gate holds one sentence back" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
      <div style={panelStyle()}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "12px",
            padding: "12px 18px",
            borderBottom: `1px solid ${color.lineSoft}`,
            background: "rgba(230,225,215,.02)",
          }}
        >
          <MicroLabel>Draft · unmodified model</MicroLabel>
          <span style={{ font: `400 10px ${font.mono}`, color: color.accent }}>{scene.stakes.model}</span>
        </div>
        <div style={{ padding: "20px 22px", minHeight: "120px", font: `400 17px/1.75 ${font.sans}` }}>
          {state.genText}
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: "9px",
              height: "20px",
              marginLeft: "3px",
              verticalAlign: "-4px",
              background: color.accent,
              animation: "ilCaret 1s step-end infinite",
            }}
          />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px,1fr))", borderTop: `1px solid ${color.lineSoft}` }}>
          {footer.map((cell, index) => (
            <div
              key={cell.label}
              style={{
                padding: "12px 18px",
                borderLeft: index === 0 ? "none" : `1px solid ${color.lineSoft}`,
                font: `400 10px ${font.mono}`,
                color: color.textDim,
              }}
            >
              {cell.label} <span style={{ color: color.text }}>{cell.value}</span>
            </div>
          ))}
        </div>
      </div>
      <p style={{ margin: 0, maxWidth: "720px", font: `400 13px/1.6 ${font.sans}`, color: color.textDim }}>
        Interlock did not touch the model. It sits in front of it as an OpenAI-compatible proxy, so the draft you see
        is exactly what the upstream produced.
      </p>
    </div>
  );
}

/* ---------- 03 · Lane B ---------- */

export function LaneBStage({ scene, state }: StageProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "14px",
          padding: "11px 16px",
          borderRadius: radius.panel,
          border: "1px solid rgba(200,180,160,.28)",
          background: "rgba(200,180,160,.06)",
        }}
      >
        <MicroLabel tone={color.accent}>Generation still streaming</MicroLabel>
        <span
          aria-hidden="true"
          style={{ flex: 1, height: "2px", background: "rgba(230,225,215,.06)", overflow: "hidden" }}
        >
          <span
            style={{
              display: "block",
              width: "30%",
              height: "100%",
              background: color.accent,
              animation: "ilSweep 1.6s linear infinite",
            }}
          />
        </span>
        <span style={{ font: `400 10px ${font.mono}`, color: color.textDim }}>these three ran underneath it</span>
      </div>

      {scene.laneB.map((node, index) => {
        const nodeState = state.nodeSt[`b${index}`];
        return (
          <div key={node.label} style={nodeBoxStyle(nodeState, true)}>
            <span aria-hidden="true" style={dotStyle(nodeState)} />
            <span style={{ display: "flex", flexDirection: "column", gap: "4px", flex: 1, minWidth: 0 }}>
              <span style={{ font: `600 12px ${font.sans}` }}>{node.label}</span>
              <span style={metaStyle()}>{node.meta}</span>
            </span>
            <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "3px" }}>
              <span style={valueStyle(nodeState, 15)}>{nodeValueText(nodeState, node.v)}</span>
              {nodeState && nodeState !== "active" && node.ms !== null ? (
                <span style={latencyStyle()}>{node.ms} ms</span>
              ) : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- 04 · The ladder ---------- */

export function LadderStage({ scene, state, settings }: StageProps) {
  const maxCost = LADDER.reduce((max, row) => Math.max(max, scene.costs[row.lv]), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
        <MicroLabel>All six actions priced in parallel · expected loss</MicroLabel>
        <span style={{ font: `400 10px ${font.mono}`, color: color.textFaint }}>cheapest safe action wins</span>
      </div>

      {LADDER.map((row) => (
        <LadderRow
          key={row.lv}
          level={row.lv}
          name={row.name}
          desc={row.desc}
          cost={scene.costs[row.lv]}
          maxCost={maxCost}
          rowState={state.ladderSt[row.lv]}
          chosen={state.chosenShown && scene.chosen === row.lv}
          dimmed={state.chosenShown && scene.chosen !== row.lv}
          tone={scene.tone}
          currency={settings.currency}
          unavailableReason={
            scene.costMeta?.[row.lv] && scene.costMeta[row.lv]?.available === false
              ? (scene.costMeta[row.lv]?.reason ?? "unavailable")
              : null
          }
        />
      ))}

      <p style={{ margin: "6px 0 0", font: `400 12px/1.6 ${font.mono}`, color: color.textDim }}>
        {state.chosenShown
          ? scene.why
          : "pricing residual harm · nuisance · compute · latency for all six…"}
      </p>
    </div>
  );
}

function LadderRow({
  level,
  name,
  desc,
  cost,
  maxCost,
  rowState,
  chosen,
  dimmed,
  tone,
  currency,
  unavailableReason,
}: {
  level: Level;
  name: string;
  desc: string;
  cost: number;
  maxCost: number;
  rowState: LadderRowState | undefined;
  chosen: boolean;
  dimmed: boolean;
  tone: SurfaceTone;
  currency: EngineSettings["currency"];
  /** Live loss tables can mark an action unavailable; the row says why. */
  unavailableReason?: string | null;
}) {
  const priced = rowState === "priced";
  const tint = TONE[tone];
  const barWidth = priced ? `${Math.max(3, (cost / maxCost) * 100)}%` : "0%";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "10px 16px",
        padding: "13px 18px",
        borderRadius: radius.panel,
        border: `1px solid ${chosen ? rgba(tint, 0.6) : color.line}`,
        background: chosen ? rgba(tint, 0.1) : color.bgPanel,
        boxShadow: chosen ? `0 0 34px ${rgba(tint, 0.22)}` : "none",
        opacity: dimmed ? 0.42 : 1,
        animation: rowState === "pricing" ? "ilPulse 1.1s ease-in-out infinite" : "none",
        transition: "all .5s ease",
      }}
    >
      <span style={{ width: "32px", font: `700 15px ${font.mono}`, color: chosen ? tint : color.text }}>{level}</span>
      <span style={{ minWidth: "150px", display: "flex", flexDirection: "column", gap: "3px" }}>
        <span style={{ font: `600 13px ${font.sans}` }}>{name}</span>
        <span style={{ font: `400 10px ${font.mono}`, color: color.textMeta }}>
          {unavailableReason ? `unavailable · ${unavailableReason}` : desc}
        </span>
      </span>
      <span
        aria-hidden="true"
        style={{ flex: "1 1 120px", height: "6px", borderRadius: "3px", background: "rgba(230,225,215,.06)" }}
      >
        <span
          style={{
            display: "block",
            height: "100%",
            width: barWidth,
            borderRadius: "3px",
            background: chosen ? tint : "rgba(230,225,215,.22)",
            transition: "width .7s cubic-bezier(.2,.8,.2,1)",
          }}
        />
      </span>
      <span
        style={{
          width: "86px",
          textAlign: "right",
          font: `700 15px ${font.mono}`,
          color: chosen ? tint : color.text,
        }}
      >
        {unavailableReason ? "—" : priced ? formatMoney(cost, currency) : "· · ·"}
      </span>
      <span
        style={{
          width: "74px",
          textAlign: "right",
          font: `700 9px ${font.mono}`,
          letterSpacing: ".12em",
          textTransform: "uppercase",
          color: chosen ? tint : color.textFaint,
        }}
      >
        {unavailableReason
          ? "unavailable"
          : chosen
            ? "Chosen"
            : dimmed
              ? "considered"
              : priced
                ? "priced"
                : "pricing"}
      </span>
    </div>
  );
}

/* ---------- 05 · Commit gate ---------- */

export function GateStage({ scene, state }: StageProps) {
  const settled = state.gateStep === 1;
  const gateTint = TONE[scene.gate.tone];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px,1fr))", gap: "12px" }}>
        <div
          style={{
            border: "1px solid rgba(154,209,127,.3)",
            background: "rgba(154,209,127,.05)",
            borderRadius: radius.panel,
            padding: "16px 18px",
          }}
        >
          <MicroLabel tone={color.pass}>Committed · already visible</MicroLabel>
          <p style={{ margin: "10px 0 0", font: `400 15px/1.65 ${font.sans}` }}>{scene.gate.committed}</p>
        </div>
        <div
          style={{
            border: `1px solid ${rgba(gateTint, 0.3)}`,
            background: rgba(gateTint, 0.05),
            borderRadius: radius.panel,
            padding: "16px 18px",
            animation: settled ? "none" : "ilPulse 1.4s ease-in-out infinite",
          }}
        >
          <MicroLabel tone={gateTint}>{scene.gate.title}</MicroLabel>
          <p style={{ margin: "10px 0 0", font: `400 15px/1.65 ${font.sans}`, color: color.textSoft }}>
            {scene.gate.buffered}
          </p>
        </div>
      </div>

      <div
        style={{
          padding: "15px 18px",
          borderRadius: radius.panel,
          border: `1px solid ${settled ? rgba(gateTint, 0.4) : color.line}`,
          background: settled ? rgba(gateTint, 0.07) : "transparent",
          color: settled ? color.text : color.textDim,
          font: `500 13px/1.6 ${font.sans}`,
          animation: settled ? "ilRise .5s ease-out" : "none",
        }}
      >
        {settled ? scene.gate.verdict : "holding one sentence behind generation…"}
      </div>
    </div>
  );
}

/* ---------- 06 · Release ---------- */

function formatCount(value: number, card: Scene["summary"][number]): string {
  const decimals = card.dec ?? 0;
  const shown = decimals > 0 ? value.toFixed(decimals) : Math.round(value).toLocaleString("en-IN");
  return `${card.prefix ?? ""}${shown}${card.suffix ?? ""}`;
}

export function ReleaseStage({ scene, state }: StageProps) {
  const stampTint = TONE[scene.stampTone];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div
        style={{
          borderRadius: radius.card,
          border: `1px solid ${rgba(stampTint, 0.34)}`,
          background: rgba(stampTint, 0.05),
          padding: "20px 22px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
          <MicroLabel>What the customer sees</MicroLabel>
          <span
            style={{
              padding: "6px 11px",
              borderRadius: radius.stamp,
              border: `1px solid ${rgba(stampTint, 0.5)}`,
              color: stampTint,
              font: `700 10px ${font.mono}`,
              letterSpacing: ".14em",
            }}
          >
            {scene.stamp}
          </span>
        </div>
        <p style={{ margin: "14px 0 0", font: `400 18px/1.7 ${font.sans}` }}>{scene.final}</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px,1fr))", gap: "12px" }}>
        {scene.summary.map((card, index) => (
          <div key={card.label} style={panelStyle({ padding: "15px 17px" })}>
            <div
              style={{
                font: `700 25px ${font.mono}`,
                letterSpacing: "-.02em",
                color: index === 3 ? color.pass : color.text,
              }}
            >
              {formatCount(state.counts[index] ?? 0, card)}
            </div>
            <MicroLabel style={{ marginTop: "8px" }}>{card.label}</MicroLabel>
            <p style={{ margin: "8px 0 0", font: `400 10px/1.45 ${font.sans}`, color: color.textFaint }}>{card.note}</p>
          </div>
        ))}
      </div>

      <div
        style={{
          border: "1px dashed rgba(217,112,95,.34)",
          background: "rgba(217,112,95,.05)",
          borderRadius: radius.panel,
          padding: "14px 17px",
        }}
      >
        <MicroLabel tone={color.fail}>What would have shipped without Interlock</MicroLabel>
        <p style={{ margin: "10px 0 0", font: `400 14px/1.65 ${font.sans}`, color: color.textSoft }}>
          {scene.counterfactual}
        </p>
      </div>
    </div>
  );
}

/* ---------- 07 · Lane C ---------- */

export function LaneCStage({
  state,
  onReplay,
  onReset,
}: StageProps & { onReplay: () => void; onReset: () => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px,1fr))", gap: "12px" }}>
        {LANE_C_CARDS.map((card, index) => {
          const nodeState: NodeRuntimeState | undefined = state.nodeSt[`c${index}`];
          const resolved = nodeState === "pass";
          return (
            <div
              key={card.label}
              style={{
                border: `1px dashed ${resolved ? rgba(color.pass, 0.34) : color.line}`,
                background: resolved ? rgba(color.pass, 0.05) : color.bgPanel,
                borderRadius: radius.panel,
                padding: "15px 17px",
                transition: "all .35s ease",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px" }}>
                <span style={{ font: `600 12px ${font.sans}` }}>{card.label}</span>
                <span aria-hidden="true" style={dotStyle(nodeState)} />
              </div>
              <div style={{ marginTop: "10px", ...valueStyle(nodeState, 15) }}>{resolved ? card.value : "…"}</div>
              <p style={{ ...metaStyle(), margin: "8px 0 0" }}>{card.meta}</p>
            </div>
          );
        })}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "14px",
          border: "1px dashed rgba(230,225,215,.16)",
          borderRadius: radius.panel,
          padding: "14px 18px",
        }}
      >
        <span aria-hidden="true" style={{ font: `400 16px ${font.mono}`, color: color.accent }}>
          ⟲
        </span>
        <p style={{ margin: 0, flex: 1, font: `400 13px/1.6 ${font.sans}`, color: color.textDim }}>
          Recalibrated thresholds are written back to the control plane for the next request. Lane C never blocked this
          one.
        </p>
      </div>

      <div style={{ display: "flex", gap: "10px" }}>
        <button
          type="button"
          onClick={onReplay}
          style={{
            padding: "10px 16px",
            borderRadius: radius.button,
            border: "none",
            cursor: "pointer",
            background: color.accent,
            color: color.onAccent,
            font: `600 12px ${font.sans}`,
          }}
        >
          ↻ Replay this trace
        </button>
        <button
          type="button"
          onClick={onReset}
          style={{
            padding: "10px 16px",
            borderRadius: radius.button,
            border: `1px solid ${color.line}`,
            cursor: "pointer",
            background: "transparent",
            color: color.text,
            font: `600 12px ${font.sans}`,
          }}
        >
          New prompt
        </button>
      </div>
    </div>
  );
}
