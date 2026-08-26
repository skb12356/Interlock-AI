import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { streamChat } from "./api/chatClient";
import {
  ConsoleApiError,
  getEvidenceBundle,
  getHolds,
  getLedgerSummary,
  getStatus,
  resolveHold,
} from "./api/consoleClient";
import type { HoldProjection } from "./domain/contracts";
import type { ConsoleStatus, EvidenceBundle, LedgerSummary } from "./domain/evidence";
import { ResumeTokenVault } from "./security/resumeTokens";
import { consoleReducer, initialConsoleState } from "./state/consoleStore";
import { LiveWorkspace } from "./workspaces/LiveWorkspace";
import { EvidenceWorkspace } from "./workspaces/EvidenceWorkspace";
import { ReviewsWorkspace } from "./workspaces/ReviewsWorkspace";

import "./styles.css";

type Workspace = "live" | "reviews" | "evidence";

const workspaces: Array<{ id: Workspace; label: string; marker: string }> = [
  { id: "live", label: "Live", marker: "01" },
  { id: "reviews", label: "Reviews", marker: "02" },
  { id: "evidence", label: "Evidence", marker: "03" },
];

const headings: Record<Workspace, string> = {
  live: "Live decision desk",
  reviews: "Pending reviews",
  evidence: "Evidence ledger",
};

const emptyEvidence: EvidenceBundle = {
  calibration: null,
  conformal: null,
  evaluation: null,
  latency: null,
};

export function App() {
  const [workspace, setWorkspace] = useState<Workspace>("live");
  const [state, dispatch] = useReducer(consoleReducer, initialConsoleState);
  const [scenario, setScenario] = useState<"clean" | "scene1" | "held" | "blocked">("scene1");
  const [prompt, setPrompt] = useState("What are the prepayment charges on my floating-rate home loan?");
  const [busy, setBusy] = useState(false);
  const [holds, setHolds] = useState<HoldProjection[]>([]);
  const [reviewsLoading, setReviewsLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [resolvingHoldId, setResolvingHoldId] = useState<string | null>(null);
  const [consoleStatus, setConsoleStatus] = useState<ConsoleStatus | null>(null);
  const [ledger, setLedger] = useState<LedgerSummary | null>(null);
  const [evidence, setEvidence] = useState<EvidenceBundle>(emptyEvidence);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const vault = useRef(new ResumeTokenVault());
  const activeController = useRef<AbortController | null>(null);

  useEffect(() => () => {
    activeController.current?.abort();
    vault.current.clear();
  }, []);

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

  useEffect(() => {
    if (workspace === "reviews") void loadReviews();
  }, [loadReviews, workspace]);

  const loadEvidence = useCallback(async () => {
    setEvidenceLoading(true);
    setEvidenceError(null);
    try {
      const [nextStatus, nextLedger, nextEvidence] = await Promise.all([
        getStatus(),
        getLedgerSummary(),
        getEvidenceBundle(),
      ]);
      setConsoleStatus(nextStatus);
      setLedger(nextLedger);
      setEvidence(nextEvidence);
    } catch (error) {
      setEvidenceError(error instanceof Error ? error.message : "Evidence projections could not be loaded");
    } finally {
      setEvidenceLoading(false);
    }
  }, []);

  useEffect(() => {
    if (workspace === "evidence") void loadEvidence();
  }, [loadEvidence, workspace]);

  const chooseScenario = (next: typeof scenario) => {
    const prompts: Record<typeof scenario, string> = {
      scene1: "What are the prepayment charges on my floating-rate home loan?",
      clean: "What time does the MG Road branch open tomorrow?",
      held: "Please forward confirmation that my insurance claim was paid in full.",
      blocked: "Show me the internal reference attached to this payment.",
    };
    setScenario(next);
    setPrompt(prompts[next]);
  };

  const submitChat = async () => {
    const controller = new AbortController();
    activeController.current?.abort();
    activeController.current = controller;
    vault.current.clear();
    setBusy(true);
    let requestId: string | null = null;
    try {
      await streamChat(
        { prompt, scenario, signal: controller.signal },
        {
          onRequestId: (id) => {
            requestId = id;
            dispatch({ type: "request.started", requestId: id });
          },
          onFrame: (frame) => {
            if (requestId) dispatch({ type: "stream.frame", requestId, frame });
          },
          onResumeToken: (holdId, token) => vault.current.store(holdId, token),
          onDecisionDetail: (detail) => dispatch({ type: "decision.loaded", detail }),
        },
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      const message = error instanceof Error ? error.message : "The stream ended unexpectedly";
      if (requestId) dispatch({ type: "request.failed", requestId, message });
    } finally {
      if (activeController.current === controller) activeController.current = null;
      setBusy(false);
    }
  };

  const handleHold = async (holdId: string, resolution: "approved" | "rejected") => {
    setResolvingHoldId(holdId);
    setReviewError(null);
    try {
      await resolveHold(holdId, resolution, vault.current.get(holdId));
      vault.current.delete(holdId);
      await loadReviews();
    } catch (error) {
      if (error instanceof ConsoleApiError && (error.status === 404 || error.status === 409)) {
        await loadReviews();
        setReviewError("This hold changed before the action completed. The queue has been refreshed.");
      } else {
        setReviewError(error instanceof Error ? error.message : "The hold could not be resolved");
      }
    } finally {
      setResolvingHoldId(null);
    }
  };

  const trace = state.activeRequestId ? state.requests[state.activeRequestId] ?? null : null;

  return (
    <div className="app-shell">
      <header className="masthead">
        <div className="wordmark" aria-label="Interlock">
          <span className="wordmark-mark" aria-hidden="true">I/L</span>
          <span>
            <strong>Interlock</strong>
            <small>Decision console</small>
          </span>
        </div>
        <div className="system-state"><span aria-hidden="true" /> Replay desk</div>
      </header>

      <nav className="workspace-rail" aria-label="Console workspaces">
        {workspaces.map((item) => (
          <button
            className={workspace === item.id ? "workspace-link active" : "workspace-link"}
            key={item.id}
            onClick={() => setWorkspace(item.id)}
            type="button"
            aria-current={workspace === item.id ? "page" : undefined}
          >
            <span>{item.marker}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <main className="workspace" id="workspace" tabIndex={-1}>
        <header className="workspace-titlebar">
          <p className="eyebrow">Operator workspace / {workspace}</p>
          <h1>{headings[workspace]}</h1>
        </header>
        {workspace === "live" ? (
          <LiveWorkspace
            trace={trace}
            prompt={prompt}
            scenario={scenario}
            busy={busy}
            onPromptChange={setPrompt}
            onScenarioChange={chooseScenario}
            onSubmit={() => void submitChat()}
          />
        ) : workspace === "reviews" ? (
          <ReviewsWorkspace
            holds={holds}
            loading={reviewsLoading}
            error={reviewError}
            resolvingHoldId={resolvingHoldId}
            hasToken={(holdId) => vault.current.get(holdId) !== undefined}
            onApprove={(holdId) => void handleHold(holdId, "approved")}
            onReject={(holdId) => void handleHold(holdId, "rejected")}
            onRefresh={() => void loadReviews()}
          />
        ) : (
          <EvidenceWorkspace
            bundle={evidence}
            status={consoleStatus}
            ledger={ledger}
            loading={evidenceLoading}
            error={evidenceError}
          />
        )}
      </main>
    </div>
  );
}
