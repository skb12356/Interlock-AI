import { LADDER, LAST_STAGE, STAGES, type Level } from "./stages";
import type { NodeState, Scene } from "./scenes";
import { ACTION_LEVEL, deriveLiveScene, emptyOverlay, type LiveOverlay } from "./liveScene";
import type { SurfaceTone } from "./tokens";

/**
 * The trace engine turns one gateway stream into the seven-stage view. It is
 * deliberately free of React so its behaviour can be tested directly, and it is
 * live-only: there is no scripted timeline, so every stage a viewer sees is a
 * frame the backend actually sent.
 */

export type Phase = "idle" | "run";
/** `active` is transient (a check in flight); the rest are terminal. */
export type NodeRuntimeState = NodeState | "active";
export type LadderRowState = "pricing" | "priced";

export interface BoardState {
  cur: string[][];
  done: boolean;
}

export interface TraceUiState {
  phase: Phase;
  prompt: string;
  stage: number;
  railOpen: boolean;
  nodeSt: Record<string, NodeRuntimeState>;
  genText: string;
  ladderSt: Partial<Record<Level, LadderRowState>>;
  chosenShown: boolean;
  gateStep: 0 | 1;
  board: BoardState | null;
  boardTone: SurfaceTone;
  counts: Record<number, number>;
  /** Milliseconds since submit; frozen once the run finishes. */
  elapsed: number;
  /** Wall-clock duration of the finished run; null while it is still streaming. */
  durationMs: number | null;
  /** What the gateway stream has reported so far. */
  live: LiveOverlay | null;
  log: string[];
}

export interface EngineSettings {
  reducedMotion: boolean;
  currency: "rupee" | "dollar";
}

export const DEFAULT_SETTINGS: EngineSettings = {
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

export function initialTraceState(): TraceUiState {
  return {
    phase: "idle",
    prompt: "",
    stage: 0,
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
    durationMs: null,
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
export function boardFrame(target: string[][], tick: number, random: () => number): BoardState {
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

  private boardTimer: number | null = null;
  private clockTimer: number | null = null;
  private startedAt = 0;

  constructor(settings: Partial<EngineSettings> = {}, clock: EngineClock = defaultClock) {
    this.settings = { ...DEFAULT_SETTINGS, ...settings };
    this.clock = clock;
    this.state = initialTraceState();
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

  /** The render contract, always derived from what the stream reported. */
  get scene(): Scene {
    return deriveLiveScene(this.state.live ?? emptyOverlay(this.state.prompt));
  }

  private log(line: string): void {
    this.set({ log: this.state.log.concat(line).slice(-40) });
  }

  private stopClock(): void {
    if (this.clockTimer !== null) this.clock.clearInterval(this.clockTimer);
    this.clockTimer = null;
  }

  private stopBoard(): void {
    if (this.boardTimer !== null) this.clock.clearInterval(this.boardTimer);
    this.boardTimer = null;
  }

  /** Stops every timer. Call on unmount. */
  destroy(): void {
    this.stopClock();
    this.stopBoard();
    this.listeners.clear();
  }

  setPrompt(prompt: string): void {
    this.set({ prompt });
  }

  setRailOpen(railOpen: boolean): void {
    this.set({ railOpen });
  }

  /* ---- run control ---- */

  /** Starts a run. The console only ever streams from the gateway. */
  submit(prompt = this.state.prompt): void {
    this.stopClock();
    this.startedAt = this.clock.now();
    this.clockTimer = this.clock.setInterval(
      () => this.set({ elapsed: this.clock.now() - this.startedAt }),
      CLOCK_TICK_MS,
    );
    this.set({
      phase: "run",
      prompt,
      stage: 0,
      log: [],
      nodeSt: {},
      counts: {},
      elapsed: 0,
      genText: "",
      ladderSt: {},
      chosenShown: false,
      gateStep: 0,
      live: emptyOverlay(prompt),
      durationMs: null,
    });
    this.enter(0);
  }

  /** Reopens a finished trace for inspection. No timers, nothing left running. */
  loadTrace(overlay: LiveOverlay, durationMs: number | null): void {
    this.stopClock();
    this.set({
      phase: "run",
      prompt: overlay.prompt,
      stage: 0,
      log: [
        overlay.error
          ? `reopened · stream failed · ${overlay.error}`
          : `reopened · ${overlay.decision?.action ?? "no decision recorded"}`,
      ],
      nodeSt: {},
      counts: {},
      elapsed: durationMs ?? 0,
      durationMs,
      live: overlay,
    });
    this.enter(0);
  }

  reset(): void {
    this.stopClock();
    this.stopBoard();
    this.set({ phase: "idle", board: null, stage: 0, elapsed: 0, durationMs: null, live: null });
  }

  go(index: number): void {
    if (index < 0 || index > LAST_STAGE) return;
    this.set({ stage: index });
    this.enter(index);
  }

  /* ---- split-flap board ---- */

  private startBoard(message: string, tone: SurfaceTone): void {
    this.stopBoard();
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
      if (frame.done) this.stopBoard();
    }, BOARD_TICK_MS);
  }

  private enter(index: number): void {
    const stage = STAGES[index];
    const scene = this.scene;
    this.startBoard(scene.board[stage.key], scene.boardTone[stage.key]);
    this.syncLiveNodes();
  }

  /* ---- stream application ---- */

  private updateLive(mutate: (overlay: LiveOverlay) => LiveOverlay): void {
    const current = this.state.live;
    if (!current) return;
    this.set({ live: mutate(current) });
    this.syncLiveNodes();
  }

  /** Mirrors whatever the stream delivered into the state the stages render. */
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
    if (overlay.finished) {
      for (let index = 0; index < 4; index += 1) nodeSt[`c${index}`] = "pass";
    }
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

  /**
   * Ends the run. The clock stops here and the elapsed reading freezes, so the
   * header can report the time the request actually took rather than counting
   * on into a run that finished minutes ago.
   */
  finishLive(error: string | null = null): void {
    const durationMs = this.clock.now() - this.startedAt;
    this.stopClock();
    this.updateLive((overlay) => ({ ...overlay, finished: true, error }));
    this.set({ elapsed: durationMs, durationMs });
    this.log(error ? `stream failed · ${error}` : "stream complete");
    this.advanceTo(error ? 4 : 6);
  }
}
