import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SCENES } from "./scenes";
import { LADDER, STAGES } from "./stages";
import { TraceEngine, boardFrame, boardTarget, formatMoney } from "./traceEngine";

describe("board maths", () => {
  it("pads every line to the longest line", () => {
    const target = boardTarget("LANE A CLEAR\n25 MS");
    expect(target).toHaveLength(2);
    expect(target[0]).toHaveLength(12);
    expect(target[1]).toHaveLength(12);
    expect(target[1].join("")).toBe("25 MS       ");
  });

  it("leaves cells blank before their start tick and locks them seven ticks later", () => {
    const target = boardTarget("AB");
    // Cell (0,0) starts at tick 0; its neighbour does not start until tick 0.5.
    const early = boardFrame(target, 0, () => 0);
    expect(early.cur[0][0]).toBe("A");
    expect(early.cur[0][1]).toBe(" ");
    expect(early.done).toBe(false);

    const mid = boardFrame(target, 5, () => 0);
    expect(mid.cur[0][0]).toBe("A");
    expect(mid.done).toBe(false);

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

describe("TraceEngine choreography", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const build = (overrides = {}) =>
    new TraceEngine({ pace: 1, autoplay: true, reducedMotion: false, currency: "rupee", ...overrides });

  it("starts on lane A and resolves each check to its terminal state", () => {
    const engine = build();
    engine.submit();
    expect(engine.getState().phase).toBe("run");
    expect(engine.getState().stage).toBe(0);

    vi.advanceTimersByTime(300);
    expect(engine.getState().nodeSt.a0).toBe("active");

    vi.advanceTimersByTime(220);
    expect(engine.getState().nodeSt.a0).toBe(SCENES.scene1.laneA[0].st);
    expect(engine.getState().log.at(-1)).toContain("lane_a · injection check");
    engine.destroy();
  });

  it("advances to the next stage after the stage dwell", () => {
    const engine = build();
    engine.submit();
    vi.advanceTimersByTime(STAGES[0].dwell);
    expect(engine.getState().stage).toBe(1);
    engine.destroy();
  });

  it("multiplies every delay by the pace setting", () => {
    const engine = build({ pace: 2 });
    engine.submit();
    vi.advanceTimersByTime(STAGES[0].dwell);
    expect(engine.getState().stage).toBe(0);
    vi.advanceTimersByTime(STAGES[0].dwell);
    expect(engine.getState().stage).toBe(1);
    engine.destroy();
  });

  it("does not auto-advance while paused", () => {
    const engine = build();
    engine.submit();
    engine.togglePause();
    vi.advanceTimersByTime(STAGES[0].dwell * 2);
    expect(engine.getState().stage).toBe(0);
    engine.destroy();
  });

  it("continues autoplay after the viewer resumes", () => {
    const engine = build();
    engine.submit();
    engine.togglePause();
    vi.advanceTimersByTime(STAGES[0].dwell * 2);
    engine.togglePause();
    vi.advanceTimersByTime(1);
    expect(engine.getState().stage).toBe(1);
    engine.destroy();
  });

  it("cancels a half-finished stage when the viewer jumps", () => {
    const engine = build();
    engine.submit();
    vi.advanceTimersByTime(300);
    engine.go(3);
    vi.advanceTimersByTime(220);
    // The lane A resolution that was already scheduled must not land after the jump.
    expect(engine.getState().nodeSt.a0).toBe("active");
    expect(engine.getState().stage).toBe(3);
    engine.destroy();
  });

  it("prices all six ladder rows and reveals the winner at 3.1 s", () => {
    const engine = build({ autoplay: false });
    engine.submit();
    engine.go(3);

    vi.advanceTimersByTime(250);
    expect(Object.values(engine.getState().ladderSt)).toHaveLength(LADDER.length);
    expect(engine.getState().ladderSt.L0).toBe("pricing");

    vi.advanceTimersByTime(1100);
    expect(engine.getState().ladderSt.L0).toBe("priced");
    expect(engine.getState().chosenShown).toBe(false);

    vi.advanceTimersByTime(3100 - 1350);
    expect(engine.getState().chosenShown).toBe(true);
    expect(engine.getState().log.at(-1)).toBe("control_plane · chosen L2");
    engine.destroy();
  });

  it("types the draft two characters at a time", () => {
    const engine = build({ autoplay: false });
    engine.submit();
    engine.go(1);
    vi.advanceTimersByTime(500);
    expect(engine.getState().genText).toHaveLength(2);
    vi.advanceTimersByTime(24 * 4);
    expect(engine.getState().genText).toHaveLength(10);
    engine.destroy();
  });

  it("settles the board and the draft immediately under reduced motion", () => {
    const engine = build({ reducedMotion: true, autoplay: false });
    engine.submit();
    expect(engine.getState().board?.done).toBe(true);
    engine.go(1);
    expect(engine.getState().genText).toBe(SCENES.scene1.gen);
    engine.destroy();
  });

  it("switching scene replaces the prompt", () => {
    const engine = build();
    engine.setScene("blocked");
    expect(engine.getState().prompt).toBe(SCENES.blocked.prompt);
    engine.destroy();
  });

  it("reset returns to the hero and stops the clock", () => {
    const engine = build();
    engine.submit();
    vi.advanceTimersByTime(500);
    engine.reset();
    const elapsed = engine.getState().elapsed;
    vi.advanceTimersByTime(1000);
    expect(engine.getState().phase).toBe("hero");
    expect(engine.getState().elapsed).toBe(elapsed);
    engine.destroy();
  });
});
