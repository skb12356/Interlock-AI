import { Hero } from "./Hero";
import { StageView } from "./StageView";
import type { EngineSettings, TraceEngine, TraceUiState } from "./traceEngine";

/** The Live workspace: hero until a prompt is sent, then the stage machine. */
export function LiveTheater({
  engine,
  state,
  settings,
  onSubmit,
}: {
  engine: TraceEngine;
  state: TraceUiState;
  settings: EngineSettings;
  onSubmit: () => void;
}) {
  const scene = engine.scene;

  if (state.phase === "hero") {
    return (
      <Hero
        prompt={state.prompt}
        scene={state.scene}
        onPrompt={(prompt) => engine.setPrompt(prompt)}
        onScene={(next) => engine.setScene(next)}
        onSubmit={onSubmit}
      />
    );
  }

  return <StageView engine={engine} state={state} scene={scene} settings={settings} onReplay={onSubmit} />;
}
