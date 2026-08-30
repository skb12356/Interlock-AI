/**
 * Design tokens for the live decision console.
 *
 * Values are transcribed from the design handoff. Signal colours are semantic:
 * a viewer must be able to read system state from colour alone, so `accent`
 * means "in flight", `pass`/`warn`/`fail` are terminal verdicts, and nothing
 * here is decorative.
 */

export const color = {
  bgBase: "#0b0c09",
  bgShell: "linear-gradient(135deg,#1a1d18 0%,#080907 52%,#2a2e26 100%)",
  bgPanel: "#1a1d18",
  bgRail: "rgba(22,24,18,.5)",
  bgRailPanel: "rgba(13,14,11,.94)",
  bgHeader: "rgba(11,12,9,.86)",
  line: "rgba(230,225,215,.09)",
  lineSoft: "rgba(230,225,215,.08)",
  text: "#e6e1d7",
  textHi: "#f8f7f5",
  textDim: "#9aa08c",
  textMeta: "#7e8471",
  textFaint: "#6a7060",
  textMute: "#4f5447",
  textMuteAlt: "#5c6152",
  textSoft: "#cfc9bb",
  textSoftAlt: "#c2bcae",
  accent: "#c8b4a0",
  pass: "#9ad17f",
  warn: "#d9a55c",
  fail: "#d9705f",
  onAccent: "#16180f",
} as const;

/** Terminal node verdicts plus the transient in-flight state. */
export type Tone = "pass" | "warn" | "fail" | "active" | "info" | "idle";

export const TONE: Record<Tone, string> = {
  pass: color.pass,
  warn: color.warn,
  fail: color.fail,
  active: color.accent,
  info: color.accent,
  idle: color.textFaint,
};

/** Board/stamp tones are the subset that can colour a whole surface. */
export type SurfaceTone = "pass" | "warn" | "fail" | "active";

const HEX = /^#([0-9a-f]{6})$/i;

/** Alpha helper for the four signal colours (`rgba(hex, a)`). */
export function rgba(hex: string, alpha: number): string {
  const match = HEX.exec(hex);
  if (!match) return hex;
  const value = match[1];
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function toneColor(tone: Tone | undefined): string {
  return tone ? TONE[tone] : color.textFaint;
}

export const font = {
  sans: "'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif",
  mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
} as const;

/** Uppercase mono micro-label used as an eyebrow throughout the console. */
export const microLabel = {
  font: `500 9px ${font.mono}`,
  letterSpacing: ".16em",
  textTransform: "uppercase" as const,
  color: color.textFaint,
};

export const radius = {
  flap: "3px",
  mark: "4px",
  stamp: "5px",
  button: "6px",
  primary: "7px",
  panel: "8px",
  card: "10px",
  pill: "100px",
} as const;
