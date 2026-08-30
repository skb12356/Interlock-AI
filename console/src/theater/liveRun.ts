import { streamChat, type ChatRequest } from "../api/chatClient";
import type { ParsedFrame } from "../domain/contracts";
import type { SceneId } from "./scenes";
import type { TraceEngine } from "./traceEngine";
import { ResumeTokenVault } from "../security/resumeTokens";

/**
 * Bridges the gateway's SSE contract onto the stage machine. Nothing is
 * synthesised here: each frame lands on the engine exactly once, and the run
 * finishes (successfully or not) so the trace never hangs mid-stage.
 */
export async function runLiveTrace(
  engine: TraceEngine,
  options: {
    prompt: string;
    scenario: SceneId;
    replay: boolean;
    signal?: AbortSignal;
    vault?: ResumeTokenVault;
  },
): Promise<void> {
  engine.submit(true);

  const request: ChatRequest = {
    prompt: options.prompt,
    scenario: options.scenario,
    replay: options.replay,
    signal: options.signal,
  };

  const onFrame = (frame: ParsedFrame) => {
    if (frame.kind === "openai") {
      const delta = frame.data.choices?.[0]?.delta?.content;
      if (delta) engine.appendGeneration(delta);
      return;
    }
    if (frame.kind !== "interlock") return;
    if (frame.event === "interlock.stakes") engine.applyStakes(frame.data);
    else if (frame.event === "interlock.signal") engine.applySignal(frame.data);
    else if (frame.event === "interlock.decision") engine.applyDecision(frame.data);
    else if (frame.event === "interlock.hold") engine.applyHold(frame.data);
  };

  try {
    await streamChat(request, {
      onFrame,
      onResumeToken: (holdId, token) => options.vault?.store(holdId, token),
      onDecisionDetail: (detail) => engine.applyLossTable(detail.loss_table, detail.latency_ms),
    });
    engine.finishLive();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    engine.finishLive(error instanceof Error ? error.message : "The stream ended unexpectedly");
    throw error;
  }
}
