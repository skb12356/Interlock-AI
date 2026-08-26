import { useState } from "react";

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
        <p className="eyebrow">Operator workspace / {workspace}</p>
        <h1>{headings[workspace]}</h1>
        <p className="workspace-intro">
          {workspace === "live" && "Follow the evidence behind each intervention while the answer streams."}
          {workspace === "reviews" && "Resolve durable holds without exposing approval secrets."}
          {workspace === "evidence" && "Read measured performance with its limits kept attached."}
        </p>
      </main>
    </div>
  );
}
