import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";

import { TraceEngine, type EngineSettings, type TraceUiState } from "./traceEngine";

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Owns one TraceEngine for the lifetime of the component and re-renders on
 * every state change. Settings are pushed into the engine rather than
 * recreating it, so changing pace mid-run does not restart the trace.
 */
export function useTraceEngine(settings: Partial<EngineSettings> = {}): {
  engine: TraceEngine;
  state: TraceUiState;
} {
  // Created once for the component's lifetime; later settings are pushed in below
  // rather than rebuilding the engine, which would restart a running trace.
  const engine = useMemo(() => new TraceEngine({ reducedMotion: prefersReducedMotion(), ...settings }), []);

  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  useEffect(() => {
    engine.updateSettings(settingsRef.current);
  }, [engine, settings.pace, settings.autoplay, settings.currency, settings.reducedMotion]);

  useEffect(() => () => engine.destroy(), [engine]);

  const state = useSyncExternalStore(engine.subscribe, engine.getState, engine.getState);
  return { engine, state };
}
