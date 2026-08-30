import { STAGES } from "./stages";
import { color, font, rgba } from "./tokens";
import { MicroLabel } from "./primitives";

/**
 * The 60px rail is always visible and always clickable; hovering it slides a
 * 252px overlay out beside it, over the content rather than over the bars.
 * The overlay is absolutely positioned, so the stage never reflows — and
 * because it starts where the rail ends, a click aimed at a bar still lands on
 * that bar instead of on whichever overlay row sits under the cursor.
 */
export function StageRail({
  stage,
  open,
  onOpen,
  onGo,
  sceneLabel,
  sceneOutcome,
}: {
  stage: number;
  open: boolean;
  onOpen: (open: boolean) => void;
  onGo: (index: number) => void;
  sceneLabel: string;
  sceneOutcome: string;
}) {
  return (
    <div
      onMouseEnter={() => onOpen(true)}
      onMouseLeave={() => onOpen(false)}
      style={{
        position: "relative",
        borderRight: `1px solid ${color.lineSoft}`,
        background: color.bgRail,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "22px 0",
        gap: "2px",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          writingMode: "vertical-rl",
          font: `500 8px ${font.mono}`,
          letterSpacing: ".24em",
          textTransform: "uppercase",
          color: color.textMuteAlt,
          marginBottom: "8px",
        }}
      >
        stages
      </span>

      {STAGES.map((item, index) => {
        const current = index === stage;
        const past = index < stage;
        const barColor = current ? color.accent : past ? color.pass : "rgba(230,225,215,.18)";
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onGo(index)}
            aria-label={`Stage ${item.n}, ${item.title}`}
            aria-current={current ? "step" : undefined}
            style={{
              width: "100%",
              padding: "8px 0",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <span
              style={{
                font: `500 8px ${font.mono}`,
                color: current ? color.accent : past ? color.textDim : color.textMute,
                transition: "color .3s ease",
              }}
            >
              {item.n}
            </span>
            <span
              aria-hidden="true"
              style={{
                display: "block",
                height: "3px",
                borderRadius: "2px",
                background: barColor,
                width: current ? "26px" : past ? "18px" : "11px",
                boxShadow: current ? `0 0 12px ${rgba(color.accent, 0.55)}` : "none",
                animation: current ? "ilPulse 1.4s ease-in-out infinite" : "none",
                transition: "all .35s cubic-bezier(.2,.8,.2,1)",
              }}
            />
          </button>
        );
      })}

      <div
        style={{
          position: "absolute",
          left: "60px",
          top: 0,
          bottom: 0,
          width: "252px",
          zIndex: 30,
          display: "flex",
          flexDirection: "column",
          background: color.bgRailPanel,
          backdropFilter: "blur(10px)",
          borderRight: "1px solid rgba(230,225,215,.1)",
          boxShadow: "26px 0 60px rgba(0,0,0,.5)",
          opacity: open ? 1 : 0,
          transform: open ? "none" : "translateX(-14px)",
          pointerEvents: open ? "auto" : "none",
          transition: "opacity .28s ease, transform .28s cubic-bezier(.2,.8,.2,1)",
          padding: "22px 0 16px",
        }}
      >
        <MicroLabel style={{ padding: "0 20px 12px" }}>Trace stages</MicroLabel>
        {STAGES.map((item, index) => {
          const current = index === stage;
          const past = index < stage;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onGo(index)}
              tabIndex={open ? 0 : -1}
              style={{
                display: "flex",
                gap: "12px",
                alignItems: "center",
                padding: "11px 20px",
                width: "100%",
                border: "none",
                cursor: "pointer",
                textAlign: "left",
                background: current ? "rgba(200,180,160,.09)" : "transparent",
                boxShadow: current ? `inset 2px 0 ${color.accent}` : "none",
                transition: "background .3s ease",
              }}
            >
              <span style={{ font: `500 10px ${font.mono}`, color: color.textFaint }}>{item.n}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: "3px", flex: 1 }}>
                <span
                  style={{
                    font: `${current ? 600 : 400} 12px ${font.sans}`,
                    color: current ? color.text : past ? color.textSoftAlt : color.textDim,
                  }}
                >
                  {item.title}
                </span>
                <span style={{ font: `400 9px ${font.mono}`, color: color.textFaint }}>{item.sub}</span>
              </span>
              <span
                aria-hidden="true"
                style={{
                  width: "6px",
                  height: "6px",
                  borderRadius: "50%",
                  background: current ? color.accent : past ? color.pass : "rgba(230,225,215,.12)",
                  animation: current ? "ilPulse 1.4s ease-in-out infinite" : "none",
                }}
              />
            </button>
          );
        })}
        <div
          style={{
            marginTop: "auto",
            borderTop: `1px solid ${color.lineSoft}`,
            padding: "16px 20px 0",
            display: "flex",
            flexDirection: "column",
            gap: "5px",
          }}
        >
          <MicroLabel>Scene</MicroLabel>
          <span style={{ font: `500 12px ${font.sans}` }}>{sceneLabel}</span>
          <span style={{ font: `400 10px ${font.mono}`, color: color.textDim }}>{sceneOutcome}</span>
        </div>
      </div>
    </div>
  );
}
