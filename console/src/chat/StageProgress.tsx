import { useState } from "react";

import { STAGES } from "../theater/stages";
import { color, font, radius } from "../theater/tokens";
import { MicroLabel } from "../theater/primitives";

/**
 * The chat-side view of a run: the seven real Interlock stages, in order, with
 * the one in flight marked. It is the same information the stage view shows,
 * compressed to the size a chat transcript can carry.
 */
export function StageProgress({
  stage,
  status,
  durationMs,
  onSeeItLive,
}: {
  stage: number;
  status: "streaming" | "complete" | "failed";
  durationMs: number | null;
  onSeeItLive: () => void;
}) {
  const running = status === "streaming";
  const [open, setOpen] = useState(running);

  const headline = running
    ? `${STAGES[stage].title.toLowerCase()} · stage ${stage + 1} of ${STAGES.length}`
    : status === "failed"
      ? "the stream did not finish"
      : `checked in ${((durationMs ?? 0) / 1000).toFixed(2)} s`;

  return (
    <div
      style={{
        border: `1px solid ${color.line}`,
        borderRadius: radius.panel,
        background: "rgba(230,225,215,.02)",
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 14px" }}>
        <span
          aria-hidden="true"
          style={{
            width: "7px",
            height: "7px",
            borderRadius: "50%",
            flex: "none",
            background: status === "failed" ? color.fail : running ? color.accent : color.pass,
            animation: running ? "ilPulse 1.2s ease-in-out infinite" : "none",
          }}
        />
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          style={{
            flex: 1,
            minWidth: 0,
            textAlign: "left",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            padding: 0,
            font: `400 12px ${font.sans}`,
            color: color.textDim,
          }}
        >
          Interlock · {headline}
          <span aria-hidden="true" style={{ marginLeft: "8px", color: color.textFaint }}>
            {open ? "▾" : "▸"}
          </span>
        </button>
        <button
          type="button"
          onClick={onSeeItLive}
          style={{
            flex: "none",
            padding: "6px 12px",
            borderRadius: radius.button,
            border: `1px solid ${color.accent}`,
            background: "rgba(200,180,160,.1)",
            color: color.accent,
            cursor: "pointer",
            font: `600 11px ${font.sans}`,
            whiteSpace: "nowrap",
          }}
        >
          See it live →
        </button>
      </div>

      {open ? (
        <ol
          style={{
            listStyle: "none",
            margin: 0,
            padding: "4px 14px 12px",
            display: "flex",
            flexDirection: "column",
            gap: "6px",
          }}
        >
          {STAGES.map((item, index) => {
            const done = index < stage || (!running && index <= stage);
            const current = running && index === stage;
            return (
              <li
                key={item.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  font: `400 11px ${font.sans}`,
                  color: current ? color.text : done ? color.textDim : color.textMute,
                }}
              >
                <span style={{ font: `500 9px ${font.mono}`, color: color.textFaint, width: "16px" }}>
                  {item.n}
                </span>
                <span
                  aria-hidden="true"
                  style={{
                    width: "5px",
                    height: "5px",
                    borderRadius: "50%",
                    background: current ? color.accent : done ? color.pass : "rgba(230,225,215,.16)",
                    animation: current ? "ilPulse 1.2s ease-in-out infinite" : "none",
                  }}
                />
                <span>{item.title}</span>
                <span style={{ font: `400 10px ${font.mono}`, color: color.textFaint }}>{item.sub}</span>
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}

/** The action Interlock took, stamped on the answer the way the stage view does. */
export function ActionStamp({ stamp, tone }: { stamp: string; tone: string }) {
  return (
    <span
      style={{
        padding: "5px 10px",
        borderRadius: radius.stamp,
        border: `1px solid ${tone}`,
        color: tone,
        font: `700 9px ${font.mono}`,
        letterSpacing: ".14em",
        whiteSpace: "nowrap",
      }}
    >
      {stamp}
    </span>
  );
}

export function TurnMeta({ children }: { children: React.ReactNode }) {
  return <MicroLabel style={{ letterSpacing: ".12em" }}>{children}</MicroLabel>;
}
