import { color, font } from "./tokens";

/**
 * The wordless Interlock mark: two links crossing, which is the product in one
 * shape — the router's decision and the guardrail's decision are the same link.
 * Used where the console has room to introduce itself rather than just label a bar.
 */
export function InterlockMark({ size = 84, glow = true }: { size?: number; glow?: boolean }) {
  const id = "il-mark";
  return (
    <span
      aria-hidden="true"
      style={{
        position: "relative",
        display: "grid",
        placeItems: "center",
        width: `${size}px`,
        height: `${size}px`,
      }}
    >
      {glow ? (
        <span
          style={{
            position: "absolute",
            inset: `-${Math.round(size * 0.45)}px`,
            borderRadius: "50%",
            background: `radial-gradient(circle, rgba(200,180,160,.16) 0%, transparent 68%)`,
            filter: "blur(2px)",
          }}
        />
      ) : null}

      <svg width={size} height={size} viewBox="0 0 100 100" style={{ position: "relative" }}>
        <defs>
          {/* A black copy of the left link's band: the right link vanishes exactly
              where the two cross, which is what makes them read as linked. */}
          <mask id={`${id}-weave`}>
            <rect width="100" height="100" fill="white" />
            <rect x="14" y="26" width="44" height="48" rx="22" fill="none" stroke="black" strokeWidth="13" />
          </mask>
        </defs>

        <rect
          x="42"
          y="26"
          width="44"
          height="48"
          rx="22"
          fill="none"
          stroke={color.pass}
          strokeWidth="6"
          opacity="0.9"
          mask={`url(#${id}-weave)`}
        />
        <rect
          x="14"
          y="26"
          width="44"
          height="48"
          rx="22"
          fill="none"
          stroke={color.accent}
          strokeWidth="6"
        />
      </svg>
    </span>
  );
}

/** The mark with the wordmark under it, for the console's front door. */
export function InterlockLockup({ size = 84 }: { size?: number }) {
  return (
    <span style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
      <InterlockMark size={size} />
      <span
        style={{
          font: `500 10px ${font.mono}`,
          letterSpacing: ".34em",
          textTransform: "uppercase",
          color: color.textMeta,
          paddingLeft: ".34em",
        }}
      >
        Interlock
      </span>
    </span>
  );
}
