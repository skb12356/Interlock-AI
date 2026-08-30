import type { CSSProperties, ReactNode } from "react";

import { color, font, rgba, TONE, type Tone } from "./tokens";
import type { NodeRuntimeState } from "./traceEngine";

export const microLabelStyle: CSSProperties = {
  font: `500 9px ${font.mono}`,
  letterSpacing: ".16em",
  textTransform: "uppercase",
  color: color.textFaint,
};

export function MicroLabel({
  children,
  tone,
  style,
}: {
  children: ReactNode;
  tone?: string;
  style?: CSSProperties;
}) {
  return <div style={{ ...microLabelStyle, ...(tone ? { color: tone } : null), ...style }}>{children}</div>;
}

const IDLE_DOT = "rgba(230,225,215,.18)";

export function toneOf(state: NodeRuntimeState | undefined): string {
  if (!state) return IDLE_DOT;
  return TONE[state as Tone] ?? IDLE_DOT;
}

/** Node card: neutral at rest, tinted once a check has resolved, ringing while in flight. */
export function nodeBoxStyle(state: NodeRuntimeState | undefined, wide = false): CSSProperties {
  const touched = Boolean(state) && state !== "idle";
  const tint = toneOf(state);
  return {
    border: `1px solid ${touched ? rgba(tint, 0.34) : color.line}`,
    borderRadius: "8px",
    background: touched ? rgba(tint, 0.06) : color.bgPanel,
    padding: wide ? "14px 18px" : "15px 17px",
    transition: "all .35s ease",
    animation: state === "active" ? "ilRing 1.1s ease-out infinite" : "none",
    ...(wide ? { display: "flex", alignItems: "center", gap: "14px" } : null),
  };
}

export function dotStyle(state: NodeRuntimeState | undefined, size = 8): CSSProperties {
  const tint = toneOf(state);
  return {
    width: `${size}px`,
    height: `${size}px`,
    borderRadius: "50%",
    flex: "none",
    background: tint,
    boxShadow: `0 0 0 4px ${tint === IDLE_DOT ? "rgba(230,225,215,.12)" : rgba(tint, 0.12)}`,
    animation: state === "active" ? "ilPulse 1s ease-in-out infinite" : "none",
  };
}

export function valueStyle(state: NodeRuntimeState | undefined, size = 16): CSSProperties {
  const resolved = state && state !== "idle" && state !== "active";
  return {
    font: `700 ${size}px ${font.mono}`,
    color: resolved ? toneOf(state) : state === "active" ? color.accent : color.textDim,
    letterSpacing: "-.01em",
  };
}

/** The value a node shows before it resolves: em dash at rest, CHECKING in flight. */
export function nodeValueText(state: NodeRuntimeState | undefined, resolvedValue: string): string {
  if (!state) return "—";
  if (state === "active") return "CHECKING";
  return resolvedValue;
}

export function latencyStyle(): CSSProperties {
  return { font: `500 10px ${font.mono}`, color: color.textFaint };
}

export function metaStyle(): CSSProperties {
  return { font: `400 10px/1.5 ${font.sans}`, color: color.textMeta };
}

export function panelStyle(extra?: CSSProperties): CSSProperties {
  return {
    border: `1px solid ${color.line}`,
    borderRadius: "8px",
    background: color.bgPanel,
    ...extra,
  };
}
