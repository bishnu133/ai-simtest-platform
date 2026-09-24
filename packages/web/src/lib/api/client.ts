import type { ConversationListResponse } from "./types";

const isServer = typeof window === "undefined";

function target(): { base: string; headers: Record<string, string> } {
  if (isServer) {
    return {
      base: `${process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000"}/v1`,
      headers: {
        Authorization: `Bearer ${process.env.DEV_AUTH_BEARER ?? "dev"}`,
        "X-Dev-User-Id": process.env.DEV_AUTH_USER_ID ?? "dev_actor_1",
        Accept: "application/json",
      },
    };
  }
  // Browser: go through the Route Handler proxy, which injects auth server-side.
  return { base: "/api", headers: { Accept: "application/json" } };
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiGet<T>(path: string): Promise<T> {
  const { base, headers } = target();
  const res = await fetch(`${base}${path}`, { headers, cache: "no-store" });
  if (!res.ok) {
    let code = `http_${res.status}`;
    let message = res.statusText || "Request failed";
    try {
      const j = await res.json();
      if (j?.error) {
        code = j.error.code ?? code;
        message = j.error.message ?? message;
      }
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, code, message);
  }
  return (await res.json()) as T;
}

export function listConversations(params?: {
  run_id?: string;
  verdict?: string;
}): Promise<ConversationListResponse> {
  const q = new URLSearchParams();
  if (params?.run_id) q.set("run_id", params.run_id);
  if (params?.verdict) q.set("verdict", params.verdict);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return apiGet<ConversationListResponse>(`/conversations${qs}`);
}
