/**
 * Browser-side client for the engine wizard API. All calls go through the
 * Next.js proxy at /api/engine so the engine URL never reaches the client.
 */
import type {
  AIModelCheck,
  AISettingsInput,
  AISettingsView,
  BotCheckRequest,
  BotCheckResult,
  PersonaInput,
  BotHistoryRun,
  LibraryCheck,
  LibraryItem,
  LibraryKind,
  SavedBot,
  SavedBotInput,
  SavedPersona,
  TemplateSchedule,
  TestTemplate,
  SeenPersona,
  CalibrationCheck,
  CalibrationOverview,
  CIInfo,
  NotificationChannelInput,
  NotificationSettings,
  NotificationTestResult,
  JudgedConversation,
  RunDiff,
  SuiteDetail,
  SuiteSummary,
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
  // DELETE answers 204 with no body
  if (res.status === 204) return undefined as T;
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

  /** One conversation from any bot in a comparison (side-by-side reading). */
  getComparedConversation: (id: string, conversationId: string) =>
    request<{ bot: string; judged_conversation: JudgedConversation }>(
      `/simulations/${encodeURIComponent(id)}/compare/conversations/${encodeURIComponent(conversationId)}`,
    ),

  getReview: (id: string, judge: string, size = 20) =>
    request<ReviewResponse>(
      `/simulations/${encodeURIComponent(id)}/review?judge=${encodeURIComponent(judge)}&size=${size}`,
    ),

  postReview: (id: string, body: { judge: string; key: string; human_pass: boolean | null; note?: string }) =>
    request<ReviewSummary>(`/simulations/${encodeURIComponent(id)}/review`, {
      method: "POST",
      body: JSON.stringify({ note: "", ...body }),
    }),

  saveSuite: (id: string, body: { name?: string; include_warnings?: boolean; include_passing?: number }) =>
    request<SuiteSummary>(`/simulations/${encodeURIComponent(id)}/suite`, { method: "POST", body: JSON.stringify(body) }),

  listSuites: () => request<{ suites: SuiteSummary[] }>("/suites"),

  getSuite: (id: string) => request<SuiteDetail>(`/suites/${encodeURIComponent(id)}`),

  deleteSuite: (id: string) => request<void>(`/suites/${encodeURIComponent(id)}`, { method: "DELETE" }),

  compareRuns: (before: string, after: string) =>
    request<RunDiff>(`/compare-runs?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`),

  getCalibration: () => request<CalibrationOverview>("/calibration"),

  getGoldenSet: () => request<{ examples: Record<string, unknown>[] }>("/calibration/golden-set"),

  startCalibrationCheck: (judges: string[] = []) =>
    request<Pick<CalibrationCheck, "id" | "status" | "created_at" | "judges" | "progress">>("/calibration/checks", {
      method: "POST",
      body: JSON.stringify({ judges }),
    }),

  getCalibrationCheck: (id: string) => request<CalibrationCheck>(`/calibration/checks/${encodeURIComponent(id)}`),

  getCI: () => request<CIInfo>("/ci"),

  /** A run's setup for `simtest ci --request`: no API keys, never replay logs. */
  getRunSetup: (id: string) => request<Record<string, unknown>>(`/simulations/${encodeURIComponent(id)}/setup`),

  getNotifications: () => request<NotificationSettings>("/notifications"),

  saveNotifications: (body: {
    enabled: boolean;
    dashboard_url: string;
    environment: string;
    channels: NotificationChannelInput[];
  }) => request<NotificationSettings>("/notifications", { method: "PUT", body: JSON.stringify(body) }),

  testNotification: (channelId: string) =>
    request<NotificationTestResult>(`/notifications/test/${encodeURIComponent(channelId)}`, { method: "POST" }),

  getAISettings: () => request<AISettingsView>("/ai-settings"),

  saveAISettings: (body: AISettingsInput) =>
    request<AISettingsView>("/ai-settings", { method: "PUT", body: JSON.stringify(body) }),

  checkAIModels: () => request<{ results: AIModelCheck[] }>("/ai-settings/check", { method: "POST" }),

  /** One message to the bot, sent as a run would send it. */
  checkBot: (body: BotCheckRequest) =>
    request<BotCheckResult>("/bot-check", { method: "POST", body: JSON.stringify(body) }),

  listBots: () => request<{ bots: SavedBot[] }>("/bots"),

  createBot: (body: SavedBotInput) => request<SavedBot>("/bots", { method: "POST", body: JSON.stringify(body) }),

  updateBot: (id: string, body: SavedBotInput) =>
    request<SavedBot>(`/bots/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),

  botHistory: (id: string) =>
    request<{ bot: SavedBot; runs: BotHistoryRun[] }>(`/bots/${encodeURIComponent(id)}/history`),

  deleteBot: (id: string) => request<{ deleted: string }>(`/bots/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listPersonas: () => request<{ library: SavedPersona[]; seen: SeenPersona[] }>("/personas"),

  savePersona: (body: PersonaInput & { from_run?: string | null }) =>
    request<SavedPersona>("/personas", { method: "POST", body: JSON.stringify(body) }),

  deletePersona: (id: string) =>
    request<{ deleted: string }>(`/personas/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /** The same test again: same bot, documents, personas and settings. Keys are never kept. */
  rerun: (id: string, botApiKey?: string) =>
    request<SimulationStatus>(`/simulations/${encodeURIComponent(id)}/rerun`, {
      method: "POST",
      body: JSON.stringify(botApiKey ? { bot_api_key: botApiKey } : {}),
    }),

  listTemplates: () => request<{ templates: TestTemplate[] }>("/templates"),

  createTemplate: (name: string, fromRun: string) =>
    request<TestTemplate>("/templates", { method: "POST", body: JSON.stringify({ name, from_run: fromRun }) }),

  updateTemplate: (id: string, body: { name?: string; schedule?: TemplateSchedule }) =>
    request<TestTemplate>(`/templates/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),

  deleteTemplate: (id: string) =>
    request<{ deleted: string }>(`/templates/${encodeURIComponent(id)}`, { method: "DELETE" }),

  runTemplate: (id: string, botApiKey?: string) =>
    request<SimulationStatus>(`/templates/${encodeURIComponent(id)}/run`, {
      method: "POST",
      body: JSON.stringify(botApiKey ? { bot_api_key: botApiKey } : {}),
    }),

  listLibrary: (kind: LibraryKind) => request<{ items: LibraryItem[]; starter: string }>(`/library/${kind}`),

  checkLibraryItem: (kind: LibraryKind, yaml: string) =>
    request<LibraryCheck>(`/library/${kind}/check`, { method: "POST", body: JSON.stringify({ yaml }) }),

  saveLibraryItem: (kind: LibraryKind, yaml: string, replaceId?: string) =>
    request<LibraryItem & { warnings: string[] }>(
      replaceId ? `/library/${kind}/${encodeURIComponent(replaceId)}` : `/library/${kind}`,
      { method: replaceId ? "PUT" : "POST", body: JSON.stringify({ yaml }) },
    ),

  deleteLibraryItem: (kind: LibraryKind, id: string) =>
    request<{ deleted: string }>(`/library/${kind}/${encodeURIComponent(id)}`, { method: "DELETE" }),

  exportUrl: (id: string, format: string) =>
    `${BASE}/simulations/${encodeURIComponent(id)}/exports/${encodeURIComponent(format)}`,
};
