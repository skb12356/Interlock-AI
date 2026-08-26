import type { HoldProjection } from "../domain/contracts";

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
