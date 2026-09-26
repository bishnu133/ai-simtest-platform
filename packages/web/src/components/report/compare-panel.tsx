"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, CircleMinus, Loader2, Trophy, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { engine, EngineError } from "@/lib/engine/client";
import { formatMs, judgeLabel, pct, plural } from "@/lib/engine/report";
import type { CompareResult, CompareVersus, JudgedConversation } from "@/lib/engine/types";
import { ScoreBars } from "./charts";
import { EmptyNote, Panel } from "./parts";
import { TranscriptTurns } from "./transcript-sheet";

const signedPct = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v * 100))} pts`;

/** Verdict with an icon and a word: never colour alone. */
function Verdict({ verdict }: { verdict: CompareVersus["verdict"] }) {
  const [Icon, tone, label] =
    verdict === "better"
      ? [CheckCircle2, "text-pass", "Better"]
      : verdict === "worse"
        ? [XCircle, "text-fail", "Worse"]
        : [CircleMinus, "text-muted-foreground", verdict === "too close to call" ? "Too close to call" : "Not enough data"];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${tone}`}>
      <Icon className="h-3.5 w-3.5" aria-hidden /> {label}
    </span>
  );
}

/**
 * Each challenger's difference from the main bot: dot at the mean, a 2px line
 * across its 95% range, a recessive rule at zero. One series, no legend; the
 * rows are labelled. Hover shows the numbers; a table carries them for
 * screen readers.
 */
function DifferenceChart({ rows, baseline }: { rows: CompareVersus[]; baseline: string }) {
  const measured = rows.filter((r) => r.mean_difference != null);
  if (!measured.length) return <EmptyNote>Not enough paired customers to measure a difference.</EmptyNote>;
  const extent = Math.max(0.1, ...measured.flatMap((r) => [Math.abs(r.range_low ?? 0), Math.abs(r.range_high ?? 0)]));
  const span = Math.min(1, Math.ceil(extent * 10) / 10);
  const x = (v: number) => `${((v + span) / (2 * span)) * 100}%`;
  return (
    <figure aria-label={`Difference from ${baseline} in replies passed`} className="space-y-2">
      {measured.map((r) => (
        <div
          key={r.name}
          className="group grid grid-cols-[minmax(0,1fr)_6.5rem] items-center gap-x-3 sm:grid-cols-[minmax(6rem,9rem)_minmax(0,1fr)_7.5rem]"
        >
          <span className="col-span-2 truncate text-sm text-muted-foreground sm:col-span-1" title={r.name}>
            {r.name}
          </span>
          <div className="relative h-8">
            <div className="absolute inset-y-0 w-px bg-foreground/30" style={{ left: x(0) }} aria-hidden />
            <div
              className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-primary"
              style={{ left: x(r.range_low ?? 0), width: `calc(${x(r.range_high ?? 0)} - ${x(r.range_low ?? 0)})` }}
              aria-hidden
            />
            <div
              className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-card"
              style={{ left: x(r.mean_difference ?? 0) }}
              aria-hidden
            />
            <div
              role="tooltip"
              className="pointer-events-none absolute -top-10 z-10 hidden whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block"
              style={{ left: `min(${x(r.mean_difference ?? 0)}, calc(100% - 14rem))` }}
            >
              <span className="font-medium">{r.name}</span> {signedPct(r.mean_difference)} (range {signedPct(r.range_low)} to{" "}
              {signedPct(r.range_high)}) · {r.paired_personas} customers
            </div>
          </div>
          <span className="flex flex-col items-end">
            <span className="text-sm font-semibold tabular-nums text-foreground">{signedPct(r.mean_difference)}</span>
            <Verdict verdict={r.verdict} />
          </span>
        </div>
      ))}
      <div
        className="grid grid-cols-[minmax(0,1fr)_6.5rem] gap-x-3 text-[11px] text-muted-foreground sm:grid-cols-[minmax(6rem,9rem)_minmax(0,1fr)_7.5rem]"
        aria-hidden
      >
        <span className="hidden sm:block" />
        <div className="relative h-4 whitespace-nowrap">
          <span className="absolute left-0">{signedPct(-span)}</span>
          <span className="absolute max-w-[40%] -translate-x-1/2 truncate" style={{ left: x(0) }}>
            {baseline}
          </span>
          <span className="absolute right-0">{signedPct(span)}</span>
        </div>
        <span />
      </div>
      <figcaption className="text-xs text-muted-foreground">
        Dot: average difference per customer in replies passed. Line: the 95% range. A range that crosses {baseline} is too
        close to call.
      </figcaption>
      <div className="sr-only">
        <table>
          <caption>Difference from {baseline} in replies passed, per customer</caption>
          <thead>
            <tr>
              <th scope="col">Bot</th>
              <th scope="col">Difference</th>
              <th scope="col">95% range</th>
              <th scope="col">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {measured.map((r) => (
              <tr key={r.name}>
                <th scope="row">{r.name}</th>
                <td>{signedPct(r.mean_difference)}</td>
                <td>
                  {signedPct(r.range_low)} to {signedPct(r.range_high)}
                </td>
                <td>{r.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function headline(result: CompareResult): string {
  const leader = result.leader;
  if (!leader) return "No bot finished, so there is nothing to compare.";
  if (leader === result.baseline) {
    return result.decisive
      ? `${leader} did better than every other bot.`
      : `${leader} is ahead, but not by enough to be sure.`;
  }
  return result.decisive
    ? `${leader} did better than ${result.baseline}.`
    : `${leader} scored highest, but the difference from ${result.baseline} is too close to call.`;
}

export function ComparePanel({ simulationId, result }: { simulationId: string; result: CompareResult }) {
  const [open, setOpen] = useState<CompareResult["diverging"][number] | null>(null);
  const finished = result.bots.filter((b) => !b.error && b.pass_rate != null);
  const judges = Array.from(new Set(finished.flatMap((b) => Object.keys(b.judge_pass_rates ?? {}))));
  const versus = new Map(result.versus_baseline.map((v) => [v.name, v]));

  return (
    <Panel title="Model comparison" description={result.method}>
      <div className="mb-5 flex items-start gap-3 rounded-lg border bg-muted/30 p-4">
        <Trophy className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
        <div>
          <p className="font-semibold text-foreground">{headline(result)}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            The rest of this report is about {result.baseline}. Every bot&apos;s conversations are under Export.
          </p>
          {result.bots.some((b) => b.error) && (
            <p className="mt-1 text-sm text-muted-foreground">
              Did not finish: {result.bots.filter((b) => b.error).map((b) => `${b.name} (${b.error})`).join("; ")}
            </p>
          )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-sm font-semibold text-foreground">Replies passed, by bot</h4>
          <ScoreBars
            caption="Share of replies judged PASS, by bot"
            target={0.8}
            rows={finished.map((b) => ({ label: b.name, value: b.pass_rate ?? 0, hint: b.baseline ? "your bot" : undefined }))}
          />
        </div>
        <div>
          <h4 className="mb-2 text-sm font-semibold text-foreground">Difference from {result.baseline}, same customers</h4>
          <DifferenceChart rows={result.versus_baseline} baseline={result.baseline} />
        </div>
      </div>

      <div className="-mx-5 mt-6 overflow-x-auto px-5 relative">
        <table className="w-full min-w-[36rem] text-sm">
          <caption className="sr-only">Each bot&apos;s results</caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="py-2 pr-3 font-medium">Bot</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Critical</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Stuck</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Reply time (median · p95)</th>
              <th scope="col" className="py-2 pl-3 text-right font-medium">Customers won · lost · tied</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {finished.map((b) => {
              const v = versus.get(b.name);
              return (
                <tr key={b.name}>
                  <th scope="row" className="py-2.5 pr-3 text-left font-medium text-foreground">
                    {b.name}
                    {b.baseline && <span className="ml-1.5 text-xs font-normal text-muted-foreground">your bot</span>}
                  </th>
                  <td className="px-3 py-2.5 text-right tabular-nums">{b.critical_failures ?? 0}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{b.stuck_conversations ?? 0}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {b.latency_ms?.median != null ? `${formatMs(b.latency_ms.median)} · ${formatMs(b.latency_ms.p95 ?? 0)}` : "—"}
                  </td>
                  <td className="py-2.5 pl-3 text-right tabular-nums">{v ? `${v.wins} · ${v.losses} · ${v.ties}` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {judges.length > 0 && (
        <div className="-mx-5 mt-6 overflow-x-auto px-5 relative">
          <h4 className="mb-2 text-sm font-semibold text-foreground">Each judge&apos;s pass rate</h4>
          <table className="w-full min-w-[28rem] text-sm">
            <caption className="sr-only">Share of replies each judge passed, by bot</caption>
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">Judge</th>
                {finished.map((b) => (
                  <th key={b.name} scope="col" className="px-3 py-2 text-right font-medium">
                    {b.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {judges.map((j) => {
                const values = finished.map((b) => b.judge_pass_rates?.[j]);
                const best = Math.max(...values.map((v) => v ?? -1));
                return (
                  <tr key={j}>
                    <th scope="row" className="py-2 pr-3 text-left font-normal text-muted-foreground">
                      {judgeLabel(j)}
                    </th>
                    {values.map((v, i) => (
                      <td key={i} className={`px-3 py-2 text-right tabular-nums ${v === best ? "font-semibold text-foreground" : ""}`}>
                        {v != null ? pct(v) : "—"}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-6">
        <h4 className="mb-1 text-sm font-semibold text-foreground">Where they went different ways</h4>
        <p className="mb-3 text-xs text-muted-foreground">Customers whose conversations differed most between bots. Open one to read them side by side.</p>
        {result.diverging.length ? (
          <ul className="divide-y rounded-lg border">
            {result.diverging.map((d) => (
              <li key={d.persona_id} className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm">
                <div className="min-w-0">
                  <span className="font-medium text-foreground">{d.persona}</span>{" "}
                  <span className="text-xs text-muted-foreground">{judgeLabel(d.persona_type)}</span>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {d.bots.map((b) => `${b.name} ${pct(b.pass_share)}`).join(" · ")}
                  </div>
                </div>
                <Button variant="outline" size="xs" onClick={() => setOpen(d)}>
                  Side by side
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyNote>No customer&apos;s conversations differed by more than a third of the replies.</EmptyNote>
        )}
      </div>

      <SideBySide simulationId={simulationId} item={open} onClose={() => setOpen(null)} />
    </Panel>
  );
}

function Column({ simulationId, bot, conversationId }: { simulationId: string; bot: string; conversationId: string }) {
  const [state, setState] = useState<{ id: string; data?: JudgedConversation; error?: string } | null>(null);
  useEffect(() => {
    let live = true;
    engine
      .getComparedConversation(simulationId, conversationId)
      .then((r) => live && setState({ id: conversationId, data: r.judged_conversation }))
      .catch((e) => live && setState({ id: conversationId, error: e instanceof EngineError ? e.message : "Could not load it." }));
    return () => {
      live = false;
    };
  }, [simulationId, conversationId]);
  const current = state?.id === conversationId ? state : null;
  return (
    <section className="min-w-0 rounded-lg border p-3" aria-label={`${bot}'s conversation`}>
      <h3 className="mb-3 text-sm font-semibold text-foreground">{bot}</h3>
      {!current ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading…
        </p>
      ) : current.error ? (
        <p className="text-sm text-fail">{current.error}</p>
      ) : current.data ? (
        <TranscriptTurns conversation={current.data} />
      ) : null}
    </section>
  );
}

function SideBySide({
  simulationId,
  item,
  onClose,
}: {
  simulationId: string;
  item: CompareResult["diverging"][number] | null;
  onClose: () => void;
}) {
  return (
    <Sheet open={!!item} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-[min(96vw,110rem)]">
        {item && (
          <>
            <SheetHeader className="space-y-1 pr-6 text-left">
              <SheetTitle>{item.persona}</SheetTitle>
              <SheetDescription>
                The same customer with {plural(item.bots.length, "bot")}. The customer reacts to each bot&apos;s replies, so the
                conversations part ways after the first answer.
              </SheetDescription>
            </SheetHeader>
            <div className="mt-4 grid gap-4" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, 22rem), 1fr))` }}>
              {item.bots.map((b) => (
                <Column key={b.conversation_id} simulationId={simulationId} bot={b.name} conversationId={b.conversation_id} />
              ))}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
