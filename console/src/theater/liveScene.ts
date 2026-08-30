import type {
  Action,
  DecisionEvent,
  HoldEvent,
  LossRow,
  SignalEvent,
  StakesEvent,
} from "../domain/contracts";
import type { NodeFixture, NodeState, Scene } from "./scenes";
import type { Level } from "./stages";
import type { SurfaceTone } from "./tokens";

/**
 * Live mode maps the gateway's own SSE contract onto the same stage machine the
 * demo uses. Nothing here invents a number: a check the backend does not report
 * is shown as unreported, not as a plausible-looking measurement.
 */

export interface LiveOverlay {
  prompt: string;
  stakes: StakesEvent | null;
  assistantText: string;
  signals: SignalEvent[];
  decision: DecisionEvent | null;
  lossTable: LossRow[] | null;
  decisionLatencyMs: number | null;
  hold: HoldEvent | null;
  error: string | null;
  finished: boolean;
}

export function emptyOverlay(prompt: string): LiveOverlay {
  return {
    prompt,
    stakes: null,
    assistantText: "",
    signals: [],
    decision: null,
    lossTable: null,
    decisionLatencyMs: null,
    hold: null,
    error: null,
    finished: false,
  };
}

export const ACTION_LEVEL: Record<Action, Level> = {
  L0_pass: "L0",
  L1_annotate: "L1",
  L2_repair: "L2",
  L3_reroute: "L3",
  L4_hold: "L4",
  L5_block: "L5",
};

const ACTION_STAMP: Record<Action, string> = {
  L0_pass: "L0 PASS",
  L1_annotate: "L1 ANNOTATE",
  L2_repair: "L2 REPAIR",
  L3_reroute: "L3 REROUTE",
  L4_hold: "L4 HOLD",
  L5_block: "L5 BLOCK",
};

const ACTION_TONE: Record<Action, SurfaceTone> = {
  L0_pass: "pass",
  L1_annotate: "pass",
  L2_repair: "warn",
  L3_reroute: "warn",
  L4_hold: "warn",
  L5_block: "fail",
};

function probState(prob: number | null): NodeState {
  if (prob === null) return "idle";
  if (prob >= 0.7) return "fail";
  if (prob >= 0.4) return "warn";
  return "pass";
}

function reversibilityState(stakes: StakesEvent | null): NodeState {
  if (!stakes) return "idle";
  if (stakes.reversibility === "irreversible") return "fail";
  if (stakes.reversibility === "costly") return "warn";
  return "pass";
}

function laneANodes(overlay: LiveOverlay): NodeFixture[] {
  const stakes = overlay.stakes;
  return [
    {
      label: "Stakes estimate",
      v: stakes ? `₹${stakes.impact_inr.toLocaleString("en-IN")}` : "—",
      st: stakes ? "info" : "idle",
      ms: null,
      meta: stakes ? `${stakes.reversibility} · ${stakes.domain}` : "waiting for interlock.stakes",
    },
    {
      label: "Reversibility",
      v: stakes ? stakes.reversibility.toUpperCase() : "—",
      st: reversibilityState(stakes),
      ms: null,
      meta: "drives both the routing budget and the checking budget",
    },
    {
      label: "Gate mode",
      v: stakes ? stakes.mode.toUpperCase() : "—",
      st: stakes ? (stakes.mode === "buffered" ? "info" : "pass") : "idle",
      ms: null,
      meta: "buffered traffic streams one sentence behind generation",
    },
    {
      label: "Route decision",
      v: stakes?.model_served ? stakes.model_served.toUpperCase() : "—",
      st: stakes?.model_served ? "info" : "idle",
      ms: null,
      meta: stakes?.route_reason ?? "route reason not reported on this stream",
    },
    {
      label: "Pre-flight checks",
      v: "AGGREGATE ONLY",
      st: "idle",
      ms: null,
      meta: "injection, PII and canary results are not itemised in the stream contract",
    },
    {
      label: "Request",
      v: overlay.stakes?.stakes_id ? overlay.stakes.stakes_id.slice(0, 10).toUpperCase() : "LIVE",
      st: "info",
      ms: null,
      meta: "streamed through the gateway on :8080",
    },
  ];
}

function laneBNodes(overlay: LiveOverlay): NodeFixture[] {
  const latest = new Map<string, SignalEvent>();
  overlay.signals.forEach((signal) => latest.set(signal.name, signal));
  const rows = Array.from(latest.values())
    .sort((a, b) => (b.prob ?? 0) - (a.prob ?? 0))
    .slice(0, 6)
    .map<NodeFixture>((signal) => ({
      label: signal.name,
      v: signal.prob === null ? "NOT SCORED" : signal.prob.toFixed(2),
      st: probState(signal.prob),
      ms: null,
      meta: `reported for sentence idx ${signal.sentence_idx}`,
    }));
  if (rows.length > 0) return rows;
  return [
    {
      label: "Observer signals",
      v: "—",
      st: "idle",
      ms: null,
      meta: "no interlock.signal frame has arrived yet",
    },
  ];
}

function liveCosts(overlay: LiveOverlay): {
  costs: Record<Level, number>;
  costMeta: Scene["costMeta"];
} {
  const costs: Record<Level, number> = { L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, L5: 0 };
  const costMeta: NonNullable<Scene["costMeta"]> = {};
  overlay.lossTable?.forEach((row) => {
    const level = ACTION_LEVEL[row.action];
    costs[level] = row.total;
    costMeta[level] = { available: row.available, reason: row.unavailable_reason };
  });
  return { costs, costMeta };
}

/** Builds the Scene the stage components render from whatever the stream has delivered. */
export function deriveLiveScene(overlay: LiveOverlay): Scene {
  const action = overlay.decision?.action ?? null;
  const chosen: Level = action ? ACTION_LEVEL[action] : "L0";
  const tone: SurfaceTone = action ? ACTION_TONE[action] : "active";
  const { costs, costMeta } = liveCosts(overlay);
  const held = overlay.hold !== null;

  const summary: Scene["summary"] = [
    {
      v: overlay.decisionLatencyMs ?? 0,
      suffix: " ms",
      prefix: "+",
      label: "decision latency",
      note: overlay.decisionLatencyMs === null ? "not reported for this request" : "measured on the decision path",
    },
    {
      v: overlay.signals.length,
      label: "signals scored",
      note: "frames received on interlock.signal",
    },
    {
      v: overlay.lossTable?.length ?? 0,
      label: "actions priced",
      note: "rows in the decision loss table",
    },
    {
      v: action && action !== "L0_pass" ? 1 : 0,
      suffix: " / 1",
      label: "interventions applied",
      note: action ? `chosen action ${ACTION_STAMP[action]}` : "no decision frame yet",
    },
  ];

  const board: Scene["board"] = {
    laneA: overlay.stakes ? `STAKES ${overlay.stakes.impact_inr}\n${overlay.stakes.domain.toUpperCase()}` : "LIVE REQUEST\nPRE FLIGHT",
    gen: `LIVE STREAM\n${(overlay.stakes?.model_served ?? "UPSTREAM").toUpperCase()}`,
    laneB: overlay.signals.length > 0 ? `SIGNALS ${overlay.signals.length}\nSCORED LIVE` : "AWAITING\nSIGNALS",
    ladder: action ? `${ACTION_STAMP[action].replace(" ", " WINS ")}` : "PRICING\nTHE LADDER",
    gate: held ? "HELD FOR\nA HUMAN" : "COMMIT GATE\nONE BEHIND",
    release: action ? ACTION_STAMP[action] : "RELEASED",
    laneC: "SAMPLED\nAFTERWARDS",
  };

  const boardTone: Scene["boardTone"] = {
    laneA: "active",
    gen: "active",
    laneB: overlay.signals.some((signal) => (signal.prob ?? 0) >= 0.7) ? "fail" : "active",
    ladder: tone,
    gate: held ? "warn" : tone,
    release: tone,
    laneC: "active",
  };

  return {
    label: "Live request",
    outcome: action ? ACTION_STAMP[action] : "in flight",
    tone,
    prompt: overlay.prompt,
    stakes: {
      impact: overlay.stakes?.impact_inr ?? 0,
      rev: overlay.stakes?.reversibility ?? "reversible",
      domain: overlay.stakes?.domain ?? "unknown",
      model: overlay.stakes?.model_served ?? "upstream",
    },
    laneA: laneANodes(overlay),
    gen: overlay.assistantText,
    laneB: laneBNodes(overlay),
    costs,
    costMeta,
    chosen,
    why: overlay.decision?.hard_rule
      ? `hard rule: ${overlay.decision.hard_rule}`
      : overlay.decision
        ? `chosen loss ${overlay.decision.chosen_loss.toFixed(2)} · runner-up ${overlay.decision.runner_up ?? "none"} · margin ${overlay.decision.margin?.toFixed(2) ?? "—"}`
        : "waiting for interlock.decision",
    gate: {
      committed: overlay.assistantText || "— nothing released yet —",
      buffered: held ? (overlay.hold?.reason ?? "held") : "— no sentence is being held —",
      title: held ? "frozen at the interlock" : "buffer",
      tone: held ? "fail" : "pass",
      verdict: held
        ? `Hold ${overlay.hold?.hold_id} (${overlay.hold?.kind}) is waiting on a human.`
        : overlay.finished
          ? "The gate released every sentence it checked."
          : "holding one sentence behind generation…",
    },
    final: overlay.assistantText || "— no content was released —",
    stamp: action ? ACTION_STAMP[action] : "IN FLIGHT",
    stampTone: tone,
    counterfactual:
      overlay.decision?.counterfactual ??
      "The stream reported no counterfactual for this request.",
    summary,
    board,
    boardTone,
  };
}
