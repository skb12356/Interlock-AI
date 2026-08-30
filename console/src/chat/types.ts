import type { LiveOverlay } from "../theater/liveScene";

/** One turn: the prompt, the answer Interlock released, and the trace behind it. */
export interface ChatTurn {
  id: string;
  prompt: string;
  createdAt: number;
  status: "streaming" | "complete" | "failed";
  /** The released text — what the customer would actually see. */
  answer: string;
  /** How far the stream got, 0..6. */
  stage: number;
  durationMs: number | null;
  error: string | null;
  /** Everything the stream reported, kept so the trace can be reopened later. */
  overlay: LiveOverlay | null;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  turns: ChatTurn[];
}

export const SESSION_STORAGE_KEY = "interlock.console.sessions.v1";
