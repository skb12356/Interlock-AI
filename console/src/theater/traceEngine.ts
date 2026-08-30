import { LADDER, LAST_STAGE, STAGES, type Level, type StageKey } from "./stages";
import { SCENES, type NodeState, type Scene, type SceneId } from "./scenes";
import { ACTION_LEVEL, deriveLiveScene, emptyOverlay, type LiveOverlay } from "./liveScene";
import type { SurfaceTone } from "./tokens";

/**
 * The trace engine drives the stage choreography. It is deliberately free of
 * React so the timings can be tested directly with fake timers: every schedule
 * in the design is a number in one place, and every timeout it starts is
 * registered so a stage jump can cancel a half-finished sequence.
 */

export type Phase = "hero" | "run";
export type Mode = "demo" | "live";
/** `active` is transient (a check in flight); the rest are terminal. */
export type NodeRuntimeState = NodeState | "active";
export type LadderRowState = "pricing" | "priced";

export interface BoardState {
  cur: string[][];
  done: boolean;
}

export interface TraceUiState {
  phase: Phase;
  mode: Mode;
  scene: SceneId;
  prompt: string;
  stage: number;
  paused: boolean;
  railOpen: boolean;
  nodeSt: Record<string, NodeRuntimeState>;
  genText: string;
  ladderSt: Partial<Record<Level, LadderRowState>>;
  chosenShown: boolean;
  gateStep: 0 | 1;
  board: BoardState | null;
  boardTone: SurfaceTone;
  counts: Record<number, number>;
  elapsed: number;
  /** Milliseconds spent inside the current stage, for the footer progress bar. */
  stageElapsed: number;
  /** Present only in live mode: what the gateway stream has reported so far. */
  live: LiveOverlay | null;
  log: string[];
}

export interface EngineSettings {
  /** Multiplies every scheduled delay. 0.5–2 in the UI. */
  pace: number;
  autoplay: boolean;
  reducedMotion: boolean;
  currency: "rupee" | "dollar";
}

export const DEFAULT_SETTINGS: EngineSettings = {
  pace: 1,
  autoplay: true,
  reducedMotion: false,
  currency: "rupee",
};

/** Glyph alphabet the split-flap board cycles through before settling. */
export const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789₹%·";

export const BOARD_TICK_MS = 42;
export const CLOCK_TICK_MS = 90;
const BOARD_SETTLE_TICKS = 7;
const BOARD_START_STRIDE = 0.5;

export interface EngineClock {
  setTimeout: (fn: () => void, ms: number) => number;
  clearTimeout: (id: number) => void;
  setInterval: (fn: () => void, ms: number) => number;
  clearInterval: (id: number) => void;
  now: () => number;
  random: () => number;
}

const defaultClock: EngineClock = {
  setTimeout: (fn, ms) => setTimeout(fn, ms) as unknown as number,
  clearTimeout: (id) => clearTimeout(id),
  setInterval: (fn, ms) => setInterval(fn, ms) as unknown as number,
  clearInterval: (id) => clearInterval(id),
  now: () => Date.now(),
  random: () => Math.random(),
};

export function initialTraceState(scene: SceneId = "scene1"): TraceUiState {
  return {
    phase: "hero",
    mode: "demo",
    scene,
    prompt: SCENES[scene].prompt,
    stage: 0,
    paused: false,
    railOpen: false,
    nodeSt: {},
    genText: "",
    ladderSt: {},
    chosenShown: false,
    gateStep: 0,
    board: null,
    boardTone: "active",
    counts: {},
    elapsed: 0,
    stageElapsed: 0,
    live: null,
    log: [],
  };
}

export function formatMoney(value: number, currency: EngineSettings["currency"]): string {
  if (currency === "dollar") {
    const dollars = Math.round((value / 88) * 100) / 100;
    return `$${dollars.toLocaleString("en-US")}`;
  }
  return `₹${value.toLocaleString("en-IN")}`;
}

/** Pads every board line to the longest line and splits it into cells. */
export function boardTarget(message: string): string[][] {
  const lines = message.split("\n");
  const width = lines.reduce((max, line) => Math.max(max, line.length), 0);
  return lines.map((line) => line.padEnd(width, " ").split(""));
}

/**
 * One frame of the split-flap animation. Cell (r, c) of a `w`-wide grid starts
 * flipping at tick `(r*w + c) * 0.5` and locks 7 ticks later.
 */
export function boardFrame(
  target: string[][],
  tick: number,
  random: () => number,
): BoardState {
  const width = target[0]?.length ?? 0;
  let done = true;
  const cur = target.map((row, ri) =>
    row.map((ch, ci) => {
      const start = (ri * width + ci) * BOARD_START_STRIDE;
      const settle = start + BOARD_SETTLE_TICKS;
      if (tick >= settle) return ch;
      done = false;
      if (tick < start) return " ";
      return ch === " " ? " " : GLYPHS[Math.floor(random() * GLYPHS.length)];
    }),
  );
  return { cur, done };
}

type Listener = () => void;

export class TraceEngine {
  private state: TraceUiState;
  private listeners = new Set<Listener>();
  private settings: EngineSettings;
  private readonly clock: EngineClock;

  private timers: number[] = [];
  private boardTimer: number | null = null;
  private clockTimer: number | null = null;
  private startedAt = 0;
  private stageStartedAt = 0;

  constructor(
    settings: Partial<EngineSettings> = {},
    clock: EngineClock = defaultClock,
    scene: SceneId = "scene1",
  ) {
    this.settings = { ...DEFAULT_SETTINGS, ...settings };
    this.clock = clock;
    this.state = initialTraceState(scene);
  }

  /* ---- store surface ---- */

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = (): TraceUiState => this.state;

  private set(patch: Partial<TraceUiState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  updateSettings(settings: Partial<EngineSettings>): void {
    this.settings = { ...this.settings, ...settings };
  }

  getSettings(): EngineSettings {
    return this.settings;
  }

  get scene(): Scene {
    return this.state.live ? deriveLiveScene(this.state.live) : SCENES[this.state.scene];
  }

  /* ---- timers ---- */

  private at(ms: number, fn: () => void): void {
    this.timers.push(this.clock.setTimeout(fn, ms * this.settings.pace));
  }

  private clearStageTimers(): void {
    this.timers.forEach((id) => this.clock.clearTimeout(id));
    this.timers = [];
  }

  private log(line: string): void {
    this.set({ log: this.state.log.concat(line).slice(-40) });
  }

  /** Stops every timer. Call on unmount. */
  destroy(): void {
    this.clearStageTimers();
    if (this.boardTimer !== null) this.clock.clearInterval(this.boardTimer);
    if (this.clockTimer !== null) this.clock.clearInterval(this.clockTimer);
    this.boardTimer = null;
    this.clockTimer = null;
    this.listeners.clear();
  }

  /* ---- hero controls ---- */

  setScene(scene: SceneId): void {
    this.set({ scene, prompt: SCENES[scene].prompt });
  }

  setPrompt(prompt: string): void {
    this.set({ prompt });
  }

  setMode(mode: Mode): void {
    this.set({ mode });
  }

  toggleMode(): void {
    this.setMode(this.state.mode === "demo" ? "live" : "demo");
  }

  setRailOpen(railOpen: boolean): void {
    this.set({ railOpen });
  }

  /* ---- run control ---- */

  /** `live` starts a real gateway request; the demo path replays fixtures. */
  submit(live = false): void {
    this.clearStageTimers();
    if (this.clockTimer !== null) this.clock.clearInterval(this.clockTimer);
    this.startedAt = this.clock.now();
    this.stageStartedAt = this.startedAt;
    this.clockTimer = this.clock.setInterval(
      () =>
        this.set({
          elapsed: this.clock.now() - this.startedAt,
          stageElapsed: this.clock.now() - this.stageStartedAt,
        }),
      CLOCK_TICK_MS,
    );
    this.set({
      phase: "run",
      stage: 0,
      paused: false,
      log: [],
      nodeSt: {},
      counts: {},
      elapsed: 0,
      stageElapsed: 0,
      genText: "",
      ladderSt: {},
      chosenShown: false,
      gateStep: 0,
      live: live ? emptyOverlay(this.state.prompt) : null,
    });
    this.enter(0);
  }

  replay(): void {
    this.submit(this.state.live !== null);
  }

  reset(): void {
    this.clearStageTimers();
    if (this.boardTimer !== null) this.clock.clearInterval(this.boardTimer);
    if (this.clockTimer !== null) this.clock.clearInterval(this.clockTimer);
    this.boardTimer = null;
    this.clockTimer = null;
    this.set({ phase: "hero", board: null, stage: 0, elapsed: 0 });
  }

  go(index: number): void {
    if (index < 0 || index > LAST_STAGE) return;
    this.clearStageTimers();
    this.set({ stage: index });
    this.enter(index);
  }

  next(): void {
    this.go(this.state.stage + 1);
  }

  prev(): void {
    this.go(this.state.stage - 1);
  }

  togglePause(): void {
    this.set({ paused: !this.state.paused });
  }

  /* ---- split-flap board ---- */

  private startBoard(message: string, tone: SurfaceTone): void {
    if (this.boardTimer !== null) this.clock.clearInterval(this.boardTimer);
    const target = boardTarget(message);
    if (this.settings.reducedMotion) {
      this.set({ board: { cur: target, done: true }, boardTone: tone });
      return;
    }
    this.set({
      board: { cur: target.map((row) => row.map(() => " ")), done: false },
      boardTone: tone,
    });
    // Ticks are derived from the wall clock rather than counted, so a throttled
    // interval (a background tab) still settles the board instead of freezing it.
    const startedAt = this.clock.now();
    this.boardTimer = this.clock.setInterval(() => {
      const tick = Math.floor((this.clock.now() - startedAt) / BOARD_TICK_MS) + 1;
      const frame = boardFrame(target, tick, this.clock.random);
      this.set({ board: frame });
      if (frame.done && this.boardTimer !== null) {
        this.clock.clearInterval(this.boardTimer);
        this.boardTimer = null;
      }
    }, BOARD_TICK_MS);
  }

  /* ---- per-stage choreography ---- */

  private enter(index: number): void {
    const stage = STAGES[index];
    const scene = this.scene;
    this.stageStartedAt = this.clock.now();
    this.set({ stageElapsed: 0 });
    this.startBoard(scene.board[stage.key], scene.boardTone[stage.key]);

    if (!this.state.live && this.settings.autoplay && !this.state.paused && index < LAST_STAGE) {
      this.at(stage.dwell, () => {
        if (!this.state.paused) this.go(index + 1);
      });
    }

    if (this.state.live) {
      // Live stages are filled by the stream, never by a scripted timeline.
      this.syncLiveNodes();
      return;
    }
    this.stageChoreography(stage.key, scene);
  }

  /* ---- live mode ---- */

  private updateLive(mutate: (overlay: LiveOverlay) => LiveOverlay): void {
    const current = this.state.live;
    if (!current) return;
    this.set({ live: mutate(current) });
    this.syncLiveNodes();
  }

  /**
   * Mirrors whatever the stream has delivered into the same view state the demo
   * path animates, so every stage component stays source-agnostic.
   */
  private syncLiveNodes(): void {
    const overlay = this.state.live;
    if (!overlay) return;
    const scene = deriveLiveScene(overlay);
    const nodeSt: Record<string, NodeRuntimeState> = { ...this.state.nodeSt };
    scene.laneA.forEach((node, index) => {
      nodeSt[`a${index}`] = node.st;
    });
    scene.laneB.forEach((node, index) => {
      nodeSt[`b${index}`] = node.st;
    });
    const ladderSt: Partial<Record<Level, LadderRowState>> = {};
    if (overlay.lossTable) {
      overlay.lossTable.forEach((row) => {
        ladderSt[ACTION_LEVEL[row.action]] = "priced";
      });
    } else if (overlay.decision) {
      LADDER.forEach((row) => {
        ladderSt[row.lv] = "pricing";
      });
    }
    const counts: Record<number, number> = {};
    scene.summary.forEach((card, index) => {
      counts[index] = card.v;
    });
    this.set({
      nodeSt,
      genText: overlay.assistantText,
      ladderSt,
      chosenShown: overlay.decision !== null,
      gateStep: overlay.decision !== null || overlay.hold !== null ? 1 : 0,
      counts,
    });
  }

  /** Moves forward only: a late frame must never rewind the viewer's position. */
  private advanceTo(index: number): void {
    if (index <= this.state.stage || index > LAST_STAGE) return;
    this.go(index);
  }

  applyStakes(stakes: LiveOverlay["stakes"]): void {
    this.updateLive((overlay) => ({ ...overlay, stakes }));
    this.log(`interlock.stakes · ₹${stakes?.impact_inr ?? 0} · ${stakes?.reversibility ?? "unknown"}`);
  }

  appendGeneration(text: string): void {
    if (!text) return;
    this.updateLive((overlay) => ({ ...overlay, assistantText: overlay.assistantText + text }));
    this.advanceTo(1);
  }

  applySignal(signal: LiveOverlay["signals"][number]): void {
    this.updateLive((overlay) => ({ ...overlay, signals: overlay.signals.concat(signal) }));
    this.log(`interlock.signal · ${signal.name} · ${signal.prob === null ? "unscored" : signal.prob.toFixed(2)}`);
    this.advanceTo(2);
  }

  applyDecision(decision: NonNullable<LiveOverlay["decision"]>): void {
    this.updateLive((overlay) => ({ ...overlay, decision }));
    this.log(`interlock.decision · ${decision.action} · ${decision.chosen_loss.toFixed(2)}`);
    this.advanceTo(3);
  }

  applyLossTable(lossTable: NonNullable<LiveOverlay["lossTable"]>, latencyMs: number | null): void {
    this.updateLive((overlay) => ({ ...overlay, lossTable, decisionLatencyMs: latencyMs }));
    this.advanceTo(3);
  }

  applyHold(hold: NonNullable<LiveOverlay["hold"]>): void {
    this.updateLive((overlay) => ({ ...overlay, hold }));
    this.log(`interlock.hold · ${hold.hold_id} · ${hold.kind}`);
    this.advanceTo(4);
  }

  finishLive(error: string | null = null): void {
    this.updateLive((overlay) => ({ ...overlay, finished: true, error }));
    this.log(error ? `stream failed · ${error}` : "stream complete");
    this.advanceTo(error ? 4 : 5);
  }

  private stageChoreography(key: StageKey, scene: Scene): void {
    switch (key) {
      case "laneA":
        return this.runLaneA(scene);
      case "gen":
        return this.runGen(scene);
      case "laneB":
        return this.runLaneB(scene);
      case "ladder":
        return this.runLadder(scene);
      case "gate":
        return this.runGate(scene);
      case "release":
        return this.runRelease(scene);
      case "laneC":
        return this.runLaneC();
    }
  }

  private runLaneA(scene: Scene): void {
    this.set({ nodeSt: {}, genText: "" });
    scene.laneA.forEach((node, k) => {
      this.at(300 + k * 300, () => {
        this.set({ nodeSt: { ...this.state.nodeSt, [`a${k}`]: "active" } });
      });
      this.at(300 + k * 300 + 220, () => {
        this.set({ nodeSt: { ...this.state.nodeSt, [`a${k}`]: node.st } });
        this.log(`lane_a · ${node.label.toLowerCase()} · ${node.v}`);
      });
    });
  }

  private runGen(scene: Scene): void {
    if (this.settings.reducedMotion) {
      this.set({ genText: scene.gen });
    } else {
      this.set({ genText: "" });
      let cursor = 0;
      const step = () => {
        cursor = Math.min(scene.gen.length, cursor + 2);
        this.set({ genText: scene.gen.slice(0, cursor) });
        if (cursor < scene.gen.length) this.at(24, step);
      };
      this.at(500, step);
    }
    this.log(`generation · streaming from ${scene.stakes.model}`);
  }

  private runLaneB(scene: Scene): void {
    this.set({ genText: scene.gen });
    scene.laneB.forEach((node, k) => {
      this.at(500 + k * 900, () => {
        this.set({ nodeSt: { ...this.state.nodeSt, [`b${k}`]: "active" } });
      });
      this.at(500 + k * 900 + 600, () => {
        this.set({ nodeSt: { ...this.state.nodeSt, [`b${k}`]: node.st } });
        this.log(`lane_b · ${node.label.toLowerCase()} · ${node.v}`);
      });
    });
  }

  private runLadder(scene: Scene): void {
    this.set({ ladderSt: {}, chosenShown: false });
    this.at(250, () => {
      const pricing: Partial<Record<Level, LadderRowState>> = {};
      LADDER.forEach((row) => {
        pricing[row.lv] = "pricing";
      });
      this.set({ ladderSt: pricing });
    });
    LADDER.forEach((row, k) => {
      this.at(1100 + k * 260, () => {
        this.set({ ladderSt: { ...this.state.ladderSt, [row.lv]: "priced" } });
        this.log(
          `control_plane · priced ${row.lv} · ${formatMoney(scene.costs[row.lv], this.settings.currency)}`,
        );
      });
    });
    this.at(3100, () => {
      this.set({ chosenShown: true });
      this.log(`control_plane · chosen ${scene.chosen}`);
    });
  }

  private runGate(scene: Scene): void {
    this.set({ gateStep: 0 });
    this.at(1400, () => {
      this.set({ gateStep: 1 });
      this.log(`commit_gate · ${scene.chosen} applied`);
    });
  }

  private runRelease(scene: Scene): void {
    this.set({ counts: {} });
    scene.summary.forEach((card, k) => {
      const duration = 900;
      const startAt = this.clock.now() + (300 + k * 120) * this.settings.pace;
      const tick = () => {
        const progress = Math.min(1, (this.clock.now() - startAt) / duration);
        if (progress < 0) {
          this.at(30, tick);
          return;
        }
        const eased = 1 - Math.pow(1 - progress, 3);
        this.set({ counts: { ...this.state.counts, [k]: card.v * eased } });
        if (progress < 1) this.at(30, tick);
      };
      this.at(300 + k * 120, tick);
    });
    this.log(`released · ${scene.stamp}`);
  }

  private runLaneC(): void {
    for (let k = 0; k < 4; k += 1) {
      this.at(400 + k * 400, () => {
        this.set({ nodeSt: { ...this.state.nodeSt, [`c${k}`]: "pass" } });
      });
    }
    this.log("lane_c · sampled, non-blocking");
  }
}
