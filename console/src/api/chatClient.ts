import type { DecisionDetail, ParsedFrame } from "../domain/contracts";
import { SseParser } from "../stream/sse";
import { getDecisionDetail } from "./consoleClient";

export interface ChatRequest {
  prompt: string;
  scenario: "clean" | "scene1" | "held" | "blocked";
  signal?: AbortSignal;
}

export interface ChatHandlers {
  onRequestId?: (requestId: string) => void;
  onFrame: (frame: ParsedFrame) => void;
  onResumeToken?: (holdId: string, token: string) => void;
  onDecisionDetail?: (detail: DecisionDetail) => void;
  onDiagnostic?: (message: string) => void;
}

export async function streamChat(
  request: ChatRequest,
  handlers: ChatHandlers,
  fetcher: typeof fetch = fetch,
): Promise<{ requestId: string; replay: boolean }> {
  const response = await fetcher("/gateway/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: "interlock",
      messages: [{ role: "user", content: request.prompt }],
      stream: true,
      scenario: request.scenario,
    }),
    signal: request.signal,
  });
  if (!response.ok) throw new Error(`Chat request failed with ${response.status}`);
  if (!response.body) throw new Error("Chat response did not include a stream");

  const requestId =
    response.headers.get("x-interlock-request-id") ?? `req_browser_${Date.now().toString(36)}`;
  handlers.onRequestId?.(requestId);
  const decisionIds = new Set<string>();
  const parser = new SseParser({ onResumeToken: handlers.onResumeToken });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let completed = false;
  const emit = (frame: ParsedFrame) => {
    if (frame.kind === "done") completed = true;
    if (frame.kind === "interlock" && frame.event === "interlock.decision") {
      decisionIds.add(frame.data.decision_id);
    }
    handlers.onFrame(frame);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const frame of parser.push(decoder.decode(value, { stream: true }))) emit(frame);
  }
  for (const frame of parser.finish(decoder.decode())) emit(frame);
  if (!completed) throw new Error("Chat stream ended before [DONE]");

  if (handlers.onDecisionDetail) {
    for (const decisionId of decisionIds) {
      void getDecisionDetail(decisionId, request.signal, fetcher)
        .then((detail) => {
          if (detail) handlers.onDecisionDetail?.(detail);
          else handlers.onDiagnostic?.(`Decision detail ${decisionId} is not available yet`);
        })
        .catch((error: unknown) => {
          handlers.onDiagnostic?.(error instanceof Error ? error.message : "Decision detail request failed");
        });
    }
  }

  return {
    requestId,
    replay: response.headers.has("x-interlock-replay"),
  };
}
