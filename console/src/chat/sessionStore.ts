import type { LiveOverlay } from "../theater/liveScene";
import { SESSION_STORAGE_KEY, type ChatSession, type ChatTurn } from "./types";

/**
 * Chat sessions live in the browser only. They are a convenience for whoever is
 * driving the console — the authoritative record of any request is the trace the
 * gateway wrote, not this list — so a cleared browser loses nothing that matters.
 */

function newId(prefix: string): string {
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}_${Date.now().toString(36)}${random}`;
}

/** Sessions are named after their first prompt, the way a chat app does it. */
export function titleFor(prompt: string): string {
  const clean = prompt.trim().replace(/\s+/g, " ");
  if (clean.length <= 42) return clean || "New session";
  return `${clean.slice(0, 41)}…`;
}

export function createSession(): ChatSession {
  const now = Date.now();
  return { id: newId("s"), title: "New session", createdAt: now, updatedAt: now, turns: [] };
}

export function createTurn(prompt: string): ChatTurn {
  return {
    id: newId("t"),
    prompt,
    createdAt: Date.now(),
    status: "streaming",
    answer: "",
    stage: 0,
    durationMs: null,
    error: null,
    overlay: null,
  };
}

export function appendTurn(session: ChatSession, turn: ChatTurn): ChatSession {
  return {
    ...session,
    title: session.turns.length === 0 ? titleFor(turn.prompt) : session.title,
    updatedAt: Date.now(),
    turns: session.turns.concat(turn),
  };
}

export function patchTurn(
  session: ChatSession,
  turnId: string,
  patch: Partial<ChatTurn>,
): ChatSession {
  return {
    ...session,
    updatedAt: Date.now(),
    turns: session.turns.map((turn) => (turn.id === turnId ? { ...turn, ...patch } : turn)),
  };
}

export function upsertSession(sessions: ChatSession[], session: ChatSession): ChatSession[] {
  const index = sessions.findIndex((item) => item.id === session.id);
  if (index === -1) return [session, ...sessions];
  const next = sessions.slice();
  next[index] = session;
  return next;
}

export function removeSession(sessions: ChatSession[], sessionId: string): ChatSession[] {
  return sessions.filter((session) => session.id !== sessionId);
}

/** Newest first, which is the order the sidebar shows them in. */
export function sortSessions(sessions: ChatSession[]): ChatSession[] {
  return sessions.slice().sort((a, b) => b.updatedAt - a.updatedAt);
}

function isSession(value: unknown): value is ChatSession {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<ChatSession>;
  return (
    typeof candidate.id === "string" &&
    typeof candidate.title === "string" &&
    Array.isArray(candidate.turns)
  );
}

export function loadSessions(storage: Storage | undefined = safeStorage()): ChatSession[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return sortSessions(parsed.filter(isSession));
  } catch {
    // A corrupt or unreadable store is not worth failing the console over.
    return [];
  }
}

export function saveSessions(
  sessions: ChatSession[],
  storage: Storage | undefined = safeStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessions.slice(0, 40)));
  } catch {
    // Quota or a private window: the console keeps working without history.
  }
}

function safeStorage(): Storage | undefined {
  try {
    return typeof localStorage === "undefined" ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

/** Strips the parts of an overlay that are not worth persisting. */
export function persistableOverlay(overlay: LiveOverlay): LiveOverlay {
  return { ...overlay, signals: overlay.signals.slice(-24) };
}
