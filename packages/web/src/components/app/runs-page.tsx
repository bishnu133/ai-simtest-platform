"use client";

import Link from "next/link";
import { Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRuns } from "@/lib/engine/queries";
import { RunsTable } from "./runs-table";

export function RunsPage() {
  const { data: runs, isPending, isError, error } = useRuns();
  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Runs</h2>
          <p className="text-sm text-muted-foreground">
            Every test started from this workspace. Finished runs are kept when the engine restarts.
          </p>
        </div>
        <Button asChild>
          <Link href="/new">
            <Plus /> New test
          </Link>
        </Button>
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
        <RunsTable runs={runs ?? []} />
      )}
    </div>
  );
}
