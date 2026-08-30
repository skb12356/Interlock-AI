import { SplitFlapBoard } from "./SplitFlapBoard";
import { StageRail } from "./StageRail";
import { GateStage, GenStage, LaneAStage, LaneBStage, LaneCStage, LadderStage, ReleaseStage } from "./stageBodies";
import { STAGES } from "./stages";
import { color, font, radius } from "./tokens";
import { MicroLabel } from "./primitives";
import type { Scene } from "./scenes";
import type { EngineSettings, TraceEngine, TraceUiState } from "./traceEngine";

function formatElapsed(ms: number): string {
  return `${(ms / 1000).toFixed(2)} s`;
}

export function StageView({
  engine,
  state,
  scene,
  settings,
  onReplay,
  onBackToChat,
  onNewSession,
}: {
  engine: TraceEngine;
  state: TraceUiState;
  scene: Scene;
  settings: EngineSettings;
  onReplay: () => void;
  onBackToChat: () => void;
  onNewSession: () => void;
}) {
  const stage = STAGES[state.stage];
  const finished = state.durationMs !== null;

  const stageProps = { scene, state, settings };
  const body =
    stage.key === "laneA" ? (
      <LaneAStage {...stageProps} />
    ) : stage.key === "gen" ? (
      <GenStage {...stageProps} />
    ) : stage.key === "laneB" ? (
      <LaneBStage {...stageProps} />
    ) : stage.key === "ladder" ? (
      <LadderStage {...stageProps} />
    ) : stage.key === "gate" ? (
      <GateStage {...stageProps} />
    ) : stage.key === "release" ? (
      <ReleaseStage {...stageProps} />
    ) : (
      <LaneCStage {...stageProps} onReplay={onReplay} onReset={onBackToChat} />
    );

  // Progress is stage position, not a clock: the stream decides when a stage ends.
  const progress = (((state.stage + (finished ? 1 : 0)) / STAGES.length) * 100).toFixed(2);

  return (
    <div
      style={{
        position: "relative",
        zIndex: 2,
        display: "grid",
        gridTemplateColumns: "60px minmax(0,1fr)",
        minHeight: 0,
        flex: 1,
      }}
    >
      <StageRail
        stage={state.stage}
        open={state.railOpen}
        onOpen={(open) => engine.setRailOpen(open)}
        onGo={(index) => engine.go(index)}
        sceneLabel={scene.label}
        sceneOutcome={scene.outcome}
      />

      <div style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
        <div
          className="il-stage-heading"
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "20px",
            padding: "24px 34px 0",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "16px", minWidth: 0 }}>
            <span
              aria-hidden="true"
              style={{
                font: `700 clamp(48px,6vw,84px)/.82 ${font.mono}`,
                letterSpacing: "-.05em",
                color: "rgba(230,225,215,.1)",
              }}
            >
              {stage.n}
            </span>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", minWidth: 0 }}>
              <h2
                style={{
                  margin: 0,
                  font: `600 clamp(22px,2.4vw,34px)/1.1 ${font.sans}`,
                  letterSpacing: "-.025em",
                }}
              >
                {stage.title}
              </h2>
              <MicroLabel>{stage.sub}</MicroLabel>
            </div>
          </div>
          <div style={{ flex: "none", whiteSpace: "nowrap", textAlign: "right" }}>
            <div style={{ font: `700 22px ${font.mono}`, color: finished ? color.pass : color.accent }}>
              {formatElapsed(finished ? (state.durationMs ?? 0) : state.elapsed)}
            </div>
            <MicroLabel style={{ marginTop: "4px" }}>{finished ? "Time taken" : "Since submit"}</MicroLabel>
          </div>
        </div>

        <div
          className="il-stage-board"
          style={{ display: "grid", placeItems: "center", padding: "26px 34px 8px", minHeight: "132px" }}
        >
          <SplitFlapBoard board={state.board} tone={state.boardTone} />
        </div>

        <div className="il-scroll il-stage-scroll" style={{ flex: 1, minHeight: 0, padding: "14px 34px 26px" }}>
          {body}
        </div>

        <div
          className="il-stage-footer"
          style={{
            height: "56px",
            flex: "none",
            display: "flex",
            alignItems: "center",
            gap: "18px",
            padding: "0 34px",
            borderTop: `1px solid ${color.lineSoft}`,
            background: "rgba(22,24,18,.6)",
          }}
        >
          <div
            aria-hidden="true"
            className="il-stage-progress"
            style={{ flex: 1, height: "3px", borderRadius: "2px", background: "rgba(230,225,215,.08)" }}
          >
            <span
              style={{
                display: "block",
                height: "100%",
                borderRadius: "2px",
                width: `${progress}%`,
                background: `linear-gradient(90deg, ${color.accent}, ${color.pass})`,
                transition: "width .3s linear",
              }}
            />
          </div>

          <span className="il-stage-status">
            <MicroLabel style={{ whiteSpace: "nowrap" }}>
              {stage.n} / 07 · {state.log.at(-1) ?? "awaiting first event"}
            </MicroLabel>
          </span>

          <span style={{ display: "flex", gap: "8px", flex: "none" }}>
            <FooterButton label="Back to chat" onClick={onBackToChat} />
            <FooterButton label="New chat session" onClick={onNewSession} emphasis />
          </span>
        </div>
      </div>
    </div>
  );
}

function FooterButton({
  label,
  onClick,
  emphasis,
}: {
  label: string;
  onClick: () => void;
  emphasis?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: "8px 14px",
        borderRadius: radius.button,
        border: `1px solid ${emphasis ? color.accent : color.line}`,
        background: emphasis ? "rgba(200,180,160,.12)" : "transparent",
        cursor: "pointer",
        color: emphasis ? color.accent : color.text,
        font: `500 9px ${font.mono}`,
        letterSpacing: ".14em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
  );
}
