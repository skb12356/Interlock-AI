import type { ConsoleEnvelope } from "../domain/contracts";

const knownProjectionEvents = new Set([
  "interlock.stakes",
  "interlock.signal",
  "interlock.decision",
  "interlock.hold",
]);

interface ProjectionCursor {
  streamId: string | null;
  lastSeq: number;
}

interface SocketLike {
  onopen: (() => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  close: () => void;
}

export type ProjectionStatus = "connecting" | "connected" | "reconnecting" | "stopped";

interface ProjectionConnectionOptions {
  onEnvelope: (envelope: ConsoleEnvelope) => void;
  onStatus?: (status: ProjectionStatus) => void;
  onDiagnostic?: (message: string) => void;
  fetcher?: typeof fetch;
  createSocket?: (url: string) => SocketLike;
  reconnectDelayMs?: number;
  location?: Pick<Location, "protocol" | "host">;
}

interface RecentProjection {
  stream_id: string;
  latest_seq: number;
  events: ConsoleEnvelope[];
}

export function projectionWebSocketUrl(location: Pick<Location, "protocol" | "host">): string {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/console/ws`;
}

function isEnvelope(value: unknown): value is ConsoleEnvelope {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.stream_id === "string" &&
    typeof record.seq === "number" &&
    Number.isInteger(record.seq) &&
    record.seq >= 0 &&
    typeof record.event === "string" &&
    typeof record.ts === "number" &&
    typeof record.replayed === "boolean" &&
    "data" in record
  );
}

export class ProjectionConnection {
  private readonly options: Required<Pick<ProjectionConnectionOptions, "fetcher" | "createSocket" | "reconnectDelayMs" | "location">> & ProjectionConnectionOptions;
  private socket: SocketLike | null = null;
  private reconnectTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
  private stopped = true;
  private generation = 0;
  private recoveringGeneration: number | null = null;
  private pendingEnvelopes: ConsoleEnvelope[] = [];
  private currentCursor: ProjectionCursor = { streamId: null, lastSeq: 0 };

  constructor(options: ProjectionConnectionOptions) {
    this.options = {
      ...options,
      fetcher: options.fetcher ?? fetch,
      createSocket: options.createSocket ?? ((url) => new WebSocket(url) as unknown as SocketLike),
      reconnectDelayMs: options.reconnectDelayMs ?? 1_000,
      location: options.location ?? window.location,
    };
  }

  get cursor(): ProjectionCursor {
    return { ...this.currentCursor };
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.open("connecting");
  }

  stop(): void {
    this.stopped = true;
    this.generation += 1;
    this.recoveringGeneration = null;
    this.pendingEnvelopes = [];
    if (this.reconnectTimer !== null) globalThis.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close();
    this.socket = null;
    this.options.onStatus?.("stopped");
  }

  private open(status: ProjectionStatus): void {
    if (this.stopped) return;
    this.options.onStatus?.(status);
    const socket = this.options.createSocket(projectionWebSocketUrl(this.options.location));
    const generation = ++this.generation;
    this.socket = socket;
    socket.onopen = () => {
      this.options.onStatus?.("connected");
      this.recoveringGeneration = generation;
      this.pendingEnvelopes = [];
      void this.recoverRecent(generation);
    };
    socket.onmessage = (event) => this.handleMessage(event.data, generation);
    socket.onerror = () => {
      if (this.stopped || this.socket !== socket) return;
      this.options.onDiagnostic?.("Projection connection encountered a transport error");
      socket.close();
    };
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      if (this.stopped) return;
      this.options.onStatus?.("reconnecting");
      this.reconnectTimer = globalThis.setTimeout(
        () => this.open("reconnecting"),
        this.options.reconnectDelayMs,
      );
    };
  }

  private handleMessage(data: string, generation: number): void {
    try {
      const value: unknown = JSON.parse(data);
      if (!isEnvelope(value)) {
        this.options.onDiagnostic?.("Projection received an invalid envelope");
        return;
      }
      if (!knownProjectionEvents.has(value.event)) {
        this.options.onDiagnostic?.(`Projection received unsupported event ${value.event}`);
        return;
      }
      if (this.recoveringGeneration === generation) this.pendingEnvelopes.push(value);
      else this.accept(value);
    } catch {
      this.options.onDiagnostic?.("Projection received malformed JSON");
    }
  }

  private accept(envelope: ConsoleEnvelope): void {
    const changedStream = this.currentCursor.streamId !== envelope.stream_id;
    if (!changedStream && envelope.seq <= this.currentCursor.lastSeq) return;
    this.currentCursor = { streamId: envelope.stream_id, lastSeq: envelope.seq };
    this.options.onEnvelope(envelope);
  }

  private async recoverRecent(generation: number): Promise<void> {
    const query = new URLSearchParams({ after: String(this.currentCursor.lastSeq) });
    if (this.currentCursor.streamId) query.set("stream_id", this.currentCursor.streamId);
    try {
      const fetcher = this.options.fetcher;
      const response = await fetcher(`/console/recent?${query.toString()}`);
      if (generation !== this.generation || this.stopped) return;
      if (!response.ok) throw new Error(`Recent projection request failed with ${response.status}`);
      const payload = (await response.json()) as RecentProjection;
      if (generation !== this.generation || this.stopped) return;
      if (
        !payload || typeof payload.stream_id !== "string" ||
        !Number.isInteger(payload.latest_seq) || payload.latest_seq < 0 ||
        !Array.isArray(payload.events)
      ) {
        throw new Error("Recent projection response was malformed");
      }
      for (const event of payload.events) {
        if (!isEnvelope(event)) {
          this.options.onDiagnostic?.("Recent projection contained an invalid envelope");
        } else if (!knownProjectionEvents.has(event.event)) {
          this.options.onDiagnostic?.(`Recent projection contained unsupported event ${event.event}`);
        } else {
          this.accept(event);
        }
      }
      if (this.currentCursor.streamId !== payload.stream_id || payload.latest_seq > this.currentCursor.lastSeq) {
        this.currentCursor = { streamId: payload.stream_id, lastSeq: payload.latest_seq };
      }
      this.finishRecovery(generation);
    } catch (error) {
      if (generation !== this.generation || this.stopped) return;
      this.options.onDiagnostic?.(error instanceof Error ? error.message : "Recent projection recovery failed");
      this.finishRecovery(generation);
    }
  }

  private finishRecovery(generation: number): void {
    if (this.recoveringGeneration !== generation) return;
    const pending = this.pendingEnvelopes;
    this.recoveringGeneration = null;
    this.pendingEnvelopes = [];
    for (const envelope of pending) this.accept(envelope);
  }
}
