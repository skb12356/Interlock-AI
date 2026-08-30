import { color, font, radius } from "../theater/tokens";
import { MicroLabel } from "../theater/primitives";
import type { ChatSession } from "./types";

/** Sessions only: no library, no projects — this console does one thing. */
export function ChatSidebar({
  sessions,
  activeId,
  onNew,
  onOpen,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
}) {
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
              return (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => onOpen(session.id)}
                  aria-current={active ? "true" : undefined}
                  style={{
                    textAlign: "left",
                    padding: "9px 12px",
                    borderRadius: radius.button,
                    border: "1px solid transparent",
                    background: active ? "rgba(200,180,160,.1)" : "transparent",
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
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
}
