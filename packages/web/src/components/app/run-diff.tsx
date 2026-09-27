"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowLeftRight, ArrowUpRight, CheckCircle2, CircleMinus, Loader2, TriangleAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DifferenceChart } from "@/components/report/compare-panel";
import { EmptyNote, Panel } from "@/components/report/parts";
import { engine } from "@/lib/engine/client";
import { judgeLabel, pct } from "@/lib/engine/report";
import type { RunDiff } from "@/lib/engine/types";

const fmt = (v: unknown, percentage = false) =>
  v == null ? "—" : typeof v === "number" ? (percentage ? pct(v) : String(Math.round(v * 100) / 100)) : typeof v === "object" ? JSON.stringify(v) : String(v);

const signed = (v: number | null, percentage: boolean) =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${percentage ? `${Math.abs(Math.round(v * 100))} pts` : Math.abs(Math.round(v * 100) / 100)}`;

/**
 * Which way a number moved. Direction only, in muted ink: whether a change is
 * more than chance is the range above, and a green "Better" beside a result
 * that is too close to call said the opposite of it.
 */
function Change({ better }: { better: boolean | null }) {
  if (better == null) return <span className="text-xs text-muted-foreground">No change</span>;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      {better ? <ArrowUpRight className="h-3.5 w-3.5" aria-hidden /> : <ArrowDownRight className="h-3.5 w-3.5" aria-hidden />}
      {better ? "better direction" : "worse direction"}
    </span>
  );
}

function RunHead({ label, run }: { label: string; run: RunDiff["before"] }) {
  return (
    <div className="min-w-0 rounded-lg border bg-card p-4">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <Link href={`/simulations/${run.id}`} className="block truncate text-base font-semibold text-foreground hover:text-primary hover:underline">
        {run.name}
      </Link>
      <p className="text-xs text-muted-foreground">
        {run.created_at ? new Date(run.created_at).toLocaleString() : ""}
        {run.bot_build ? <span className="font-mono"> · build {run.bot_build.slice(0, 12)}</span> : " · build not recorded"}
      </p>
    </div>
  );
}

export function RunDiffView({ before, after }: { before: string; after: string }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["run-diff", before, after],
    queryFn: () => engine.compareRuns(before, after),
    enabled: !!before && !!after,
  });

  if (!before || !after) {
    return (
      <div className="mx-auto max-w-3xl p-4 md:p-8 text-sm text-muted-foreground">
        Pick two finished runs on the <Link href="/runs" className="text-primary hover:underline">Runs</Link> page to compare them.
      </div>
    );
  }
  if (isPending) {
    return (
      <div className="flex justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <p role="alert" className="mx-auto max-w-3xl p-8 text-sm font-medium text-destructive">
        {error instanceof Error ? error.message : "Could not compare these runs."}
      </p>
    );
  }

  const d = data.difference;
  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
      <div className="grid items-center gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <RunHead label="Before" run={data.before} />
        <Button asChild variant="ghost" size="icon" aria-label="Swap before and after" className="justify-self-center">
          <Link href={`/runs/compare?before=${data.after.id}&after=${data.before.id}`}>
            <ArrowLeftRight />
          </Link>
        </Button>
        <RunHead label="After" run={data.after} />
      </div>

      <div
        role="note"
        className={`flex gap-3 rounded-lg border p-4 text-sm ${data.comparable ? "bg-muted/30" : "border-warn/30 bg-warn/5"}`}
      >
        {data.comparable ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-pass" aria-hidden />
        ) : (
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        )}
        <div className="space-y-1">
          <p className="font-medium text-foreground">{data.note}</p>
          {data.build_changed && <p className="text-muted-foreground">The bot build is different between the two runs.</p>}
        </div>
      </div>

      {!data.comparable && (
        <Panel title="What changed in the test" description="Settings that differ between the two runs.">
          <div className="-mx-5 overflow-x-auto px-5">
            <table className="w-full min-w-[28rem] text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-2 pr-3 font-medium">Setting</th>
                  <th scope="col" className="px-3 py-2 font-medium">Before</th>
                  <th scope="col" className="py-2 pl-3 font-medium">After</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.settings_changed.map((s) => (
                  <tr key={s.label}>
                    <th scope="row" className="py-2 pr-3 text-left font-medium text-foreground">{s.label}</th>
                    <td className="break-all px-3 py-2 text-muted-foreground">{fmt(s.before)}</td>
                    <td className="break-all py-2 pl-3 text-muted-foreground">{fmt(s.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel
        title="Did it get better?"
        description={
          d.method === "paired"
            ? "Both runs cover the same saved conversations, so replies are compared one to one."
            : "Different customers in each run, so the runs are compared as two samples; expect a wider range."
        }
      >
        <DifferenceChart
          baseline="Before"
          rows={[{ name: "After", mean_difference: d.mean, range_low: d.low, range_high: d.high, verdict: d.verdict, paired_personas: 0 }]}
        />
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Headline numbers" description="Which way each number moved; whether it is more than chance is shown above.">
          <div className="-mx-5 overflow-x-auto px-5">
            <table className="w-full min-w-[26rem] text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-2 pr-3 font-medium">Measure</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Before</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">After</th>
                  <th scope="col" className="py-2 pl-3 text-right font-medium">Change</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.metrics.map((m) => (
                  <tr key={m.key}>
                    <th scope="row" className="py-2 pr-3 text-left font-normal text-muted-foreground">{m.label}</th>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(m.before, m.percentage)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(m.after, m.percentage)}</td>
                    <td className="py-2 pl-3 text-right">
                      <span className="block tabular-nums text-foreground">{signed(m.delta, m.percentage)}</span>
                      <Change better={m.better} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Each judge's pass rate">
          {data.judges.length ? (
            <div className="-mx-5 overflow-x-auto px-5">
              <table className="w-full min-w-[22rem] text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th scope="col" className="py-2 pr-3 font-medium">Judge</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Before</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">After</th>
                    <th scope="col" className="py-2 pl-3 text-right font-medium">Change</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {data.judges.map((j) => (
                    <tr key={j.judge}>
                      <th scope="row" className="py-2 pr-3 text-left font-normal text-muted-foreground">{judgeLabel(j.judge)}</th>
                      <td className="px-3 py-2 text-right tabular-nums">{fmt(j.before, true)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmt(j.after, true)}</td>
                      <td className="py-2 pl-3 text-right tabular-nums">{signed(j.delta, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyNote>No judge results to compare.</EmptyNote>
          )}
        </Panel>
      </div>

      <Panel title="Failures" description="What went wrong, by kind: gone, new, and still there.">
        <div className="grid gap-4 md:grid-cols-3">
          {(
            [
              ["Resolved", data.failures.resolved, CheckCircle2, "text-pass", (f: { before?: number }) => `${f.before ?? 0} before`],
              ["New", data.failures.new, XCircle, "text-fail", (f: { after?: number }) => `${f.after ?? 0} now`],
              ["Still there", data.failures.persistent, CircleMinus, "text-muted-foreground",
                (f: { before?: number; after?: number }) => `${f.before ?? 0} → ${f.after ?? 0}`],
            ] as const
          ).map(([title, items, Icon, tone, count]) => (
            <div key={title}>
              <h4 className={`mb-2 flex items-center gap-1.5 text-sm font-semibold ${tone}`}>
                <Icon className="h-4 w-4" aria-hidden /> {title} <span className="tabular-nums text-muted-foreground">({items.length})</span>
              </h4>
              {items.length ? (
                <ul className="space-y-1.5 text-sm">
                  {items.map((f) => (
                    <li key={f.title} className="flex justify-between gap-3">
                      <span className="min-w-0 text-foreground">{f.title}</span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{count(f as never)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyNote>None.</EmptyNote>
              )}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
