import type { SurfaceTone } from "./tokens";

export type StageKey = "laneA" | "gen" | "laneB" | "ladder" | "gate" | "release" | "laneC";

export interface StageDef {
  n: string;
  key: StageKey;
  title: string;
  sub: string;
  /** Autoplay dwell in ms, before the pace multiplier. */
  dwell: number;
}

export const STAGES: StageDef[] = [
  { n: "01", key: "laneA", title: "Pre-flight", sub: "lane a · before the model", dwell: 5600 },
  { n: "02", key: "gen", title: "Generation", sub: "unmodified upstream model", dwell: 6400 },
  { n: "03", key: "laneB", title: "In-flight checks", sub: "lane b · under generation", dwell: 6200 },
  { n: "04", key: "ladder", title: "Pricing the ladder", sub: "control plane · expected loss", dwell: 7400 },
  { n: "05", key: "gate", title: "Commit gate", sub: "one sentence behind", dwell: 5200 },
  { n: "06", key: "release", title: "Release", sub: "what the customer sees", dwell: 6800 },
  { n: "07", key: "laneC", title: "Afterwards", sub: "lane c · off critical path", dwell: 6000 },
];

export const LAST_STAGE = STAGES.length - 1;

export type Level = "L0" | "L1" | "L2" | "L3" | "L4" | "L5";

export interface LadderRowDef {
  lv: Level;
  name: string;
  /** The 13.7 s / 30.7 s figures are measured medians from artifacts/action_latency.json. */
  desc: string;
}

export const LADDER: LadderRowDef[] = [
  { lv: "L0", name: "Pass", desc: "ship unchanged · 0 ms" },
  { lv: "L1", name: "Annotate", desc: "cite, hedge, flag · ~0 ms" },
  { lv: "L2", name: "Repair", desc: "regenerate one sentence · 13.7 s" },
  { lv: "L3", name: "Reroute", desc: "stronger model, re-retrieve · 30.7 s" },
  { lv: "L4", name: "Hold", desc: "require a human · SLA 4 h" },
  { lv: "L5", name: "Block", desc: "deterministic rule · 0 ms" },
];

export type BoardMessages = Record<StageKey, string>;
export type BoardTones = Record<StageKey, SurfaceTone>;
