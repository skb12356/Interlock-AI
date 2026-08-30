import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ConsoleApiError, getEvidenceBundle, getHolds, getStatus, resolveHold } from "./api/consoleClient";
import { ChatPage, type StreamingTurn } from "./chat/ChatPage";
import { ChatSidebar } from "./chat/ChatSidebar";
import {
  appendTurn,
  createSession,
  createTurn,
  loadSessions,
  patchTurn,
  persistableOverlay,
  saveSessions,
  sortSessions,
  upsertSession,
} from "./chat/sessionStore";
import type { ChatSession, ChatTurn } from "./chat/types";
import type { HoldProjection } from "./domain/contracts";
import type { ConsoleStatus, EvidenceBundle } from "./domain/evidence";
import { ResumeTokenVault } from "./security/resumeTokens";
import { useProjectionHistory } from "./state/useProjectionHistory";
import { EvidencePanel } from "./theater/EvidencePanel";
import { Header, type GatewayHealth, type View } from "./theater/Header";
import { runLiveTrace } from "./theater/liveRun";
import { ReviewsPanel, toHoldCard } from "./theater/ReviewsPanel";
import { ShellDecoration } from "./theater/ShellDecoration";
import { StageView } from "./theater/StageView";
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

const VIEWS: View[] = ["chat", "live", "reviews", "evidence"];

/** The view lives in the hash so "see it live" is a real, shareable location. */
function readView(): View {
  if (typeof window === "undefined") return "chat";
  const hash = window.location.hash.replace(/^#\/?/, "");
  return (VIEWS as string[]).includes(hash) ? (hash as View) : "chat";
}

export function App() {
  const { engine, state } = useTraceEngine();
  const projection = useProjectionHistory();
  const [view, setViewState] = useState<View>(readView);

  const [sessions, setSessions] = useState<ChatSession[]>(() => loadSessions());
  const [activeSessionId, setActiveSessionId] = useState<string | null>(() => loadSessions()[0]?.id ?? null);
  const [streaming, setStreaming] = useState<StreamingTurn | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);

  const [consoleStatus, setConsoleStatus] = useState<ConsoleStatus | null>(null);
  const [holds, setHolds] = useState<HoldProjection[]>([]);
  const [reviewsLoading, setReviewsLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [resolvingHoldId, setResolvingHoldId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceBundle>(emptyEvidence);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const vault = useRef(new ResumeTokenVault());
  const liveRun = useRef<AbortController | null>(null);

  const setView = useCallback((next: View) => {
    setViewState(next);
    if (typeof window !== "undefined") window.location.hash = `#/${next}`;
  }, []);

  useEffect(() => {
    const onHash = () => setViewState(readView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    void getStatus().then(setConsoleStatus).catch(() => undefined);
  }, []);

  useEffect(() => {
    const currentVault = vault.current;
    return () => {
      currentVault.clear();
      liveRun.current?.abort();
    };
  }, []);

  useEffect(() => {
    saveSessions(sessions);
  }, [sessions]);

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  );

  const updateSession = useCallback((sessionId: string, mutate: (session: ChatSession) => ChatSession) => {
    setSessions((current) => {
      const session = current.find((item) => item.id === sessionId);
      if (!session) return current;
      return sortSessions(upsertSession(current, mutate(session)));
    });
  }, []);

  const startSession = useCallback(() => {
    const session = createSession();
    setSessions((current) => sortSessions(upsertSession(current, session)));
    setActiveSessionId(session.id);
    setChatError(null);
    setView("chat");
    return session;
  }, [setView]);

  /**
   * One prompt, one real gateway request. The engine narrates the stages while
   * the stream runs; when it ends the turn keeps the overlay so the trace can be
   * reopened later without asking the backend to run it again.
   */
  const sendPrompt = useCallback(
    (prompt: string) => {
      let sessionId = activeSessionId;
      if (!sessionId || !sessions.some((session) => session.id === sessionId)) {
        sessionId = startSession().id;
      }
      const turn = createTurn(prompt);
      updateSession(sessionId, (session) => appendTurn(session, turn));
      setStreaming({ turnId: turn.id, stage: 0, answer: "" });
      setChatError(null);

      liveRun.current?.abort();
      const controller = new AbortController();
      liveRun.current = controller;
      vault.current.clear();

      const settle = (patch: Partial<ChatTurn>) => {
        const current = engine.getState();
        updateSession(sessionId, (session) =>
          patchTurn(session, turn.id, {
            stage: current.stage,
            answer: current.live?.assistantText ?? "",
            durationMs: current.durationMs,
            overlay: current.live ? persistableOverlay(current.live) : null,
            ...patch,
          }),
        );
        setStreaming(null);
      };

      void runLiveTrace(engine, {
        prompt,
        replay: consoleStatus?.source !== "live",
        signal: controller.signal,
        vault: vault.current,
      })
        .then(() => settle({ status: "complete" }))
        .catch((error: unknown) => {
          const message = error instanceof Error ? error.message : "The stream ended unexpectedly";
          setChatError(message);
          settle({ status: "failed", error: message });
        });
    },
    [activeSessionId, consoleStatus?.source, engine, sessions, startSession, updateSession],
  );

  // Mirror the running trace into the transcript without persisting every token.
  useEffect(() => {
    if (!streaming) return;
    setStreaming((current) =>
      current === null || (current.stage === state.stage && current.answer === (state.live?.assistantText ?? ""))
        ? current
        : { ...current, stage: state.stage, answer: state.live?.assistantText ?? "" },
    );
  }, [state.stage, state.live?.assistantText, streaming]);

  const openTrace = useCallback(
    (turn: ChatTurn) => {
      if (streaming?.turnId !== turn.id && turn.overlay) engine.loadTrace(turn.overlay, turn.durationMs);
      setView("live");
    },
    [engine, setView, streaming?.turnId],
  );

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

  const health: GatewayHealth = consoleStatus
    ? consoleStatus.source === "live"
      ? "connected"
      : "replay"
    : "connecting";
  const healthDetail = consoleStatus
    ? `gateway ${consoleStatus.source} · ${projection.status} · ${Object.keys(projection.state.requests).length} traces`
    : "gateway connecting";

  const traceAvailable = state.phase === "run";

  return (
    <div
      style={{
        position: "relative",
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: color.bgShell,
        color: color.text,
        overflow: "hidden",
      }}
    >
      <ShellDecoration />
      <Header
        view={view}
        onView={setView}
        breadcrumb={view === "chat" || view === "live" ? (activeSession?.title ?? "New session") : null}
        health={health}
        healthDetail={healthDetail}
        traceAvailable={traceAvailable}
      />

      <main style={{ position: "relative", zIndex: 2, flex: 1, display: "flex", minHeight: 0 }}>
        {view === "chat" ? (
          <>
            <ChatSidebar
              sessions={sessions}
              activeId={activeSessionId}
              onNew={() => startSession()}
              onOpen={(id) => {
                setActiveSessionId(id);
                setChatError(null);
              }}
            />
            <ChatPage
              session={activeSession}
              streaming={streaming}
              busy={streaming !== null}
              error={chatError}
              onSubmit={sendPrompt}
              onSeeItLive={openTrace}
            />
          </>
        ) : view === "live" ? (
          <StageView
            engine={engine}
            state={state}
            scene={engine.scene}
            settings={engine.getSettings()}
            onReplay={() => sendPrompt(state.prompt)}
            onBackToChat={() => setView("chat")}
            onNewSession={() => startSession()}
          />
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
