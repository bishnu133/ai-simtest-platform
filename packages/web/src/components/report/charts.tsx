"use client";

import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import { pct } from "@/lib/engine/report";

type BarRow = { label: string; value: number; hint?: string };

/**
 * Single-series horizontal bars for 0–1 scores. One colour (no legend — the
 * card title names the series); value at the bar tip in text ink; optional
 * target marker; hover shows the exact value.
 */
export function ScoreBars({
  rows,
  target,
  caption,
}: {
  rows: BarRow[];
  target?: number;
  caption: string;
}) {
  const norm = (v: number) => Math.max(0, Math.min(1, v <= 1 ? v : v / 100));
  return (
    <figure className="space-y-2.5" aria-label={caption}>
      {rows.map((row) => {
        const v = norm(row.value);
        return (
          <div key={row.label} className="group relative grid grid-cols-[minmax(6rem,9rem)_1fr_3rem] items-center gap-3">
            <span className="truncate text-sm text-muted-foreground" title={row.label}>
              {row.label}
            </span>
            <div className="relative h-3.5 rounded-r-[4px] bg-muted">
              <div
                className="h-full rounded-r-[4px] bg-primary transition-[filter] group-hover:brightness-110"
                style={{ width: `${v * 100}%` }}
              />
              {target !== undefined && (
                <div
                  className="absolute -top-1 -bottom-1 w-px bg-foreground/40"
                  style={{ left: `${target * 100}%` }}
                  aria-hidden
                />
              )}
              <div
                role="tooltip"
                className="pointer-events-none absolute -top-9 z-10 hidden whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block"
                style={{ left: `min(${v * 100}%, calc(100% - 8rem))` }}
              >
                <span className="font-medium">{row.label}</span> · {pct(row.value)}
                {row.hint ? ` · ${row.hint}` : ""}
              </div>
            </div>
            <span className="text-right text-sm font-semibold tabular-nums text-foreground">{pct(row.value)}</span>
          </div>
        );
      })}
      {target !== undefined && (
        <figcaption className="flex items-center gap-2 pt-1 text-xs text-muted-foreground">
          <span className="inline-block h-3 w-px bg-foreground/40" aria-hidden /> Target {pct(target)}
        </figcaption>
      )}
      {/* A table ignores the 1px sr-only width; the wrapper keeps a long caption from widening the page */}
      <div className="sr-only">
        <table>
          <caption>{caption}</caption>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                <td>{pct(r.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

const STATUS_SEGMENTS = [
  { key: "pass", label: "Pass", fill: "bg-pass", icon: CheckCircle2, iconClass: "text-pass" },
  { key: "warn", label: "Warning", fill: "bg-warn", icon: CircleAlert, iconClass: "text-warn" },
  { key: "fail", label: "Fail", fill: "bg-fail", icon: XCircle, iconClass: "text-fail" },
] as const;

/** Part-to-whole of judged turns: one 100% bar in the reserved status colours, 2px gaps, legend with icon + count. */
export function TurnLabelBar({ counts }: { counts: Record<"pass" | "warn" | "fail", number> }) {
  const total = counts.pass + counts.warn + counts.fail;
  if (!total) return <p className="text-sm text-muted-foreground">No judged turns.</p>;
  return (
    <figure className="space-y-4" aria-label="Judged turns by label">
      <div className="flex h-4 w-full gap-[2px]">
        {STATUS_SEGMENTS.map((s) => {
          const n = counts[s.key];
          if (!n) return null;
          return (
            <div
              key={s.key}
              className={`group relative h-full first:rounded-l-[4px] last:rounded-r-[4px] ${s.fill}`}
              style={{ width: `${(n / total) * 100}%` }}
            >
              <div
                role="tooltip"
                className="pointer-events-none absolute -top-9 left-0 z-10 hidden whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block"
              >
                {s.label}: {n} of {total} ({pct(n / total)})
              </div>
            </div>
          );
        })}
      </div>
      <figcaption className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {STATUS_SEGMENTS.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <s.icon className={`h-4 w-4 ${s.iconClass}`} aria-hidden />
            <span className="text-foreground">{s.label}</span>
            <span className="tabular-nums text-muted-foreground">
              {counts[s.key]} · {pct(counts[s.key] / total)}
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
