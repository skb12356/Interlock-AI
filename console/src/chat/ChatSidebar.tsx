import { useState } from "react";

import { color, font, radius } from "../theater/tokens";
import { MicroLabel } from "../theater/primitives";
import type { ChatSession } from "./types";

/** A bin, drawn rather than pulled in as a dependency for one glyph. */
function BinIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M2.5 4h11M6.5 4V2.8c0-.4.3-.8.8-.8h1.4c.5 0 .8.4.8.8V4M4 4l.6 9c0 .6.5 1 1 1h4.8c.5 0 1-.4 1-1L12 4M6.6 7v4M9.4 7v4"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Sessions only: no library, no projects — this console does one thing. */
export function ChatSidebar({
  sessions,
  activeId,
  onNew,
  onOpen,
  onDelete,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  // Deleting a session cannot be undone, so the bin asks once before it bites.
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  return (
    <aside
      style={{
        width: "252px",
        flex: "none",
        borderRight: `1px solid ${color.lineSoft}`,
        background: "rgba(13,14,11,.55)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        padding: "18px 14px",
        gap: "16px",
      }}
      aria-label="Chat sessions"
    >
      <button
        type="button"
        onClick={onNew}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          padding: "11px 14px",
          borderRadius: radius.panel,
          border: `1px solid ${color.line}`,
          background: "rgba(230,225,215,.03)",
          color: color.text,
          cursor: "pointer",
          font: `500 13px ${font.sans}`,
        }}
      >
        <span aria-hidden="true" style={{ color: color.accent }}>
          ＋
        </span>
        New chat session
      </button>

      <div style={{ display: "flex", flexDirection: "column", gap: "8px", minHeight: 0 }}>
        <MicroLabel>Sessions</MicroLabel>
        <div className="il-scroll" style={{ display: "flex", flexDirection: "column", gap: "2px", minHeight: 0 }}>
          {sessions.length === 0 ? (
            <p style={{ margin: 0, font: `400 12px/1.6 ${font.sans}`, color: color.textFaint }}>
              Nothing yet. Ask the bank assistant something and Interlock will trace it.
            </p>
          ) : (
            sessions.map((session) => {
              const active = session.id === activeId;
              const confirming = confirmingId === session.id;
              return (
                <div
                  key={session.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "4px",
                    borderRadius: radius.button,
                    background: active ? "rgba(200,180,160,.1)" : "transparent",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onOpen(session.id)}
                    aria-current={active ? "true" : undefined}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      textAlign: "left",
                      padding: "9px 4px 9px 12px",
                      borderRadius: radius.button,
                      border: "1px solid transparent",
                      background: "transparent",
                      color: active ? color.text : color.textDim,
                      cursor: "pointer",
                      font: `400 12px ${font.sans}`,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {session.title}
                  </button>

                  {confirming ? (
                    <span style={{ display: "flex", gap: "4px", paddingRight: "6px", flex: "none" }}>
                      <RowAction
                        label={`Confirm deleting ${session.title}`}
                        text="Delete"
                        tone={color.fail}
                        onClick={() => {
                          setConfirmingId(null);
                          onDelete(session.id);
                        }}
                      />
                      <RowAction
                        label="Keep session"
                        text="Keep"
                        tone={color.textDim}
                        onClick={() => setConfirmingId(null)}
                      />
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmingId(session.id)}
                      aria-label={`Delete session ${session.title}`}
                      title="Delete session"
                      style={{
                        flex: "none",
                        display: "grid",
                        placeItems: "center",
                        width: "28px",
                        height: "28px",
                        marginRight: "4px",
                        borderRadius: radius.button,
                        border: "1px solid transparent",
                        background: "transparent",
                        color: color.textMute,
                        cursor: "pointer",
                      }}
                    >
                      <BinIcon />
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
}

function RowAction({
  label,
  text,
  tone,
  onClick,
}: {
  label: string;
  text: string;
  tone: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      style={{
        padding: "5px 8px",
        borderRadius: radius.button,
        border: `1px solid ${tone}`,
        background: "transparent",
        color: tone,
        cursor: "pointer",
        font: `500 10px ${font.sans}`,
      }}
    >
      {text}
    </button>
  );
}
