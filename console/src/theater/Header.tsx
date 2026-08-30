import { color, font, radius } from "./tokens";

export type View = "chat" | "live" | "reviews" | "evidence" | "about";

const NAV: Array<{ id: View; label: string }> = [
  { id: "chat", label: "Chat" },
  { id: "live", label: "Trace" },
  { id: "reviews", label: "Reviews" },
  { id: "evidence", label: "Evidence" },
  { id: "about", label: "About" },
];

export type GatewayHealth = "connecting" | "connected" | "replay" | "unavailable";

const HEALTH_TONE: Record<GatewayHealth, string> = {
  connecting: color.warn,
  connected: color.pass,
  replay: color.accent,
  unavailable: color.fail,
};

/**
 * One bar: who you are looking at, where you are, and whether the gateway is
 * answering. There is no mode switch — the console only ever shows live traffic.
 */
export function Header({
  view,
  onView,
  breadcrumb,
  health,
  healthDetail,
  traceAvailable,
}: {
  view: View;
  onView: (view: View) => void;
  breadcrumb: string | null;
  health: GatewayHealth;
  healthDetail: string;
  traceAvailable: boolean;
}) {
  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 20,
        minHeight: "56px",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "10px 18px",
        padding: "9px 20px",
        borderBottom: `1px solid ${color.lineSoft}`,
        background: color.bgHeader,
        backdropFilter: "blur(8px)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0 }}>
        <span
          aria-hidden="true"
          style={{
            width: "24px",
            height: "24px",
            display: "grid",
            placeItems: "center",
            border: `1px solid ${color.accent}`,
            borderRadius: radius.mark,
            font: `700 9px ${font.mono}`,
            letterSpacing: "-.06em",
            color: color.accent,
          }}
        >
          I/L
        </span>
        <strong style={{ font: `600 13px ${font.sans}`, letterSpacing: "-.01em" }}>Interlock</strong>
        {breadcrumb ? (
          <>
            <span aria-hidden="true" style={{ color: color.textMute }}>
              /
            </span>
            <span
              style={{
                font: `400 12px ${font.sans}`,
                color: color.textDim,
                maxWidth: "280px",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {breadcrumb}
            </span>
          </>
        ) : null}
      </div>

      <nav
        aria-label="Console views"
        style={{
          display: "flex",
          gap: "2px",
          padding: "3px",
          borderRadius: radius.pill,
          border: `1px solid ${color.line}`,
          background: "rgba(230,225,215,.02)",
        }}
      >
        {NAV.map((item) => {
          const active = view === item.id;
          const disabled = item.id === "live" && !traceAvailable;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onView(item.id)}
              disabled={disabled}
              aria-current={active ? "page" : undefined}
              title={disabled ? "Run a request first — the trace opens from a chat answer" : undefined}
              style={{
                padding: "6px 14px",
                borderRadius: radius.pill,
                border: "none",
                cursor: disabled ? "not-allowed" : "pointer",
                background: active ? color.accent : "transparent",
                color: active ? color.onAccent : disabled ? color.textMute : color.textDim,
                font: `${active ? 600 : 500} 12px ${font.sans}`,
                transition: "background .2s ease, color .2s ease",
              }}
            >
              {item.label}
            </button>
          );
        })}
      </nav>

      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "5px 12px",
          borderRadius: radius.pill,
          border: `1px solid ${color.line}`,
          whiteSpace: "nowrap",
          flex: "none",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            background: HEALTH_TONE[health],
            animation: health === "connecting" ? "ilPulse 1.4s ease-in-out infinite" : "none",
          }}
        />
        <span
          style={{
            font: `500 9px ${font.mono}`,
            letterSpacing: ".14em",
            textTransform: "uppercase",
            color: color.textDim,
          }}
        >
          {healthDetail}
        </span>
      </div>
    </header>
  );
}
