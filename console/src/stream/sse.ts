import type { InterlockEventName, ParsedFrame } from "../domain/contracts";
import { parseInterlockEvent, parseOpenAIChunk } from "../domain/eventValidation";

const knownEvents = new Set<InterlockEventName>([
  "interlock.stakes",
  "interlock.signal",
  "interlock.decision",
  "interlock.hold",
]);

interface SseParserOptions {
  onResumeToken?: (holdId: string, token: string) => void;
}

export class SseParser {
  private buffer = "";
  private readonly onResumeToken?: (holdId: string, token: string) => void;

  constructor(options: SseParserOptions = {}) {
    this.onResumeToken = options.onResumeToken;
  }

  push(chunk: string): ParsedFrame[] {
    this.buffer += chunk;
    const frames: ParsedFrame[] = [];
    let boundary = this.buffer.search(/\r?\n\r?\n/);

    while (boundary >= 0) {
      const rawFrame = this.buffer.slice(0, boundary);
      const separator = this.buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? "\n\n";
      this.buffer = this.buffer.slice(boundary + separator.length);
      const parsed = this.parseFrame(rawFrame);
      if (parsed) frames.push(parsed);
      boundary = this.buffer.search(/\r?\n\r?\n/);
    }

    return frames;
  }

  finish(finalChunk = ""): ParsedFrame[] {
    const frames = this.push(finalChunk);
    if (this.buffer.trim()) {
      const parsed = this.parseFrame(this.buffer);
      if (parsed) frames.push(parsed);
    }
    this.buffer = "";
    return frames;
  }

  private parseFrame(rawFrame: string): ParsedFrame | null {
    if (!rawFrame.trim()) return null;

    let eventName: string | undefined;
    const dataLines: string[] = [];
    for (const line of rawFrame.split(/\r?\n/)) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon >= 0 ? line.slice(0, colon) : line;
      const rawValue = colon >= 0 ? line.slice(colon + 1) : "";
      const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
      if (field === "event") eventName = value;
      if (field === "data") dataLines.push(value);
    }

    if (dataLines.length === 0) {
      return { kind: "diagnostic", code: "malformed-frame", message: "SSE frame has no data field" };
    }

    const body = dataLines.join("\n");
    if (!eventName && body === "[DONE]") return { kind: "done" };

    if (eventName && !knownEvents.has(eventName as InterlockEventName)) {
      return {
        kind: "diagnostic",
        code: "unknown-event",
        message: `Ignored unsupported SSE event ${eventName}`,
      };
    }

    let data: unknown;
    try {
      data = JSON.parse(body);
    } catch {
      return { kind: "diagnostic", code: "malformed-json", message: "SSE data was not valid JSON" };
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      return { kind: "diagnostic", code: "malformed-frame", message: "SSE data must be a JSON object" };
    }

    if (!eventName) return parseOpenAIChunk(data);

    if (eventName === "interlock.hold") {
      const holdData = { ...(data as Record<string, unknown>) };
      const token = holdData.resume_token;
      delete holdData.resume_token;
      const frame = parseInterlockEvent(eventName, holdData);
      if (frame.kind === "interlock" && frame.event === "interlock.hold" && typeof token === "string") {
        this.onResumeToken?.(frame.data.hold_id, token);
      }
      return frame;
    }

    return parseInterlockEvent(eventName as InterlockEventName, data);
  }
}
