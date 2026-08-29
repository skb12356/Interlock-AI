import type { HoldProjection } from "../domain/contracts";

interface ReviewsWorkspaceProps {
  holds: HoldProjection[];
  loading: boolean;
  error: string | null;
  resolvingHoldId: string | null;
  hasToken: (holdId: string) => boolean;
  onApprove: (holdId: string) => void;
  onReject: (holdId: string) => void;
  onRefresh: () => void;
}

function timestamp(value: number): string {
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value * 1000));
}

export function ReviewsWorkspace({
  holds,
  loading,
  error,
  resolvingHoldId,
  hasToken,
  onApprove,
  onReject,
  onRefresh,
}: ReviewsWorkspaceProps) {
  return (
    <section className="reviews-desk" aria-labelledby="review-queue-title">
      <div className="reviews-toolbar">
        <div>
          <p className="eyebrow">Durable state / audited resolution</p>
          <h2 id="review-queue-title">Review queue</h2>
        </div>
        <button type="button" className="quiet-action" onClick={onRefresh} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh queue"}
        </button>
      </div>

      {error && <p className="queue-error" role="alert">{error}</p>}
      {!loading && holds.length === 0 ? (
        <div className="empty-state review-empty">
          <strong>No pending holds</strong>
          <span>Response and tool-call holds will survive a gateway restart and appear here.</span>
        </div>
      ) : (
        <div className="review-list">
          {holds.map((hold) => {
            const tokenReady = hasToken(hold.hold_id);
            const resolving = resolvingHoldId === hold.hold_id;
            return (
              <article className="review-card" key={hold.hold_id}>
                <header>
                  <div>
                    <span className="hold-kind">{hold.kind.replace("_", " ")}</span>
                    <h3>{hold.tool ?? "Response review"}</h3>
                    <p>{hold.reason}</p>
                  </div>
                  <span className={hold.expired ? "sla-badge expired" : "sla-badge"}>
                    {hold.expired ? "Expired" : "Pending"}
                  </span>
                </header>

                <dl className="hold-facts">
                  <div><dt>Hold ID</dt><dd>{hold.hold_id}</dd></div>
                  <div><dt>Request</dt><dd>{hold.request_id}</dd></div>
                  <div><dt>Created</dt><dd>{timestamp(hold.created_ts)}</dd></div>
                  <div><dt>Flagged span</dt><dd>{hold.flagged_span ?? "Not reported"}</dd></div>
                </dl>

                <div className="review-detail-grid">
                  <section>
                    <h4>Evidence</h4>
                    {hold.evidence.length ? (
                      <ul>{hold.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
                    ) : <p className="muted">No evidence was persisted.</p>}
                  </section>
                  <section>
                    <h4>Arguments</h4>
                    <pre>{JSON.stringify(hold.payload, null, 2)}</pre>
                  </section>
                </div>

                <footer>
                  <div className={tokenReady ? "token-state ready" : "token-state"}>
                    <span aria-hidden="true" />
                    {tokenReady
                      ? "Approval secret captured"
                      : "Approval unavailable — open the initiating stream in this tab"}
                  </div>
                  {hold.expired && <p className="expired-note">SLA expired · refresh queue before acting</p>}
                  <div className="review-actions">
                    <button
                      type="button"
                      className="reject-action"
                      onClick={() => onReject(hold.hold_id)}
                      disabled={resolving}
                    >
                      Reject and stop
                    </button>
                    <button
                      type="button"
                      className="approve-action"
                      onClick={() => onApprove(hold.hold_id)}
                      disabled={!tokenReady || hold.expired || resolving}
                    >
                      Approve hold
                    </button>
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
