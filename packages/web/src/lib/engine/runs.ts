/**
 * Run helpers shared by the dashboard, the runs table and the sidebar.
 */
import type { SessionStatus, SimulationStatus } from "./types";
import { TERMINAL_STATUSES } from "./types";

export const isActive = (run: SimulationStatus) => !TERMINAL_STATUSES.includes(run.status);
export const needsReview = (run: SimulationStatus) => run.status === "awaiting_approval";
export const isCompleted = (run: SimulationStatus) => run.status === "completed" && !!run.summary;

export type StatusTone = "pass" | "warn" | "fail" | "muted" | "info";

export const STATUS_META: Record<SessionStatus, { label: string; tone: StatusTone }> = {
  queued: { label: "Queued", tone: "muted" },
  running: { label: "Running", tone: "info" },
  awaiting_approval: { label: "Needs review", tone: "warn" },
  completed: { label: "Completed", tone: "pass" },
  failed: { label: "Failed", tone: "fail" },
  aborted: { label: "Stopped", tone: "muted" },
  cancelled: { label: "Cancelled", tone: "muted" },
};

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["week", 604800],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];

/** "3 hours ago", relative to `now` (passed in so renders are deterministic). */
export function relativeTime(iso: string, now: number): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return RELATIVE.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function runDuration(run: SimulationStatus): number | undefined {
  if (run.summary?.execution_time_seconds) return run.summary.execution_time_seconds;
  if (!TERMINAL_STATUSES.includes(run.status)) return undefined;
  return (new Date(run.updated_at).getTime() - new Date(run.created_at).getTime()) / 1000;
}

export function stuckShare(run: SimulationStatus): number | undefined {
  const s = run.summary;
  if (!s?.total_conversations || s.stuck_conversations == null) return undefined;
  return s.stuck_conversations / s.total_conversations;
}

/** Host of the bot endpoint, for compact display. */
export function botHost(endpoint: string): string {
  try {
    return new URL(endpoint).host;
  } catch {
    return endpoint;
  }
}

/** Which kind of test a run was, for lists ("" for a plain persona simulation). */
export function runKind(run: SimulationStatus): string {
  if (run.config.stress) return `Memory stress · ${run.config.stress.turns} messages`;
  const n = run.config.scenarios?.length ?? 0;
  if (n) return `${n} scenario${n === 1 ? "" : "s"}`;
  return "";
}
