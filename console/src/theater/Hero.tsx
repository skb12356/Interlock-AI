import type { FormEvent } from "react";

import { SCENE_IDS, SCENES, type SceneId } from "./scenes";
import { color, font, radius, rgba, TONE } from "./tokens";

const HEADLINE_LINE_ONE = [
  { word: "Routing", delay: ".1s" },
  { word: "and", delay: ".25s" },
  { word: "guarding", delay: ".4s" },
  { word: "are", delay: ".55s" },
];

const HEADLINE_LINE_TWO = [
  { word: "the", delay: ".7s" },
  { word: "same", delay: ".85s" },
  { word: "decision.", delay: "1s" },
];

const HERO_STATS = [
  { value: "100%", label: "Pre-action catch rate", tone: color.pass },
  { value: "15 ms", label: "Added p95 latency" },
  { value: "0.04%", label: "Verification cost" },
  { value: "200 / 43", label: "Cases / defective" },
];

const CORNER_DELAYS = ["1.6s", "1.75s", "1.9s", "2.05s"];

function CornerMarks() {
  const corners = [
    { top: "26px", left: "30px", borderTop: true, borderLeft: true },
    { top: "26px", right: "30px", borderTop: true, borderRight: true },
    { bottom: "26px", left: "30px", borderBottom: true, borderLeft: true },
    { bottom: "26px", right: "30px", borderBottom: true, borderRight: true },
  ];
  const edge = "1px solid rgba(200,180,160,.22)";
  return (
    <>
      {corners.map((corner, index) => (
        <span
          key={index}
          aria-hidden="true"
          style={{
            position: "absolute",
            width: "36px",
            height: "36px",
            top: corner.top,
            bottom: corner.bottom,
            left: corner.left,
            right: corner.right,
            borderTop: corner.borderTop ? edge : undefined,
            borderBottom: corner.borderBottom ? edge : undefined,
            borderLeft: corner.borderLeft ? edge : undefined,
            borderRight: corner.borderRight ? edge : undefined,
            opacity: 0,
            animation: `ilWord .8s ease-out ${CORNER_DELAYS[index]} forwards`,
          }}
        />
      ))}
    </>
  );
}

export function Hero({
  prompt,
  scene,
  onPrompt,
  onScene,
  onSubmit,
}: {
  prompt: string;
  scene: SceneId;
  onPrompt: (prompt: string) => void;
  onScene: (scene: SceneId) => void;
  onSubmit: () => void;
}) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <section
      style={{
        position: "relative",
        zIndex: 2,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "34px",
        padding: "70px 26px 90px",
        textAlign: "center",
      }}
    >
      <CornerMarks />

      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "8px",
          padding: "6px 12px",
          borderRadius: radius.pill,
          border: "1px solid rgba(230,225,215,.1)",
          font: `500 9px ${font.mono}`,
          letterSpacing: ".18em",
          textTransform: "uppercase",
          color: color.textDim,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: "5px",
            height: "5px",
            borderRadius: "50%",
            background: color.pass,
            animation: "ilPulse 2s ease-in-out infinite",
          }}
        />
        one stakes estimate · two budgets
      </span>

      <h1
        aria-label="Routing and guarding are the same decision."
        style={{
          margin: 0,
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "0 .3em",
          maxWidth: "900px",
          font: `200 clamp(38px,5.4vw,74px)/1.06 ${font.sans}`,
          letterSpacing: "-.03em",
        }}
      >
        {/* Each word animates in on its own, so the sentence is exposed via aria-label above. */}
        {HEADLINE_LINE_ONE.map((item) => (
          <span
            key={item.word}
            style={{ color: color.textHi, opacity: 0, animation: `ilWord .8s ease-out ${item.delay} forwards` }}
          >
            {item.word}
          </span>
        ))}
        <span style={{ flexBasis: "100%", height: 0 }} aria-hidden="true" />
        {HEADLINE_LINE_TWO.map((item) => (
          <span
            key={item.word}
            style={{ color: color.accent, opacity: 0, animation: `ilWord .8s ease-out ${item.delay} forwards` }}
          >
            {item.word}
          </span>
        ))}
      </h1>

      <p
        style={{
          margin: 0,
          maxWidth: "560px",
          font: `300 16px/1.7 ${font.sans}`,
          color: color.textDim,
          opacity: 0,
          animation: "ilWord 1s ease-out 1.25s forwards",
        }}
      >
        Send a request through the lanes. Every check, every priced action and every millisecond is shown as it
        happens.
      </p>

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
          value={prompt}
          onChange={(event) => onPrompt(event.target.value)}
          placeholder="Ask the bank assistant something"
          aria-label="Prompt for the bank assistant"
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
          style={{
            padding: "12px 20px",
            borderRadius: radius.primary,
            border: "none",
            cursor: "pointer",
            background: color.accent,
            color: color.onAccent,
            font: `600 13px ${font.sans}`,
            whiteSpace: "nowrap",
          }}
        >
          Send through Interlock →
        </button>
      </form>

      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "8px" }}>
        {SCENE_IDS.map((id) => {
          const selected = scene === id;
          const tint = TONE[SCENES[id].tone];
          return (
            <button
              key={id}
              type="button"
              aria-pressed={selected}
              onClick={() => onScene(id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "9px",
                padding: "9px 14px",
                borderRadius: radius.pill,
                cursor: "pointer",
                border: `1px solid ${selected ? rgba(tint, 0.45) : color.line}`,
                background: selected ? rgba(tint, 0.08) : "transparent",
                color: selected ? color.text : color.textDim,
                font: `500 12px ${font.sans}`,
                transition: "all .25s ease",
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: "6px",
                  height: "6px",
                  borderRadius: "50%",
                  background: tint,
                  boxShadow: selected ? `0 0 10px ${tint}` : "none",
                }}
              />
              {SCENES[id].label}
              <span style={{ font: `400 9px ${font.mono}`, color: color.textFaint }}>{SCENES[id].outcome}</span>
            </button>
          );
        })}
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          border: `1px solid ${color.line}`,
          borderRadius: radius.panel,
        }}
      >
        {HERO_STATS.map((stat, index) => (
          <div
            key={stat.label}
            style={{
              padding: "14px 22px",
              textAlign: "left",
              borderLeft: index === 0 ? "none" : `1px solid ${color.lineSoft}`,
            }}
          >
            <div style={{ font: `700 20px ${font.mono}`, color: stat.tone ?? color.text }}>{stat.value}</div>
            <div
              style={{
                marginTop: "5px",
                font: `500 9px ${font.mono}`,
                letterSpacing: ".16em",
                textTransform: "uppercase",
                color: color.textFaint,
              }}
            >
              {stat.label}
            </div>
          </div>
        ))}
      </div>

      <p
        style={{
          margin: 0,
          font: `400 9px ${font.mono}`,
          letterSpacing: ".14em",
          textTransform: "uppercase",
          color: color.textMute,
        }}
      >
        banking-v3@sha256:0e43e9ba · replays from a cached trace, no network required
      </p>
    </section>
  );
}
