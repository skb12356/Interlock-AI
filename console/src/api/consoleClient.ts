import type { HoldProjection } from "../domain/contracts";
import type {
  ActionLatency,
  CalibrationReport,
  ConformalReport,
  ConsoleStatus,
  EvaluationReport,
  EvidenceBundle,
  LedgerSummary,
} from "../domain/evidence";

export class ConsoleApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ConsoleApiError";
    this.status = status;
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: string;
      error?: { message?: string };
    };
    return payload.detail ?? payload.error?.message ?? `Request failed with ${response.status}`;
  } catch {
    return `Request failed with ${response.status}`;
  }
}

export async function getHolds(fetcher: typeof fetch = fetch): Promise<HoldProjection[]> {
  const response = await fetcher("/console/holds");
  if (!response.ok) throw new ConsoleApiError(await errorMessage(response), response.status);
  const payload = (await response.json()) as { holds: HoldProjection[] };
  return payload.holds;
}

async function getProjection<T>(path: string, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(path);
  if (!response.ok) throw new ConsoleApiError(await errorMessage(response), response.status);
  return (await response.json()) as T;
}

export function getStatus(fetcher: typeof fetch = fetch): Promise<ConsoleStatus> {
  return getProjection<ConsoleStatus>("/console/status", fetcher);
}

export function getLedgerSummary(fetcher: typeof fetch = fetch): Promise<LedgerSummary> {
  return getProjection<LedgerSummary>("/console/ledger/summary", fetcher);
}

async function getArtifact<T>(name: string, fetcher: typeof fetch): Promise<T | null> {
  const response = await fetcher(`/console/artifacts/${encodeURIComponent(name)}`);
  if (!response.ok) return null;
  return (await response.json()) as T;
}

export async function getEvidenceBundle(fetcher: typeof fetch = fetch): Promise<EvidenceBundle> {
  const [calibration, conformal, evaluationArtifact, latency] = await Promise.all([
    getArtifact<CalibrationReport>("calibration/report.json", fetcher),
    getArtifact<ConformalReport>("calibration/lambda.json", fetcher),
    getArtifact<EvaluationReport | { metrics: EvaluationReport }>("eval/report-guaranteed.json", fetcher),
    getArtifact<ActionLatency[]>("action_latency.json", fetcher),
  ]);
  const evaluation = evaluationArtifact && !("notes" in evaluationArtifact)
    ? evaluationArtifact.metrics
    : evaluationArtifact;
  return { calibration, conformal, evaluation, latency };
}

export async function resolveHold(
  holdId: string,
  state: "approved" | "rejected",
  resumeToken: string | undefined,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const action = state === "approved" ? "approve" : "reject";
  const init: RequestInit = { method: "POST" };
  if (state === "approved") {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify({ resume_token: resumeToken });
  }
  const response = await fetcher(
    `/gateway/v1/holds/${encodeURIComponent(holdId)}/${action}`,
    init,
  );
  if (!response.ok) throw new ConsoleApiError(await errorMessage(response), response.status);
}
