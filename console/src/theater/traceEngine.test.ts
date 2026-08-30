import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { STAGES } from "./stages";
import { TraceEngine, boardFrame, boardTarget, formatMoney } from "./traceEngine";

describe("board maths", () => {
  it("pads every line to the longest line", () => {
    const target = boardTarget("LANE A CLEAR\n25 MS");
    expect(target).toHaveLength(2);
    expect(target[0]).toHaveLength(12);
    expect(target[1].join("")).toBe("25 MS       ");
  });

  it("leaves cells blank before their start tick and locks them seven ticks later", () => {
    const target = boardTarget("AB");
    // Cell (0,0) starts at tick 0; its neighbour does not start until tick 0.5.
    const early = boardFrame(target, 0, () => 0);
    expect(early.cur[0][0]).toBe("A");
    expect(early.cur[0][1]).toBe(" ");
    expect(early.done).toBe(false);

    const settled = boardFrame(target, 8, () => 0);
    expect(settled.cur[0].join("")).toBe("AB");
    expect(settled.done).toBe(true);
  });
});

describe("money formatting", () => {
  it("formats rupees in the Indian grouping and dollars at the published rate", () => {
    expect(formatMoney(120000, "rupee")).toBe("₹1,20,000");
    expect(formatMoney(880, "dollar")).toBe("$10");
  });
});

describe("TraceEngine", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("starts a run on stage 01 with an empty overlay", () => {
    const engine = new TraceEngine();
    engine.submit("what are the prepayment charges?");
    const state = engine.getState();
    expect(state.phase).toBe("run");
    expect(state.stage).toBe(0);
    expect(state.live?.prompt).toBe("what are the prepayment charges?");
    expect(state.durationMs).toBeNull();
    engine.destroy();
  });

  it("never advances a stage on its own", () => {
    const engine = new TraceEngine();
    engine.submit("hello");
    vi.advanceTimersByTime(STAGES[0].dwell * 4);
    expect(engine.getState().stage).toBe(0);
    engine.destroy();
  });

  it("keeps the clock running while the stream is open", () => {
    const engine = new TraceEngine();
    engine.submit("hello");
    vi.advanceTimersByTime(1_000);
    expect(engine.getState().elapsed).toBeGreaterThanOrEqual(900);
    engine.destroy();
  });

  it("freezes the clock and reports the duration when the run finishes", () => {
    const engine = new TraceEngine();
    engine.submit("hello");
    vi.advanceTimersByTime(2_000);
    engine.finishLive();

    const settled = engine.getState();
    expect(settled.durationMs).toBeGreaterThanOrEqual(2_000);
    expect(settled.elapsed).toBe(settled.durationMs);

    // The reading must not keep counting after the request is over.
    vi.advanceTimersByTime(5_000);
    expect(engine.getState().elapsed).toBe(settled.elapsed);
    engine.destroy();
  });

  it("settles lane C once the stream completes", () => {
    const engine = new TraceEngine();
    engine.submit("hello");
    expect(engine.getState().nodeSt.c0).toBeUndefined();
    engine.finishLive();
    expect(engine.getState().nodeSt.c0).toBe("pass");
    expect(engine.getState().stage).toBe(6);
    engine.destroy();
  });

  it("reopens a stored trace without starting a clock", () => {
    const engine = new TraceEngine();
    engine.submit("hello");
    engine.appendGeneration("some answer");
    engine.finishLive();
    const stored = engine.getState().live;
    expect(stored).not.toBeNull();

    const viewer = new TraceEngine();
    viewer.loadTrace(stored!, 4_200);
    expect(viewer.getState().phase).toBe("run");
    expect(viewer.getState().genText).toBe("some answer");
    expect(viewer.getState().durationMs).toBe(4_200);

    vi.advanceTimersByTime(3_000);
    expect(viewer.getState().elapsed).toBe(4_200);
    viewer.destroy();
    engine.destroy();
  });

  it("resets back to an idle console", () => {
    const engine = new TraceEngine();
    engine.submit("hello");
    engine.reset();
    expect(engine.getState().phase).toBe("idle");
    expect(engine.getState().live).toBeNull();
    engine.destroy();
  });

  it("settles the board immediately under reduced motion", () => {
    const engine = new TraceEngine({ reducedMotion: true });
    engine.submit("hello");
    expect(engine.getState().board?.done).toBe(true);
    engine.destroy();
  });
});
