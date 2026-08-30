import { useCallback, useEffect, useRef, useState } from "react";

import { ConsoleApiError, getEvidenceBundle, getHolds, getStatus, resolveHold } from "./api/consoleClient";
import type { HoldProjection } from "./domain/contracts";
import type { ConsoleStatus, EvidenceBundle } from "./domain/evidence";
import { ResumeTokenVault } from "./security/resumeTokens";
import { useProjectionHistory } from "./state/useProjectionHistory";
import { EvidencePanel } from "./theater/EvidencePanel";
import { runLiveTrace } from "./theater/liveRun";
import { Header, type View } from "./theater/Header";
import { LiveTheater } from "./theater/LiveTheater";
import { ReviewsPanel, toHoldCard } from "./theater/ReviewsPanel";
import { ShellDecoration } from "./theater/ShellDecoration";
import { color } from "./theater/tokens";
import { useTraceEngine } from "./theater/useTraceEngine";

import "./theater/theater.css";

const emptyEvidence: EvidenceBundle = {
  calibration: null,
  conformal: null,
  evaluation: null,
  latency: null,
  laneC: null,
  ledger: null,
};

/** Playback settings are read once from the URL so a demo can be re-paced without a rebuild. */
function readSettings(): { pace: number; autoplay: boolean; currency: "rupee" | "dollar" } {
  if (typeof window === "undefined") return { pace: 1, autoplay: true, currency: "rupee" };
  const params = new URLSearchParams(window.location.search);
  const pace = Number(params.get("pace"));
  return {
    pace: Number.isFinite(pace) && pace >= 0.5 && pace <= 2 ? pace : 1,
    autoplay: params.get("autoplay") !== "0",
    currency: params.get("currency") === "dollar" ? "dollar" : "rupee",
  };
}

export function App() {
  const [settings] = useState(readSettings);
  const { engine, state } = useTraceEngine(settings);
  const projection = useProjectionHistory();
  const [view, setView] = useState<View>("live");

  const [consoleStatus, setConsoleStatus] = useState<ConsoleStatus | null>(null);
  const [holds, setHolds] = useState<HoldProjection[]>([]);
  const [reviewsLoading, setReviewsLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [resolvingHoldId, setResolvingHoldId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceBundle>(emptyEvidence);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const vault = useRef(new ResumeTokenVault());
  const liveRun = useRef<AbortController | null>(null);

  useEffect(() => {
    void getStatus()
      .then(setConsoleStatus)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const currentVault = vault.current;
    return () => {
      currentVault.clear();
      liveRun.current?.abort();
    };
  }, []);

  /**
   * Demo mode replays the seeded fixture; live mode streams a real request
   * through the gateway and lets the frames drive the same stage machine.
   */
  const submitTrace = useCallback(() => {
    if (state.mode === "demo") {
      setLiveError(null);
      engine.submit();
      return;
    }
    liveRun.current?.abort();
    const controller = new AbortController();
    liveRun.current = controller;
    setLiveError(null);
    vault.current.clear();
    void runLiveTrace(engine, {
      prompt: state.prompt,
      scenario: state.scene,
      replay: consoleStatus?.source !== "live",
      signal: controller.signal,
      vault: vault.current,
    }).catch((error: unknown) => {
      setLiveError(error instanceof Error ? error.message : "The stream ended unexpectedly");
    });
  }, [consoleStatus?.source, engine, state.mode, state.prompt, state.scene]);

  const loadReviews = useCallback(async () => {
    setReviewsLoading(true);
    setReviewError(null);
    try {
      setHolds(await getHolds());
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "The review queue could not be loaded");
    } finally {
      setReviewsLoading(false);
    }
  }, []);

  const loadEvidence = useCallback(async () => {
    setEvidenceLoading(true);
    setEvidenceError(null);
    try {
      setEvidence(await getEvidenceBundle());
    } catch (error) {
      setEvidenceError(error instanceof Error ? error.message : "Evidence projections could not be loaded");
    } finally {
      setEvidenceLoading(false);
    }
  }, []);

  useEffect(() => {
    if (view === "reviews") void loadReviews();
    if (view === "evidence") void loadEvidence();
  }, [loadEvidence, loadReviews, view]);

  const handleHold = async (holdId: string, resolution: "approved" | "rejected") => {
    setResolvingHoldId(holdId);
    setReviewError(null);
    try {
      await resolveHold(holdId, resolution, vault.current.get(holdId));
      vault.current.delete(holdId);
      await loadReviews();
    } catch (error) {
      if (error instanceof ConsoleApiError && (error.status === 404 || error.status === 409)) {
        vault.current.delete(holdId);
        await loadReviews();
        setReviewError("This hold changed before the action completed. The queue has been refreshed.");
      } else {
        setReviewError(error instanceof Error ? error.message : "The hold could not be resolved");
      }
    } finally {
      setResolvingHoldId(null);
    }
  };

  const connectionLabel =
    state.mode === "demo"
      ? "cached · :8080 idle"
      : consoleStatus
        ? `gateway :8080 ${consoleStatus.source === "live" ? "connected" : "replay"} · history ${projection.status} · ${Object.keys(projection.state.requests).length} traces`
        : "gateway :8080 connecting";

  return (
    <div
      style={{
        position: "relative",
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        background: color.bgShell,
        color: color.text,
      }}
    >
      <ShellDecoration />
      <Header
        view={view}
        onView={setView}
        mode={state.mode}
        onToggleMode={() => engine.toggleMode()}
        connectionLabel={connectionLabel}
      />

      {liveError ? (
        <div
          role="alert"
          style={{
            position: "relative",
            zIndex: 2,
            margin: "12px 24px 0",
            padding: "10px 14px",
            borderRadius: "8px",
            border: "1px solid rgba(217,112,95,.4)",
            background: "rgba(217,112,95,.07)",
            color: color.text,
            font: "400 12px 'JetBrains Mono', monospace",
          }}
        >
          live stream · {liveError}
        </div>
      ) : null}

      <main style={{ position: "relative", zIndex: 2, flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {view === "live" ? (
          <LiveTheater engine={engine} state={state} settings={engine.getSettings()} onSubmit={submitTrace} />
        ) : view === "reviews" ? (
          <ReviewsPanel
            holds={holds.map((hold) => toHoldCard(hold, vault.current.get(hold.hold_id) !== undefined))}
            loading={reviewsLoading}
            error={reviewError}
            resolvingHoldId={resolvingHoldId}
            onApprove={(holdId) => void handleHold(holdId, "approved")}
            onReject={(holdId) => void handleHold(holdId, "rejected")}
            onRefresh={() => void loadReviews()}
          />
        ) : (
          <EvidencePanel bundle={evidence} loading={evidenceLoading} error={evidenceError} />
        )}
      </main>
    </div>
  );
}
