import { Ban, CheckCircle2, Clock, Hand, Loader2, XCircle, type LucideIcon } from "lucide-react";
import { STATUS_META, type StatusTone } from "@/lib/engine/runs";
import type { SessionStatus } from "@/lib/engine/types";

const TONE: Record<StatusTone, string> = {
  pass: "bg-pass/10 text-pass",
  warn: "bg-warn/10 text-warn",
  fail: "bg-fail/10 text-fail",
  info: "bg-primary/10 text-primary",
  muted: "bg-muted text-muted-foreground",
};

const ICON: Record<SessionStatus, LucideIcon> = {
  queued: Clock,
  running: Loader2,
  awaiting_approval: Hand,
  completed: CheckCircle2,
  failed: XCircle,
  aborted: Ban,
  cancelled: Ban,
};

/** Status as icon + label, never colour alone. */
export function StatusBadge({ status }: { status: SessionStatus }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "muted" as const };
  const Icon = ICON[status] ?? Clock;
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${TONE[meta.tone]}`}>
      <Icon className={`h-3.5 w-3.5 ${status === "running" ? "animate-spin" : ""}`} aria-hidden />
      {meta.label}
    </span>
  );
}
