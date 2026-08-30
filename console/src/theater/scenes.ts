import type { BoardMessages, BoardTones, Level } from "./stages";
import type { SurfaceTone, Tone } from "./tokens";

/**
 * The render contract for one traced request. Every field is filled from the
 * gateway stream (see `liveScene.ts`) — the console has no scripted fixtures,
 * so anything a stage shows was reported by the backend.
 */

/** Terminal node verdicts. `info` is neutral-but-resolved, never "checking". */
export type NodeState = Extract<Tone, "pass" | "warn" | "fail" | "info" | "idle">;

export interface NodeFixture {
  label: string;
  v: string;
  st: NodeState;
  /** null when the backend does not report a latency for this check. */
  ms: number | null;
  meta: string;
}

export interface SummaryFixture {
  v: number;
  prefix?: string;
  suffix?: string;
  dec?: number;
  label: string;
  note: string;
}

export interface Scene {
  label: string;
  outcome: string;
  tone: SurfaceTone;
  prompt: string;
  stakes: {
    impact: number;
    rev: "reversible" | "costly" | "irreversible";
    domain: string;
    model: string;
  };
  laneA: NodeFixture[];
  gen: string;
  laneB: NodeFixture[];
  costs: Record<Level, number>;
  /** Set when the loss table marks an action unavailable. */
  costMeta?: Partial<Record<Level, { available: boolean; reason: string | null }>>;
  chosen: Level;
  why: string;
  gate: { committed: string; buffered: string; title: string; tone: SurfaceTone; verdict: string };
  final: string;
  stamp: string;
  stampTone: SurfaceTone;
  counterfactual: string;
  summary: SummaryFixture[];
  board: BoardMessages;
  boardTone: BoardTones;
}

/** Lane C cards are fixed copy — this lane never blocks, so it never varies. */
export const LANE_C_CARDS = [
  { label: "Fairness twins", value: "SAMPLED OFFLINE", meta: "anytime-valid e-values across protected twins" },
  { label: "Shadow replay", value: "QUEUED", meta: "same prompt on the cheaper model, offline" },
  { label: "Deep judge", value: "1% SAMPLED", meta: "calibration anchor for the observer probe" },
  { label: "Drift test", value: "SCHEDULED", meta: "watches for distribution shift since the last window" },
];
