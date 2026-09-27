"use client";

import Link from "next/link";
import { useState } from "react";
import { GitCompareArrows, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRuns } from "@/lib/engine/queries";
import { RunsTable } from "./runs-table";

export function RunsPage() {
  const { data: runs, isPending, isError, error } = useRuns();
  const [selected, setSelected] = useState<string[]>([]);
  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length < 2 ? [...cur, id] : cur));
  // Older run first: "before" and "after"
  const pair = (runs ?? [])
    .filter((r) => selected.includes(r.simulation_id))
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Runs</h2>
          <p className="text-sm text-muted-foreground">
            Every test started from this workspace. Finished runs are kept when the engine restarts.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {pair.length === 2 ? (
            <Button asChild variant="outline">
              <Link href={`/runs/compare?before=${pair[0].simulation_id}&after=${pair[1].simulation_id}`}>
                <GitCompareArrows /> Compare these 2 runs
              </Link>
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">Tick two finished runs to compare them</span>
          )}
          <Button asChild>
            <Link href="/new">
              <Plus /> New test
            </Link>
          </Button>
        </div>
      </div>
      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load runs."}
        </p>
      )}
      {isPending ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
        </div>
      ) : (
        <RunsTable runs={runs ?? []} selected={selected} onToggleSelect={toggle} />
      )}
    </div>
  );
}
