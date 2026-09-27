import { CheckCircle2, CircleMinus, TrendingUp, XCircle } from "lucide-react";
import type { CaseStatus, RegressionResult } from "@/lib/engine/types";

export const STATUS_LABELS: Record<CaseStatus, string> = {
  regressed: "Regressed",
  still_failing: "Still failing",
  partly_fixed: "Partly fixed",
  error: "Bot error",
  fixed: "Fixed",
  still_passing: "Still passing",
};

const VERDICTS: Record<RegressionResult["verdict"], { label: string; tone: string; Icon: typeof CheckCircle2 }> = {
  regressions: { label: "Regressions", tone: "text-fail", Icon: XCircle },
  all_fixed: { label: "All fixed", tone: "text-pass", Icon: CheckCircle2 },
  progress: { label: "Progress", tone: "text-pass", Icon: TrendingUp },
  no_change: { label: "No change", tone: "text-muted-foreground", Icon: CircleMinus },
};

/** A replay's outcome: icon and words, never colour alone. */
export function SuiteVerdict({
  run,
}: {
  run: { verdict: RegressionResult["verdict"]; counts: Partial<Record<CaseStatus, number>> };
}) {
  const v = VERDICTS[run.verdict] ?? VERDICTS.no_change;
  const c = run.counts;
  return (
    <span className="inline-flex flex-col items-start gap-0.5 sm:items-end">
      <span className={`inline-flex items-center gap-1 text-xs font-semibold ${v.tone}`}>
        <v.Icon className="h-3.5 w-3.5" aria-hidden /> {v.label}
      </span>
      <span className="text-[11px] tabular-nums text-muted-foreground">
        {c.fixed ?? 0} fixed · {(c.still_failing ?? 0) + (c.partly_fixed ?? 0)} failing · {c.regressed ?? 0} regressed
      </span>
    </span>
  );
}
