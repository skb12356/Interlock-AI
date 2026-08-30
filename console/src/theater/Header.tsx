import { color, font, radius } from "./tokens";
import { MicroLabel } from "./primitives";
import type { Mode } from "./traceEngine";

export type View = "live" | "reviews" | "evidence";

const NAV: Array<{ id: View; n: string; label: string }> = [
  { id: "live", n: "01", label: "Live" },
  { id: "reviews", n: "02", label: "Reviews" },
  { id: "evidence", n: "03", label: "Evidence" },
];

export function Header({
  view,
  onView,
  mode,
  onToggleMode,
  connectionLabel,
}: {
  view: View;
  onView: (view: View) => void;
  mode: Mode;
  onToggleMode: () => void;
  connectionLabel: string;
}) {
  const modeTone = mode === "demo" ? color.warn : color.pass;
  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 20,
        minHeight: "60px",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "12px 18px",
        padding: "10px 24px",
        borderBottom: `1px solid ${color.lineSoft}`,
        background: color.bgHeader,
        backdropFilter: "blur(8px)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <span
          aria-hidden="true"
          style={{
            width: "26px",
            height: "26px",
            display: "grid",
            placeItems: "center",
            border: "1px solid rgba(230,225,215,.28)",
            borderRadius: radius.mark,
            font: `700 10px ${font.mono}`,
            letterSpacing: "-.06em",
            color: color.accent,
          }}
        >
          I/L
        </span>
        <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
          <strong style={{ font: `600 13px ${font.sans}` }}>Interlock</strong>
          <span
            style={{
              font: `500 8px ${font.mono}`,
              letterSpacing: ".18em",
              textTransform: "uppercase",
              color: color.textMeta,
            }}
          >
            Control plane
          </span>
        </span>
      </div>

      <nav aria-label="Console workspaces" style={{ display: "flex", gap: "4px" }}>
        {NAV.map((item) => {
          const active = view === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onView(item.id)}
              aria-current={active ? "page" : undefined}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "7px",
                padding: "7px 12px",
                borderRadius: radius.button,
                cursor: "pointer",
                border: `1px solid ${active ? "rgba(200,180,160,.35)" : "transparent"}`,
                background: active ? "rgba(200,180,160,.1)" : "transparent",
                color: active ? color.text : color.textDim,
                font: `500 12px ${font.sans}`,
              }}
            >
              <span style={{ font: `500 9px ${font.mono}`, color: color.textFaint }}>{item.n}</span>
              {item.label}
            </button>
          );
        })}
      </nav>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "14px" }}>
        <button
          type="button"
          onClick={onToggleMode}
          aria-pressed={mode === "live"}
          style={{
            padding: "6px 12px",
            borderRadius: radius.pill,
            cursor: "pointer",
            border: `1px solid ${mode === "demo" ? "rgba(217,165,92,.4)" : "rgba(154,209,127,.4)"}`,
            background: mode === "demo" ? "rgba(217,165,92,.09)" : "rgba(154,209,127,.09)",
            color: modeTone,
            font: `500 9px ${font.mono}`,
            letterSpacing: ".14em",
            textTransform: "uppercase",
            whiteSpace: "nowrap",
            flex: "none",
          }}
        >
          {mode === "demo" ? "Demo trace" : "Live backend"}
        </button>
        <span style={{ display: "flex", alignItems: "center", gap: "8px", whiteSpace: "nowrap", flex: "none" }}>
          <span
            aria-hidden="true"
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              background: modeTone,
              animation: "ilPulse 2s ease-in-out infinite",
            }}
          />
          <MicroLabel style={{ letterSpacing: ".14em" }}>{connectionLabel}</MicroLabel>
        </span>
      </div>
    </header>
  );
}
