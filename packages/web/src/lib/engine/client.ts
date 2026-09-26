/**
 * Browser-side client for the engine wizard API. All calls go through the
 * Next.js proxy at /api/engine so the engine URL never reaches the client.
 */
import type {
  CreateSimulationRequest,
  EngineOptions,
  GateDecision,
  PendingGate,
  ReportResponse,
  ReviewResponse,
  ReviewSummary,
  SimulationStatus,
} from "./types";

const BASE = "/api/engine";

export class EngineError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
    this.name = "EngineError";
  }

  get isUnreachable() {
    return this.code === "engine_unreachable";
  }
}

function describeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{ loc, msg, ... }]
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((p: unknown) => p !== "body").join(".") : "";
        const msg = String(d?.msg ?? "Invalid value").replace(/^Value error, /, "");
        return loc ? `${loc}: ${msg}` : msg;
      })
      .join("; ");
  }
  return "Unexpected error from the engine";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: init?.body ? { "content-type": "application/json" } : undefined,
      cache: "no-store",
    });
  } catch {
    throw new EngineError(0, "Network error — is the dashboard server running?");
  }

  if (!res.ok) {
    let body: { detail?: unknown; code?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new EngineError(res.status, describeDetail(body.detail) || res.statusText, body.code);
  }
  return res.json() as Promise<T>;
}

export const engine = {
  createSimulation: (body: CreateSimulationRequest) =>
    request<SimulationStatus>("/simulations", { method: "POST", body: JSON.stringify(body) }),

  getOptions: () => request<EngineOptions>("/options"),

  listSimulations: () => request<{ simulations: SimulationStatus[] }>("/simulations"),

  getSimulation: (id: string) => request<SimulationStatus>(`/simulations/${encodeURIComponent(id)}`),

  getGate: (id: string) => request<PendingGate>(`/simulations/${encodeURIComponent(id)}/gate`),

  decide: (id: string, gateKey: string, decision: GateDecision, modifiedData?: unknown) =>
    request<SimulationStatus>(
      `/simulations/${encodeURIComponent(id)}/gate/${encodeURIComponent(gateKey)}/decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision, modified_data: modifiedData ?? null }),
      },
    ),

  cancel: (id: string) =>
    request<SimulationStatus>(`/simulations/${encodeURIComponent(id)}/cancel`, { method: "POST" }),

  getReport: (id: string) => request<ReportResponse>(`/simulations/${encodeURIComponent(id)}/report`),

  getReview: (id: string, judge: string, size = 20) =>
    request<ReviewResponse>(
      `/simulations/${encodeURIComponent(id)}/review?judge=${encodeURIComponent(judge)}&size=${size}`,
    ),

  postReview: (id: string, body: { judge: string; key: string; human_pass: boolean | null; note?: string }) =>
    request<ReviewSummary>(`/simulations/${encodeURIComponent(id)}/review`, {
      method: "POST",
      body: JSON.stringify({ note: "", ...body }),
    }),

  exportUrl: (id: string, format: string) =>
    `${BASE}/simulations/${encodeURIComponent(id)}/exports/${encodeURIComponent(format)}`,
};
