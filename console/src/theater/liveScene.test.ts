import { describe, expect, it } from "vitest";

import type { DecisionEvent, LossRow, StakesEvent } from "../domain/contracts";
import { deriveLiveScene, emptyOverlay } from "./liveScene";
import { TraceEngine } from "./traceEngine";

const stakes: StakesEvent = {
  impact_inr: 48_000,
  reversibility: "irreversible",
  domain: "claims",
  mode: "buffered",
  route_reason: "irreversible action forces the strong model",
  model_served: "gpt-oss-20b",
};

const decision: DecisionEvent = {
  decision_id: "dec_1",
  sentence_idx: 1,
  action: "L4_hold",
  chosen_loss: 74.2,
  runner_up: "L2_repair",
  margin: 12.5,
  counterfactual: "an email asserting a date no document supports",
  hard_rule: null,
};

const lossTable: LossRow[] = [
  { action: "L0_pass", residual_harm: 900, nuisance: 0, compute: 0, latency: 0, total: 900, available: true, unavailable_reason: null },
  { action: "L4_hold", residual_harm: 40, nuisance: 30, compute: 4, latency: 0, total: 74, available: true, unavailable_reason: null },
  { action: "L3_reroute", residual_harm: 0, nuisance: 0, compute: 0, latency: 0, total: 0, available: false, unavailable_reason: "no stronger model configured" },
];

describe("live scene derivation", () => {
  it("reports unmeasured lane A latencies as unreported rather than inventing them", () => {
    const scene = deriveLiveScene({ ...emptyOverlay("hello"), stakes });
    expect(scene.laneA.every((node) => node.ms === null)).toBe(true);
    expect(scene.laneA.map((node) => node.label)).toContain("Pre-flight checks");
    expect(scene.laneA.find((node) => node.label === "Route decision")?.v).toBe("GPT-OSS-20B");
  });

  it("builds lane B rows from the signals that actually arrived", () => {
    const scene = deriveLiveScene({
      ...emptyOverlay("hello"),
      signals: [
        { sentence_idx: 0, name: "grounding.citation_unsupported", prob: 0.94 },
        { sentence_idx: 0, name: "grounding.question_drift", prob: 0.1 },
      ],
    });
    expect(scene.laneB[0].label).toBe("grounding.citation_unsupported");
    expect(scene.laneB[0].v).toBe("0.94");
    expect(scene.laneB[0].st).toBe("fail");
    expect(scene.laneB[1].st).toBe("pass");
  });

  it("prices the ladder from the loss table and keeps unavailable rows labelled", () => {
    const scene = deriveLiveScene({ ...emptyOverlay("hello"), decision, lossTable });
    expect(scene.costs.L4).toBe(74);
    expect(scene.chosen).toBe("L4");
    expect(scene.stamp).toBe("L4 HOLD");
    expect(scene.costMeta?.L3).toEqual({ available: false, reason: "no stronger model configured" });
  });

  it("falls back to plain statements when the stream reported nothing", () => {
    const scene = deriveLiveScene(emptyOverlay("hello"));
    expect(scene.why).toBe("waiting for interlock.decision");
    expect(scene.counterfactual).toContain("no counterfactual");
    expect(scene.final).toBe("— no content was released —");
  });
});

describe("engine in live mode", () => {
  it("advances forward only as frames arrive and never rewinds", () => {
    const engine = new TraceEngine({ autoplay: false });
    engine.submit(true);
    expect(engine.getState().stage).toBe(0);

    engine.applyStakes(stakes);
    expect(engine.getState().stage).toBe(0);

    engine.appendGeneration("Your claim ");
    expect(engine.getState().stage).toBe(1);
    expect(engine.getState().genText).toBe("Your claim ");

    engine.applySignal({ sentence_idx: 0, name: "grounding.citation_unsupported", prob: 0.94 });
    expect(engine.getState().stage).toBe(2);

    engine.applyDecision(decision);
    expect(engine.getState().stage).toBe(3);
    expect(engine.getState().chosenShown).toBe(true);

    // A late generation delta must not drag the viewer back to stage 02.
    engine.appendGeneration("more text");
    expect(engine.getState().stage).toBe(3);
    engine.destroy();
  });

  it("marks every row the loss table priced", () => {
    const engine = new TraceEngine({ autoplay: false });
    engine.submit(true);
    engine.applyDecision(decision);
    engine.applyLossTable(lossTable, 15);
    expect(engine.getState().ladderSt.L0).toBe("priced");
    expect(engine.getState().ladderSt.L4).toBe("priced");
    expect(engine.getState().counts[0]).toBe(15);
    engine.destroy();
  });

  it("does not run the fixture choreography while live", () => {
    const engine = new TraceEngine({ autoplay: false });
    engine.submit(true);
    // Lane A node states come from the overlay, so nothing is "checking".
    expect(Object.values(engine.getState().nodeSt)).not.toContain("active");
    engine.destroy();
  });

  it("finishes the run when the stream fails", () => {
    const engine = new TraceEngine({ autoplay: false });
    engine.submit(true);
    engine.finishLive("connection reset");
    expect(engine.getState().live?.error).toBe("connection reset");
    expect(engine.getState().log.at(-1)).toContain("connection reset");
    engine.destroy();
  });
});
