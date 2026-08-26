import { describe, expect, it, vi } from "vitest";

import type { ConsoleEnvelope } from "../domain/contracts";
import { ProjectionConnection, projectionWebSocketUrl } from "./projectionClient";

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
}

const envelope = (streamId: string, seq: number): ConsoleEnvelope => ({
  stream_id: streamId,
  seq,
  event: "interlock.stakes",
  data: { impact_inr: seq, reversibility: "reversible", domain: "general", mode: "buffered" },
  ts: seq,
  request_id: "req_1",
  replayed: true,
});

describe("ProjectionConnection", () => {
  it("recovers after the current cursor and suppresses WebSocket replays", async () => {
    const socket = new FakeSocket();
    const seen: ConsoleEnvelope[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ stream_id: "epoch-a", latest_seq: 3, events: [envelope("epoch-a", 3)] }), { status: 200 }),
    );
    const connection = new ProjectionConnection({
      onEnvelope: (event) => seen.push(event),
      fetcher,
      createSocket: () => socket,
    });

    connection.start();
    socket.onopen?.();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledWith("/console/recent?after=0"));
    socket.onmessage?.(new MessageEvent("message", { data: JSON.stringify(envelope("epoch-a", 3)) }));

    expect(seen.map((event) => event.seq)).toEqual([3]);
    expect(connection.cursor).toEqual({ streamId: "epoch-a", lastSeq: 3 });
    connection.stop();
    expect(socket.close).toHaveBeenCalledOnce();
  });

  it("invokes the browser fetch function without rebinding its receiver", async () => {
    const socket = new FakeSocket();
    const fetcher = vi.fn(function (this: unknown) {
      if (this !== undefined) throw new TypeError("Illegal invocation");
      return Promise.resolve(
        new Response(JSON.stringify({ stream_id: "epoch-a", latest_seq: 0, events: [] }), { status: 200 }),
      );
    }) as unknown as typeof fetch;
    const onDiagnostic = vi.fn();
    const connection = new ProjectionConnection({
      onEnvelope: vi.fn(),
      onDiagnostic,
      fetcher,
      createSocket: () => socket,
    });

    connection.start();
    socket.onopen?.();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());

    expect(onDiagnostic).not.toHaveBeenCalled();
    connection.stop();
  });

  it("resets its cursor when the server stream changes", async () => {
    const sockets = [new FakeSocket(), new FakeSocket()];
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ stream_id: "old", latest_seq: 8, events: [envelope("old", 8)] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ stream_id: "new", latest_seq: 1, events: [envelope("new", 1)] }), { status: 200 }));
    let socketIndex = 0;
    const connection = new ProjectionConnection({
      onEnvelope: vi.fn(),
      fetcher,
      createSocket: () => sockets[socketIndex++],
      reconnectDelayMs: 0,
    });

    connection.start();
    sockets[0].onopen?.();
    await vi.waitFor(() => expect(connection.cursor.lastSeq).toBe(8));
    sockets[0].onclose?.();
    await vi.waitFor(() => expect(socketIndex).toBe(2));
    sockets[1].onopen?.();
    await vi.waitFor(() => expect(connection.cursor).toEqual({ streamId: "new", lastSeq: 1 }));

    expect(fetcher).toHaveBeenNthCalledWith(2, "/console/recent?after=8&stream_id=old");
    connection.stop();
  });

  it("reports malformed inbound frames without throwing", () => {
    const socket = new FakeSocket();
    const onDiagnostic = vi.fn();
    const connection = new ProjectionConnection({
      onEnvelope: vi.fn(),
      onDiagnostic,
      fetcher: vi.fn<typeof fetch>(),
      createSocket: () => socket,
    });

    connection.start();
    socket.onmessage?.(new MessageEvent("message", { data: "not json" }));

    expect(onDiagnostic).toHaveBeenCalledWith("Projection received malformed JSON");
    connection.stop();
  });

  it("ignores errors from a socket that was deliberately stopped", () => {
    const socket = new FakeSocket();
    const onDiagnostic = vi.fn();
    const connection = new ProjectionConnection({
      onEnvelope: vi.fn(),
      onDiagnostic,
      fetcher: vi.fn<typeof fetch>(),
      createSocket: () => socket,
    });

    connection.start();
    connection.stop();
    socket.onerror?.();

    expect(onDiagnostic).not.toHaveBeenCalled();
  });
});

describe("projectionWebSocketUrl", () => {
  it("uses the page host and secure WebSocket scheme when appropriate", () => {
    expect(projectionWebSocketUrl({ protocol: "https:", host: "console.example" })).toBe(
      "wss://console.example/console/ws",
    );
  });
});
