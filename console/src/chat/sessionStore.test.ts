import { describe, expect, it } from "vitest";

import {
  appendTurn,
  createSession,
  createTurn,
  loadSessions,
  patchTurn,
  removeSession,
  saveSessions,
  sortSessions,
  titleFor,
  upsertSession,
} from "./sessionStore";
import { SESSION_STORAGE_KEY } from "./types";

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => Array.from(map.keys())[index] ?? null,
    removeItem: (key: string) => map.delete(key) as unknown as void,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
  } as Storage;
}

describe("chat sessions", () => {
  it("names a session after its first prompt and leaves it alone afterwards", () => {
    let session = createSession();
    expect(session.title).toBe("New session");
    session = appendTurn(session, createTurn("What are the prepayment charges?"));
    expect(session.title).toBe("What are the prepayment charges?");
    session = appendTurn(session, createTurn("And the branch hours?"));
    expect(session.title).toBe("What are the prepayment charges?");
  });

  it("truncates a long title rather than letting it break the sidebar", () => {
    expect(titleFor("x".repeat(80))).toHaveLength(42);
  });

  it("patches a turn in place", () => {
    const turn = createTurn("hello");
    const session = patchTurn(appendTurn(createSession(), turn), turn.id, {
      status: "complete",
      answer: "hi",
      durationMs: 1200,
    });
    expect(session.turns[0].status).toBe("complete");
    expect(session.turns[0].durationMs).toBe(1200);
  });

  it("round-trips through storage, newest session first", () => {
    const storage = memoryStorage();
    const older = { ...createSession(), title: "older", updatedAt: 1 };
    const newer = { ...createSession(), title: "newer", updatedAt: 2 };
    saveSessions(sortSessions(upsertSession([older], newer)), storage);
    expect(loadSessions(storage).map((session) => session.title)).toEqual(["newer", "older"]);
  });

  it("survives a corrupt store instead of failing the console", () => {
    const storage = memoryStorage();
    storage.setItem(SESSION_STORAGE_KEY, "{not json");
    expect(loadSessions(storage)).toEqual([]);
  });
  it("removes one session and leaves the rest alone", () => {
    const keep = createSession();
    const drop = createSession();
    expect(removeSession([keep, drop], drop.id)).toEqual([keep]);
    expect(removeSession([keep], "missing")).toEqual([keep]);
  });

});
