import { ArrowDownRight, ArrowUpRight, CircleAlert, OctagonX, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import type { Verdict } from "@/lib/engine/report";
import type { TrendDelta } from "@/lib/engine/types";

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-fail/10 text-fail border-fail/20",
  high: "bg-warn/10 text-warn border-warn/20",
  medium: "bg-accent text-accent-foreground border-accent-foreground/15",
  low: "bg-muted text-muted-foreground border-border",
};

export function SeverityBadge({ severity }: { severity?: string }) {
  const key = (severity ?? "unknown").toLowerCase();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        SEVERITY_STYLES[key] ?? SEVERITY_STYLES.low
      }`}
    >
      {key}
    </span>
  );
}

/** Stat tile: label · value · optional delta vs the previous run. */
export function StatTile({
  label,
  value,
  sub,
  delta,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  delta?: TrendDelta;
}) {
  const changed = delta && delta.improved !== null;
  return (
    <div className="rounded-xl border bg-card p-4 shadow-xs">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-foreground">{value}</div>
      {delta && (
        <p className="mt-1 text-xs leading-snug text-muted-foreground">
          {changed &&
            (delta.delta > 0 ? (
              <ArrowUpRight className={`-mt-0.5 mr-0.5 inline h-3.5 w-3.5 ${delta.improved ? "text-pass" : "text-fail"}`} aria-hidden />
            ) : (
              <ArrowDownRight className={`-mt-0.5 mr-0.5 inline h-3.5 w-3.5 ${delta.improved ? "text-pass" : "text-fail"}`} aria-hidden />
            ))}
          <span className={changed ? (delta.improved ? "font-medium text-pass" : "font-medium text-fail") : undefined}>
            {delta.formatted}
          </span>{" "}
          vs previous run
        </p>
      )}
      {sub && !delta && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

const VERDICT_STYLES = {
  ready: { box: "border-pass/30 bg-pass/5", icon: ShieldCheck, iconClass: "text-pass" },
  attention: { box: "border-warn/30 bg-warn/5", icon: CircleAlert, iconClass: "text-warn" },
  blocked: { box: "border-fail/30 bg-fail/5", icon: OctagonX, iconClass: "text-fail" },
} as const;

export function VerdictBanner({ verdict }: { verdict: Verdict }) {
  const style = VERDICT_STYLES[verdict.level];
  return (
    <section className={`flex gap-4 rounded-xl border p-5 ${style.box}`} aria-label="Release readiness">
      <style.icon className={`h-8 w-8 shrink-0 ${style.iconClass}`} aria-hidden />
      <div className="min-w-0 space-y-1">
        <h2 className="text-lg font-semibold text-foreground">{verdict.title}</h2>
        <p className="text-sm text-foreground/80">
          {verdict.reasons.map((r, i) => (
            <span key={i}>
              {i > 0 && " · "}
              {r.charAt(0).toUpperCase() + r.slice(1)}
            </span>
          ))}
        </p>
        <p className="text-xs text-muted-foreground">
          Rule: release-ready at ≥ 80% pass rate with no critical findings and all quality gates passing.
        </p>
      </div>
    </section>
  );
}

export function Panel({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border bg-card p-5 shadow-xs ${className}`}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-foreground">{title}</h3>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}
