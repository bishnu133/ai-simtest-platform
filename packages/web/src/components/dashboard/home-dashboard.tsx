"use client";

import Link from "next/link";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Hand, Loader2, Plug, Plus, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { RunsTable, useNow } from "@/components/app/runs-table";
import { StatusBadge } from "@/components/app/status-badge";
import { pct, scoreTone } from "@/lib/engine/report";
import { useRuns } from "@/lib/engine/queries";
import { botHost, isActive, isCompleted, needsReview, relativeTime, stuckShare } from "@/lib/engine/runs";
import { humanize } from "@/lib/engine/stages";
import type { SimulationStatus } from "@/lib/engine/types";
import { TEST_TYPES } from "@/lib/test-types";
import { PassRateByRunChart } from "./pass-rate-chart";

const CHART_RUNS = 12;

function Panel({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border bg-card p-5 shadow-xs ${className}`}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Kpi({
  label,
  value,
  sub,
  change,
  higherIsBetter = true,
  unit = "pts",
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  /** Change vs the previous run, in percentage points */
  change?: number;
  higherIsBetter?: boolean;
  /** "pts" for rates (change is a 0-1 difference), "count" for whole numbers */
  unit?: "pts" | "count";
  tone?: string;
}) {
  const moved = change !== undefined && Math.abs(change) >= (unit === "pts" ? 0.005 : 1);
  const good = moved && (change! > 0) === higherIsBetter;
  return (
    <div className="rounded-xl border bg-card p-4 shadow-xs">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${tone ?? "text-foreground"}`}>{value}</div>
      {moved ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {change! > 0 ? (
            <ArrowUpRight className={`-mt-0.5 mr-0.5 inline h-3.5 w-3.5 ${good ? "text-pass" : "text-fail"}`} aria-hidden />
          ) : (
            <ArrowDownRight className={`-mt-0.5 mr-0.5 inline h-3.5 w-3.5 ${good ? "text-pass" : "text-fail"}`} aria-hidden />
          )}
          <span className={`font-medium ${good ? "text-pass" : "text-fail"}`}>
            {change! > 0 ? "+" : "−"}
            {unit === "pts" ? `${Math.round(Math.abs(change!) * 100)} pts` : Math.abs(change!)}
          </span>{" "}
          vs previous run of this bot
        </p>
      ) : (
        sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
      )}
    </div>
  );
}

function Attention({ runs, now }: { runs: SimulationStatus[]; now: number }) {
  if (!runs.length) return null;
  return (
    <Panel title="In progress" description="Runs waiting for your review come first.">
      <ul className="divide-y">
        {runs.map((run) => (
          <li key={run.simulation_id} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${needsReview(run) ? "bg-warn/10 text-warn" : "bg-primary/10 text-primary"}`}
              aria-hidden
            >
              {needsReview(run) ? <Hand className="h-4 w-4" /> : <Loader2 className="h-4 w-4 animate-spin" />}
            </span>
            <div className="min-w-0 flex-1 max-sm:basis-[calc(100%-3rem)]">
              <div className="truncate font-medium text-foreground">{run.name}</div>
              <div className="truncate text-xs text-muted-foreground">
                {needsReview(run) && run.pending_gate
                  ? `Waiting for you to review: ${run.pending_gate.title}`
                  : `${humanize(run.stage)} · step ${run.stage_number + 1} of ${run.total_stages + 1}`}{" "}
                · started {relativeTime(run.created_at, now)}
              </div>
            </div>
            <StatusBadge status={run.status} />
            <Button asChild size="sm" variant={needsReview(run) ? "default" : "outline"}>
              <Link href={`/simulations/${run.simulation_id}`}>
                {needsReview(run) ? "Review now" : "Open"} <ArrowRight />
              </Link>
            </Button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function TestTypeGrid() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {TEST_TYPES.map((t) => (
        <Link
          key={t.id}
          href={`/new?type=${t.id}`}
          className="group flex gap-3 rounded-xl border bg-card p-4 shadow-xs transition-colors hover:border-primary/40 hover:bg-primary/[0.03]"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <t.icon className="h-[18px] w-[18px]" aria-hidden />
          </span>
          <span className="min-w-0">
            <span className="flex items-center gap-2 font-medium text-foreground">
              {t.name}
              {!t.available && (
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Soon
                </span>
              )}
            </span>
            <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">{t.tagline}</span>
          </span>
        </Link>
      ))}
    </div>
  );
}

function EngineDown() {
  return (
    <div className="rounded-xl border border-fail/30 bg-fail/5 p-6">
      <div className="flex items-start gap-3">
        <Plug className="mt-0.5 h-5 w-5 text-fail" aria-hidden />
        <div className="space-y-2">
          <h2 className="font-semibold text-foreground">The AI SimTest engine isn&apos;t reachable</h2>
          <p className="text-sm text-muted-foreground">Start it in the engine repository, then this page updates on its own:</p>
          <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs text-foreground">
            API_HOST=127.0.0.1 API_PORT=8100 simtest serve
          </pre>
        </div>
      </div>
    </div>
  );
}

function Welcome() {
  const steps = [
    { title: "Connect your bot", body: "Paste its chat endpoint and, if it needs one, an API key." },
    { title: "Add its knowledge", body: "Upload the Markdown the bot answers from. AI drafts criteria, guardrails and personas." },
    { title: "Review and run", body: "Approve or edit each proposal, then watch realistic customers talk to your bot." },
  ];
  return (
    <section className="overflow-hidden rounded-xl border bg-card shadow-xs">
      <div className="grid gap-6 p-6 md:grid-cols-[1fr_auto] md:items-center md:p-8">
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
            <Sparkles className="h-3.5 w-3.5" aria-hidden /> Get started
          </p>
          <h2 className="text-2xl font-bold tracking-tight text-foreground">Test your AI assistant before your customers do</h2>
          <p className="max-w-2xl text-sm text-muted-foreground">
            AI SimTest runs persona-driven conversations against your bot and judges every reply for quality, grounding, safety
            and your own rules.
          </p>
        </div>
        <Button asChild size="lg">
          <Link href="/new">
            <Plus /> Start your first test
          </Link>
        </Button>
      </div>
      <ol className="grid border-t bg-muted/30 md:grid-cols-3">
        {steps.map((s, i) => (
          <li key={s.title} className="flex gap-3 border-b p-5 last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
              {i + 1}
            </span>
            <span>
              <span className="block text-sm font-medium text-foreground">{s.title}</span>
              <span className="mt-0.5 block text-sm text-muted-foreground">{s.body}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function HomeDashboard() {
  const { data: runs, isPending, isError } = useRuns();
  const now = useNow();

  if (isPending) {
    return (
      <div className="flex justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    );
  }

  const all = runs ?? [];
  const completed = all.filter(isCompleted);
  const latest = completed[0];
  const previous = latest && completed.slice(1).find((r) => r.config.bot_endpoint === latest.config.bot_endpoint);
  const inProgress = all.filter(isActive).sort((a, b) => Number(needsReview(b)) - Number(needsReview(a)));
  const chartRuns = completed.slice(0, CHART_RUNS).reverse();
  const latestStuck = latest ? stuckShare(latest) : undefined;
  const previousStuck = previous ? stuckShare(previous) : undefined;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Overview</h2>
          <p className="text-sm text-muted-foreground">How your AI assistants are doing, and what needs your attention.</p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <Link href="/runs">All runs</Link>
          </Button>
          <Button asChild>
            <Link href="/new">
              <Plus /> New test
            </Link>
          </Button>
        </div>
      </div>

      {isError && <EngineDown />}

      {!isError && all.length === 0 && <Welcome />}

      <Attention runs={inProgress} now={now} />

      {latest && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi
              label="Latest pass rate"
              value={pct(latest.summary?.pass_rate)}
              tone={scoreTone(latest.summary?.pass_rate)}
              change={
                previous?.summary?.pass_rate != null && latest.summary?.pass_rate != null
                  ? latest.summary.pass_rate - previous.summary.pass_rate
                  : undefined
              }
              sub={`${latest.name} · ${relativeTime(latest.created_at, now)}`}
            />
            <Kpi
              label="Stuck conversations"
              value={latestStuck != null ? pct(latestStuck) : "—"}
              change={latestStuck != null && previousStuck != null ? latestStuck - previousStuck : undefined}
              higherIsBetter={false}
              sub={
                latest.summary?.stuck_conversations != null
                  ? `${latest.summary.stuck_conversations} of ${latest.summary.total_conversations} in the latest run`
                  : undefined
              }
            />
            <Kpi
              label="Critical findings"
              value={String(latest.summary?.critical_failures ?? 0)}
              tone={latest.summary?.critical_failures ? "text-fail" : undefined}
              change={
                previous?.summary?.critical_failures != null && latest.summary?.critical_failures != null
                  ? latest.summary.critical_failures - previous.summary.critical_failures
                  : undefined
              }
              higherIsBetter={false}
              unit="count"
              sub="in the latest run"
            />
            <Kpi
              label="Runs"
              value={String(all.length)}
              sub={`${completed.length} completed · ${inProgress.length} in progress`}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Panel
              title="Pass rate by run"
              description={`The last ${chartRuns.length} completed ${chartRuns.length === 1 ? "run" : "runs"}, oldest first. Select a column to open the run.`}
              className="lg:col-span-2"
            >
              <PassRateByRunChart runs={chartRuns} />
            </Panel>
            <Panel
              title="Latest run"
              action={
                <Button asChild variant="ghost" size="sm">
                  <Link href={`/simulations/${latest.simulation_id}`}>
                    Report <ArrowRight />
                  </Link>
                </Button>
              }
            >
              <dl className="space-y-3 text-sm">
                <div>
                  <dt className="text-muted-foreground">Run</dt>
                  <dd className="truncate font-medium text-foreground">{latest.name}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Bot</dt>
                  <dd className="truncate font-mono text-xs text-foreground">{botHost(latest.config.bot_endpoint)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Build</dt>
                  <dd className={`font-mono text-xs ${latest.bot_build ? "text-foreground" : "text-warn"}`}>
                    {latest.bot_build || "Not recorded — set a Bot Info URL"}
                  </dd>
                </div>
                <div className="grid grid-cols-3 gap-2 border-t pt-3">
                  <div>
                    <dt className="text-xs text-muted-foreground">Conversations</dt>
                    <dd className="font-semibold tabular-nums">{latest.summary?.total_conversations ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Replies judged</dt>
                    <dd className="font-semibold tabular-nums">{latest.summary?.total_turns ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Warnings</dt>
                    <dd className="font-semibold tabular-nums">{latest.summary?.warnings ?? "—"}</dd>
                  </div>
                </div>
              </dl>
            </Panel>
          </div>
        </>
      )}

      {all.length > 0 && (
        <Panel
          title="Recent runs"
          action={
            <Button asChild variant="ghost" size="sm">
              <Link href="/runs">
                All runs <ArrowRight />
              </Link>
            </Button>
          }
        >
          <RunsTable runs={all} compact limit={6} />
        </Panel>
      )}

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">Start a test</h2>
          <p className="text-sm text-muted-foreground">Everything the engine can evaluate. More types arrive through Phase 2.</p>
        </div>
        <TestTypeGrid />
      </section>
    </div>
  );
}
