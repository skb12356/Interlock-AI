import { useEffect, useReducer, useRef, useState } from "react";

import { streamChat } from "./api/chatClient";
import { ResumeTokenVault } from "./security/resumeTokens";
import { consoleReducer, initialConsoleState } from "./state/consoleStore";
import { LiveWorkspace } from "./workspaces/LiveWorkspace";

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

export function App() {
  const [workspace, setWorkspace] = useState<Workspace>("live");
  const [state, dispatch] = useReducer(consoleReducer, initialConsoleState);
  const [scenario, setScenario] = useState<"clean" | "scene1" | "held" | "blocked">("scene1");
  const [prompt, setPrompt] = useState("What are the prepayment charges on my floating-rate home loan?");
  const [busy, setBusy] = useState(false);
  const vault = useRef(new ResumeTokenVault());
  const activeController = useRef<AbortController | null>(null);

  useEffect(() => () => {
    activeController.current?.abort();
    vault.current.clear();
  }, []);

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
        ) : (
          <p className="workspace-intro">
            {workspace === "reviews" && "Resolve durable holds without exposing approval secrets."}
            {workspace === "evidence" && "Read measured performance with its limits kept attached."}
          </p>
        )}
      </main>
    </div>
  );
}
