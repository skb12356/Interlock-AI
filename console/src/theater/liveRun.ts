import { streamChat, type ChatRequest } from "../api/chatClient";
import { getDecisionDetail } from "../api/consoleClient";
import type { ParsedFrame } from "../domain/contracts";
import type { TraceEngine } from "./traceEngine";
import { ResumeTokenVault } from "../security/resumeTokens";

/** Decision details land just after the stream; this is how long we wait for one. */
const DETAIL_WAITS_MS = [0, 120, 300];

/**
 * Bridges the gateway's SSE contract onto the stage machine. Nothing is
 * synthesised here: each frame lands on the engine exactly once, and the run
 * finishes (successfully or not) so the trace never hangs mid-stage.
 */
export async function runLiveTrace(
  engine: TraceEngine,
  options: {
    prompt: string;
    replay: boolean;
    signal?: AbortSignal;
    vault?: ResumeTokenVault;
  },
): Promise<void> {
  engine.submit(options.prompt);

  const request: ChatRequest = {
    prompt: options.prompt,
    replay: options.replay,
    signal: options.signal,
  };

  const decisionIds = new Set<string>();

  const onFrame = (frame: ParsedFrame) => {
    if (frame.kind === "openai") {
      const delta = frame.data.choices?.[0]?.delta?.content;
      if (delta) engine.appendGeneration(delta);
      return;
    }
    if (frame.kind !== "interlock") return;
    if (frame.event === "interlock.stakes") engine.applyStakes(frame.data);
    else if (frame.event === "interlock.signal") engine.applySignal(frame.data);
    else if (frame.event === "interlock.decision") {
      if (frame.data.sentence_idx >= 0) decisionIds.add(frame.data.decision_id);
      engine.applyDecision(frame.data);
    }
    else if (frame.event === "interlock.hold") engine.applyHold(frame.data);
  };

  try {
    await streamChat(request, {
      onFrame,
      onResumeToken: (holdId, token) => options.vault?.store(holdId, token),
    });
    // Finish first: the clock must record the time the request actually took,
    // not the time spent chasing its decision detail afterwards.
    engine.finishLive();
    // The loss table is awaited here rather than left in the background so the
    // priced ladder is part of the trace before it is stored. The wait is short:
    // a projection that has not landed by then is reported as unpriced.
    for (const decisionId of decisionIds) {
      const detail = await getDecisionDetail(decisionId, options.signal, fetch, DETAIL_WAITS_MS);
      if (detail) engine.applyLossTable(detail.loss_table, detail.latency_ms);
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    engine.finishLive(error instanceof Error ? error.message : "The stream ended unexpectedly");
    throw error;
  }
}
