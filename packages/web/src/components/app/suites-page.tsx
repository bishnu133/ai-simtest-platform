"use client";

import { LoadingBlocks } from "@/components/ui/skeleton";
import Link from "next/link";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ListRestart, Play, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSuites } from "@/components/setup/regression-form";
import { SuiteVerdict } from "@/components/setup/suite-verdict";
import { engine, EngineError } from "@/lib/engine/client";
import { plural } from "@/lib/engine/report";
import { botHost, relativeTime } from "@/lib/engine/runs";
import type { SuiteSummary } from "@/lib/engine/types";
import { useNow } from "./runs-table";

function SuiteCard({ suite, now }: { suite: SuiteSummary; now: number }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const remove = async () => {
    try {
      await engine.deleteSuite(suite.id);
      queryClient.invalidateQueries({ queryKey: ["suites"] });
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not delete it.");
    }
  };
  const runs = [...suite.runs].reverse();
  return (
    <li className="space-y-4 rounded-xl border bg-card p-5 shadow-xs">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-base font-semibold text-foreground">
            <ListRestart className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            <span className="truncate">{suite.name}</span>
          </h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {plural(suite.cases, "case")}: {suite.failing_cases} failing, {suite.warning_cases} warnings,{" "}
            {plural(suite.guard_cases, "guard")} · saved {relativeTime(suite.created_at, now)} from{" "}
            {suite.source.run_id ? (
              <Link href={`/simulations/${suite.source.run_id}`} className="font-medium text-primary hover:underline">
                {suite.source.run_name ?? "a run"}
              </Link>
            ) : (
              suite.source.run_name ?? "a run"
            )}
            {suite.source.bot_endpoint ? ` · ${botHost(suite.source.bot_endpoint)}` : ""}
          </p>
          <p className="text-xs text-muted-foreground">
            Judged with {plural(suite.criteria, "criterion", "criteria")} and {plural(suite.guardrails, "guardrail")} from that run.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild size="sm">
            <Link href={`/new?type=regression&suite=${encodeURIComponent(suite.id)}`}>
              <Play /> Replay
            </Link>
          </Button>
          {confirming ? (
            <>
              <Button size="sm" variant="destructive" onClick={remove}>
                Delete suite
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Keep
              </Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} aria-label={`Delete ${suite.name}`}>
              <Trash2 />
            </Button>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {runs.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="sr-only">Replays of {suite.name}, newest first</caption>
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">Replay</th>
                <th scope="col" className="px-3 py-2 font-medium">Bot</th>
                <th scope="col" className="py-2 pl-3 text-right font-medium">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td className="py-2 pr-3">
                    <Link href={`/simulations/${r.run_id}`} className="font-medium text-primary hover:underline">
                      {relativeTime(r.at, now)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {botHost(r.bot_endpoint)}
                    {r.bot_build && <span className="font-mono text-xs"> · build {r.bot_build.slice(0, 12)}</span>}
                  </td>
                  <td className="py-2 pl-3 text-right">
                    <SuiteVerdict run={r} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Not replayed yet. Replay it on the next build to see what was fixed.</p>
      )}
    </li>
  );
}

export function SuitesPage() {
  const { data: suites, isPending, isError, error } = useSuites();
  const now = useNow();
  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-8">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Regression suites</h2>
        <p className="text-sm text-muted-foreground">
          Failures saved from earlier runs. Replay a suite on each new build to see what was fixed, what still fails and
          what broke. Save one from any finished run&apos;s report.
        </p>
      </div>
      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load suites."}
        </p>
      )}
      {isPending ? (
        <LoadingBlocks />
      ) : suites?.length ? (
        <ul className="space-y-4">
          {suites.map((s) => (
            <SuiteCard key={s.id} suite={s} now={now} />
          ))}
        </ul>
      ) : (
        <div className="rounded-xl border bg-card p-6 text-sm text-muted-foreground">
          No suites yet. Open a finished run and choose <span className="font-medium text-foreground">Save as regression suite</span>.{" "}
          <Link href="/runs" className="font-medium text-primary hover:underline">
            Go to runs
          </Link>
        </div>
      )}
    </div>
  );
}
