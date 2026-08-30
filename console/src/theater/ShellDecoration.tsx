import { useEffect, useRef } from "react";

/** Drifting dots, at the positions and phases the design specifies. */
const DOTS = [
  { left: "22%", top: "14%", size: 3, duration: "8s", delay: "1.2s" },
  { left: "62%", top: "86%", size: 2, duration: "9.5s", delay: "2s" },
  { left: "41%", top: "9%", size: 2, duration: "11s", delay: ".4s" },
  { left: "78%", top: "91%", size: 3, duration: "10s", delay: "3s" },
];

/**
 * Background layer: grid, corner glow, drifting dots and a cursor-follow glow.
 * The glow is moved by mutating the node's style directly — routing mousemove
 * through React state would re-render the whole trace on every pixel.
 */
export function ShellDecoration() {
  const glow = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      const node = glow.current;
      if (!node) return;
      node.style.transform = `translate(${event.clientX - 210}px,${event.clientY - 210}px)`;
      node.style.opacity = "1";
    };
    const onLeave = () => {
      if (glow.current) glow.current.style.opacity = "0";
    };
    window.addEventListener("mousemove", onMove);
    document.addEventListener("mouseleave", onLeave);
    return () => {
      window.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseleave", onLeave);
    };
  }, []);

  return (
    <div aria-hidden="true" style={{ position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0 }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage:
            "repeating-linear-gradient(0deg, rgba(230,225,215,.02) 0 1px, transparent 1px 60px)," +
            "repeating-linear-gradient(90deg, rgba(230,225,215,.02) 0 1px, transparent 1px 60px)",
          backgroundSize: "60px 60px",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "radial-gradient(circle at 82% -12%, rgba(200,180,160,.12) 0%, transparent 55%)",
        }}
      />
      {DOTS.map((dot, index) => (
        <span
          key={index}
          style={{
            position: "absolute",
            left: dot.left,
            top: dot.top,
            width: `${dot.size}px`,
            height: `${dot.size}px`,
            borderRadius: "50%",
            background: "#c8b4a0",
            opacity: 0.2,
            animation: `ilFloat ${dot.duration} ease-in-out ${dot.delay} infinite`,
          }}
        />
      ))}
      <div
        ref={glow}
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: "420px",
          height: "420px",
          borderRadius: "50%",
          filter: "blur(70px)",
          background: "radial-gradient(circle, rgba(200,180,160,.1) 0%, transparent 70%)",
          opacity: 0,
          transition: "opacity .4s ease",
        }}
      />
    </div>
  );
}
