"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowLeft, ArrowUpRight, Minus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LoadingBlocks } from "@/components/ui/skeleton";
import { engine } from "@/lib/engine/client";
import { pct } from "@/lib/engine/report";
import { RISKS } from "@/lib/engine/risks";
import type { BotHistoryRun } from "@/lib/engine/types";
import { HealthPill, initials } from "./bots-page";
import { useNow } from "./runs-table";

const TARGET = 0.8;
const TICKS = [1, 0.5, 0];

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Whether this run tested a different bot build from the run before it (both known). */
function buildChanged(runs: BotHistoryRun[], i: number) {
  return i > 0 && !!runs[i].build && !!runs[i - 1].build && runs[i].build !== runs[i - 1].build;
}

/**
 * Pass rate per run, oldest first: one series (the title names it, so no
 * legend), columns anchored to the baseline with 4px rounded tops and a 2px
 * gap; the 80% target is a recessive dashed rule; a diamond above a column
 * marks a new bot build. Hover or focus shows the run; a table carries the
 * same data for screen readers.
 */
function PassRateTrend({ runs }: { runs: BotHistoryRun[] }) {
  const anyBuild = runs.some((_, i) => buildChanged(runs, i));
  return (
    <figure aria-label="Pass rate by run" className="space-y-2">
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2">
        <div className="relative h-52 text-right text-[11px] tabular-nums text-muted-foreground" aria-hidden>
          {TICKS.map((t) => (
            <span key={t} className="absolute right-0 -translate-y-1/2" style={{ top: `${(1 - t) * 100}%` }}>
              {pct(t)}
            </span>
          ))}
        </div>
        <div className="relative h-52">
          {TICKS.map((t) => (
            <div key={t} className="absolute inset-x-0 border-t border-border" style={{ top: `${(1 - t) * 100}%` }} aria-hidden />
          ))}
          <div className="absolute inset-x-0 border-t border-dashed border-foreground/40" style={{ top: `${(1 - TARGET) * 100}%` }} aria-hidden />
          <div className="absolute inset-0 flex items-end gap-[2px] px-1">
            {runs.map((run, i) => {
              const value = run.pass_rate ?? 0;
              const changed = buildChanged(runs, i);
              return (
                <Link
                  key={run.id}
                  href={`/simulations/${run.id}`}
                  className="group relative flex h-full min-w-0 flex-1 flex-col items-center justify-end outline-none"
                  aria-label={`${run.name}, ${shortDate(run.created_at)}: pass rate ${pct(value)}${changed ? ", new bot build" : ""}`}
                >
                  {changed && (
                    <span className="mb-1 h-2.5 w-2.5 rotate-45 rounded-[2px] bg-foreground/70 ring-2 ring-card" aria-hidden />
                  )}
                  <span
                    className="w-full max-w-10 rounded-t-[4px] bg-primary transition-[filter] group-hover:brightness-110 group-focus-visible:ring-2 group-focus-visible:ring-ring"
                    style={{ height: `${Math.max(value, 0.01) * 100}%` }}
                  />
                  <span
                    role="tooltip"
                    className="pointer-events-none absolute bottom-full z-10 mb-2 hidden w-56 rounded-md border bg-popover p-2.5 text-xs text-popover-foreground shadow-md group-hover:block group-focus-visible:block"
                  >
                    <span className="block text-base font-semibold tabular-nums">{pct(value)}</span>
                    <span className="block truncate text-muted-foreground">
                      {run.name} · {new Date(run.created_at).toLocaleString()}
                    </span>
                    <span className="mt-1.5 grid grid-cols-2 gap-x-2 gap-y-0.5">
                      <span className="text-muted-foreground">Conversations</span>
                      <span className="text-right tabular-nums">{run.conversations}</span>
                      <span className="text-muted-foreground">Critical</span>
                      <span className="text-right tabular-nums">{run.critical_failures}</span>
                      {run.build && (
                        <>
                          <span className="text-muted-foreground">Build</span>
                          <span className="truncate text-right font-mono">
                            {run.build.slice(0, 12)}
                            {changed ? " (new)" : ""}
                          </span>
                        </>
                      )}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2" aria-hidden>
        <span />
        <div className="flex gap-[2px] px-1 text-[11px] text-muted-foreground">
          {runs.map((run, i) => (
            <span key={run.id} className="min-w-0 flex-1 truncate text-center">
              {runs.length <= 8 || i % Math.ceil(runs.length / 6) === 0 || i === runs.length - 1 ? shortDate(run.created_at) : ""}
            </span>
          ))}
        </div>
      </div>
      <figcaption className="flex flex-wrap items-center gap-4 pl-12 text-xs text-muted-foreground">
        <span className="flex items-center gap-2">
          <span className="inline-block w-5 border-t border-dashed border-foreground/40" aria-hidden /> Target {pct(TARGET)}
        </span>
        {anyBuild && (
          <span className="flex items-center gap-2">
            <span className="inline-block h-2 w-2 rotate-45 rounded-[1px] bg-foreground/70" aria-hidden /> New bot build
          </span>
        )}
      </figcaption>
      <table className="sr-only">
        <caption>Pass rate by run, oldest first</caption>
        <thead>
          <tr>
            <th scope="col">Run</th>
            <th scope="col">Date</th>
            <th scope="col">Pass rate</th>
            <th scope="col">Build</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, i) => (
            <tr key={run.id}>
              <th scope="row">{run.name}</th>
              <td>{new Date(run.created_at).toLocaleString()}</td>
              <td>{pct(run.pass_rate ?? 0)}</td>
              <td>
                {run.build || "not recorded"}
                {buildChanged(runs, i) ? " (new)" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function Change({ now, before, higherIsBetter, points }: { now: number; before: number | undefined; higherIsBetter: boolean; points?: boolean }) {
  if (before === undefined) return <span className="text-xs text-muted-foreground">first run</span>;
  const d = now - before;
  if (Math.abs(d) < (points ? 0.005 : 0.5)) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-muted-foreground">
        <Minus className="h-3 w-3" aria-hidden /> no change
      </span>
    );
  }
  const good = higherIsBetter ? d > 0 : d < 0;
  const Icon = d > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${good ? "text-pass" : "text-fail"}`}>
      <Icon className="h-3 w-3" aria-hidden />
      {points ? `${d > 0 ? "+" : ""}${Math.round(d * 100)} pts` : `${d > 0 ? "+" : ""}${d}`}
      <span className="sr-only">{good ? " better" : " worse"}</span>
    </span>
  );
}

export function BotDetail({ id }: { id: string }) {
  const now = useNow();
  const { data, isPending, isError, error } = useQuery({ queryKey: ["bot-history", id], queryFn: () => engine.botHistory(id) });

  if (isPending)
    return (
      <div className="mx-auto w-full max-w-6xl p-4 md:p-8">
        <LoadingBlocks variant="dashboard" />
      </div>
    );
  if (isError || !data)
    return (
      <div className="mx-auto w-full max-w-6xl space-y-4 p-4 md:p-8">
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load this bot."}
        </p>
        <Button asChild variant="outline">
          <Link href="/bots">
            <ArrowLeft /> All bots
          </Link>
        </Button>
      </div>
    );

  const { bot, runs } = data;
  const latest = runs.at(-1);
  const previous = runs.at(-2);
  const newestFirst = [...runs].reverse();

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
      <Link href="/bots" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Bots
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-base font-bold text-primary">
            {initials(bot.name)}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-2xl font-bold tracking-tight text-foreground">{bot.name}</h2>
              <HealthPill bot={bot} now={now} />
            </div>
            <p className="truncate font-mono text-xs text-muted-foreground">{bot.bot_endpoint}</p>
          </div>
        </div>
        <Button asChild>
          <Link href={`/new?bot=${encodeURIComponent(bot.id)}`}>New test</Link>
        </Button>
      </div>

      {runs.length === 0 ? (
        <div className="rounded-xl border border-dashed bg-card px-6 py-12 text-center text-sm text-muted-foreground">
          No finished tests for this bot yet. Its trend appears here after the first one.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-xl border bg-card p-4 shadow-xs">
              <p className="text-xs text-muted-foreground">Latest pass rate</p>
              <p className="text-2xl font-bold tabular-nums text-foreground">{pct(latest?.pass_rate ?? 0)}</p>
              <Change now={latest?.pass_rate ?? 0} before={previous?.pass_rate ?? undefined} higherIsBetter points />
            </div>
            <div className="rounded-xl border bg-card p-4 shadow-xs">
              <p className="text-xs text-muted-foreground">Critical findings</p>
              <p className="text-2xl font-bold tabular-nums text-foreground">{latest?.critical_failures ?? 0}</p>
              <Change now={latest?.critical_failures ?? 0} before={previous?.critical_failures} higherIsBetter={false} />
            </div>
            <div className="rounded-xl border bg-card p-4 shadow-xs">
              <p className="text-xs text-muted-foreground">Best pass rate</p>
              <p className="text-2xl font-bold tabular-nums text-foreground">{pct(Math.max(...runs.map((r) => r.pass_rate ?? 0)))}</p>
              <span className="text-xs text-muted-foreground">over {runs.length} run{runs.length === 1 ? "" : "s"}</span>
            </div>
            <div className="rounded-xl border bg-card p-4 shadow-xs">
              <p className="text-xs text-muted-foreground">Bot build</p>
              <p className="truncate font-mono text-lg font-semibold text-foreground" title={latest?.build || undefined}>
                {latest?.build ? latest.build.slice(0, 12) : "—"}
              </p>
              <span className="text-xs text-muted-foreground">
                {latest?.build ? (previous && previous.build && previous.build !== latest.build ? "new in the latest run" : "same as before") : "not recorded"}
              </span>
            </div>
          </div>

          <section className="rounded-xl border bg-card p-5 shadow-xs">
            <h3 className="font-semibold text-foreground">Pass rate by run</h3>
            <p className="mb-4 text-sm text-muted-foreground">Oldest to newest. Select a column to open that run.</p>
            <PassRateTrend runs={runs} />
          </section>

          <section className="rounded-xl border bg-card shadow-xs">
            <div className="border-b p-5">
              <h3 className="font-semibold text-foreground">Where it was weak, run by run</h3>
              <p className="text-sm text-muted-foreground">Conversations with at least one reply failing each check. Newest first.</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-4 py-2.5 font-medium">
                      Run
                    </th>
                    <th scope="col" className="px-3 py-2.5 text-right font-medium">
                      Pass rate
                    </th>
                    {RISKS.map((r) => (
                      <th key={r.id} scope="col" className="px-3 py-2.5 text-right font-medium" title={r.about}>
                        {r.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {newestFirst.map((run, i) => {
                    const older = newestFirst[i + 1];
                    return (
                      <tr key={run.id} className="hover:bg-muted/30">
                        <th scope="row" className="px-4 py-2.5 text-left font-normal">
                          <Link href={`/simulations/${run.id}`} className="font-medium text-foreground hover:text-primary hover:underline">
                            {shortDate(run.created_at)}
                          </Link>
                          <span className="ml-2 text-xs text-muted-foreground">{run.name}</span>
                        </th>
                        <td className="px-3 py-2.5 text-right font-semibold tabular-nums text-foreground">{pct(run.pass_rate ?? 0)}</td>
                        {RISKS.map((r) => {
                          const n = run.risks[r.id];
                          const before = older?.risks[r.id];
                          return (
                            <td key={r.id} className="px-3 py-2.5 text-right tabular-nums">
                              <span className={n ? "font-medium text-foreground" : "text-muted-foreground"}>{n}</span>
                              {before !== undefined && n !== before && (
                                <span className={`ml-1 text-[11px] ${n < before ? "text-pass" : "text-fail"}`}>
                                  {n < before ? "▼" : "▲"}
                                  <span className="sr-only">{n < before ? " fewer than before" : " more than before"}</span>
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
