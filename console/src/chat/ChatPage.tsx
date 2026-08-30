import { useEffect, useRef, useState, type FormEvent } from "react";

import { InterlockLockup } from "../theater/InterlockMark";
import { deriveLiveScene, releasedText } from "../theater/liveScene";
import { MicroLabel } from "../theater/primitives";
import { color, font, radius, TONE } from "../theater/tokens";
import { ActionStamp, StageProgress } from "./StageProgress";
import type { ChatSession, ChatTurn } from "./types";

/** Who the console greets. A deployment would take this from the signed-in session. */
const OPERATOR_NAME = "Soham";

export interface StreamingTurn {
  turnId: string;
  stage: number;
  answer: string;
}

/**
 * The console's front door: a chat session per line of enquiry, with each answer
 * carrying the trace that produced it. Nothing here is a mock — every turn is a
 * real request through the gateway.
 */
export function ChatPage({
  session,
  streaming,
  busy,
  error,
  onSubmit,
  onSeeItLive,
}: {
  session: ChatSession | null;
  streaming: StreamingTurn | null;
  busy: boolean;
  error: string | null;
  onSubmit: (prompt: string) => void;
  onSeeItLive: (turn: ChatTurn) => void;
}) {
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement | null>(null);
  const turns = session?.turns ?? [];

  useEffect(() => {
    // Guarded: not every environment the console renders in implements it.
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [turns.length, streaming?.answer, streaming?.stage]);

  const send = () => {
    const prompt = draft.trim();
    if (!prompt || busy) return;
    setDraft("");
    onSubmit(prompt);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    send();
  };

  const composer = (
    <form
      onSubmit={submit}
      style={{
        width: "100%",
        maxWidth: "720px",
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "8px 8px 8px 18px",
        borderRadius: radius.card,
        border: "1px solid rgba(230,225,215,.14)",
        background: color.bgPanel,
        boxShadow: "0 24px 60px rgba(0,0,0,.45)",
      }}
    >
      <span aria-hidden="true" style={{ font: `500 14px ${font.mono}`, color: color.accent }}>
        &gt;
      </span>
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          // Explicit rather than relying on implicit form submission, which some
          // environments do not fire.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        }}
        placeholder="Ask anything"
        aria-label="Ask the bank assistant"
        disabled={busy}
        style={{
          flex: 1,
          minWidth: 0,
          border: "none",
          background: "transparent",
          color: color.text,
          font: `400 15px ${font.sans}`,
          outline: "none",
        }}
      />
      <button
        type="submit"
        disabled={busy || draft.trim().length === 0}
        style={{
          padding: "11px 18px",
          borderRadius: radius.primary,
          border: "none",
          cursor: busy || draft.trim().length === 0 ? "not-allowed" : "pointer",
          background: busy ? "rgba(200,180,160,.3)" : color.accent,
          color: color.onAccent,
          font: `600 13px ${font.sans}`,
          whiteSpace: "nowrap",
        }}
      >
        {busy ? "Running…" : "Send"}
      </button>
    </form>
  );

  if (turns.length === 0) {
    return (
      <section
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "26px",
          padding: "40px 26px",
          textAlign: "center",
        }}
        aria-label="Empty session"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "20px", alignItems: "center" }}>
          <InterlockLockup />
          <h1 style={{ margin: 0, font: `300 34px/1.15 ${font.sans}`, letterSpacing: "-.02em" }}>
            How can I help, {OPERATOR_NAME}?
          </h1>
          <p style={{ margin: 0, maxWidth: "520px", font: `400 14px/1.7 ${font.sans}`, color: color.textDim }}>
            Ask a question. The answer arrives with the seven stages that priced it, and{" "}
            <em style={{ color: color.accent, fontStyle: "normal" }}>see it live</em> opens the full trace.
          </p>
        </div>
        {composer}
        {error ? <ErrorLine message={error} /> : null}
      </section>
    );
  }

  return (
    <section
      style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
      aria-label="Session transcript"
    >
      <div className="il-scroll" style={{ flex: 1, minHeight: 0, padding: "26px 26px 10px" }}>
        <div style={{ maxWidth: "780px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "26px" }}>
          {turns.map((turn) => (
            <TurnView
              key={turn.id}
              turn={turn}
              streaming={streaming?.turnId === turn.id ? streaming : null}
              onSeeItLive={() => onSeeItLive(turn)}
            />
          ))}
          <div ref={bottom} />
        </div>
      </div>

      <div
        style={{
          flex: "none",
          padding: "12px 26px 22px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "10px",
          borderTop: `1px solid ${color.lineSoft}`,
          background: "rgba(11,12,9,.5)",
        }}
      >
        {composer}
        {error ? <ErrorLine message={error} /> : null}
      </div>
    </section>
  );
}

function ErrorLine({ message }: { message: string }) {
  return (
    <p role="alert" style={{ margin: 0, font: `400 12px ${font.mono}`, color: color.fail }}>
      {message}
    </p>
  );
}

function TurnView({
  turn,
  streaming,
  onSeeItLive,
}: {
  turn: ChatTurn;
  streaming: StreamingTurn | null;
  onSeeItLive: () => void;
}) {
  const status = streaming ? "streaming" : turn.status;
  const stage = streaming ? streaming.stage : turn.stage;
  const scene = turn.overlay ? deriveLiveScene(turn.overlay) : null;
  // The transcript shows what was released, not the model's scratch pad.
  const answer = releasedText(streaming ? streaming.answer : turn.answer);

  return (
    <article style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <p
          style={{
            margin: 0,
            maxWidth: "560px",
            padding: "12px 16px",
            borderRadius: radius.card,
            background: "rgba(200,180,160,.1)",
            border: "1px solid rgba(200,180,160,.24)",
            font: `400 14px/1.6 ${font.sans}`,
          }}
        >
          {turn.prompt}
        </p>
      </div>

      <StageProgress
        stage={stage}
        status={status}
        durationMs={turn.durationMs}
        onSeeItLive={onSeeItLive}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {scene && status !== "streaming" ? (
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <ActionStamp stamp={scene.stamp} tone={TONE[scene.stampTone]} />
            <MicroLabel>
              {turn.durationMs === null ? "duration unreported" : `took ${(turn.durationMs / 1000).toFixed(2)} s`}
              {turn.overlay?.decisionLatencyMs === null || turn.overlay?.decisionLatencyMs === undefined
                ? ""
                : ` · decision ${turn.overlay.decisionLatencyMs} ms`}
            </MicroLabel>
          </div>
        ) : null}

        <p style={{ margin: 0, font: `400 15px/1.75 ${font.sans}` }}>
          {answer || (status === "streaming" ? "" : "— no content was released —")}
          {status === "streaming" ? (
            <span
              aria-hidden="true"
              style={{
                display: "inline-block",
                width: "8px",
                height: "17px",
                marginLeft: "3px",
                verticalAlign: "-3px",
                background: color.accent,
                animation: "ilCaret 1s step-end infinite",
              }}
            />
          ) : null}
        </p>

        {turn.error ? <ErrorLine message={turn.error} /> : null}
      </div>
    </article>
  );
}
