import { color, font, rgba, TONE, type SurfaceTone } from "./tokens";
import type { BoardState } from "./traceEngine";

const FLAP_GRADIENT = "linear-gradient(180deg,#262a20 0 48%,#0b0c09 48% 52%,#1e2118 52% 100%)";

/**
 * The stage announcement board. Every glyph is sand while any cell is still
 * flipping; once the whole board settles it takes the stage's tone, which is
 * how a viewer reads the verdict from across a room.
 */
export function SplitFlapBoard({ board, tone }: { board: BoardState | null; tone: SurfaceTone }) {
  if (!board) return null;
  const settled = board.done;
  const message = board.cur.map((row) => row.join("")).join(" / ").replace(/\s+/g, " ").trim();

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: "6px" }}
      role="status"
      aria-live="polite"
      aria-label={settled ? message : "stage announcement flipping"}
    >
      {board.cur.map((row, ri) => (
        <div key={ri} style={{ display: "flex", gap: "4px" }} aria-hidden="true">
          {row.map((ch, ci) => {
            const blank = ch === " ";
            return (
              <div
                key={ci}
                style={{
                  width: "30px",
                  height: "42px",
                  display: "grid",
                  placeItems: "center",
                  borderRadius: "3px",
                  background: blank ? "rgba(230,225,215,.02)" : FLAP_GRADIENT,
                  border: `1px solid ${blank ? "rgba(230,225,215,.04)" : "rgba(230,225,215,.07)"}`,
                  font: `700 19px ${font.mono}`,
                  color: settled ? TONE[tone] : color.accent,
                  textShadow: settled ? `0 0 14px ${rgba(TONE[tone], 0.5)}` : "none",
                  transition: "color .25s ease",
                }}
              >
                {blank ? " " : ch}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
