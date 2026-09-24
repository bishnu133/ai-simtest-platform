"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { engine, EngineError } from "@/lib/engine/client";
import { humanize, STATUS_STYLES } from "@/lib/engine/stages";
import type { SimulationStatus } from "@/lib/engine/types";

export function RunsList() {
  const [runs, setRuns] = useState<SimulationStatus[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    const load = () =>
      engine
        .listSimulations()
        .then((r) => {
          if (!stopped) {
            setRuns(r.simulations);
            setError("");
          }
        })
        .catch((err) => !stopped && setError(err instanceof EngineError ? err.message : "Could not load runs"));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="py-6 space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b pb-6">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Simulation Runs</h1>
          <p className="text-muted-foreground">Runs started from this dashboard since the engine last restarted.</p>
        </div>
        <Button asChild>
          <Link href="/">
            <Plus /> New Test
          </Link>
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      )}

      {!runs && !error ? (
        <div className="flex justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : runs && runs.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-card p-12 text-center text-muted-foreground">
          No runs yet. <Link href="/" className="text-primary font-medium hover:underline">Start your first simulation</Link>.
        </div>
      ) : runs ? (
        <div className="rounded-md border bg-card overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/50">
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Step</TableHead>
                <TableHead>Personas</TableHead>
                <TableHead>Started</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow key={run.simulation_id}>
                  <TableCell>
                    <Link href={`/simulations/${run.simulation_id}`} className="font-medium text-primary hover:underline">
                      {run.name}
                    </Link>
                    <div className="text-xs text-muted-foreground font-mono">{run.config.bot_endpoint}</div>
                  </TableCell>
                  <TableCell>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLES[run.status] ?? ""}`}>
                      {humanize(run.status)}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {run.pending_gate ? `Review: ${run.pending_gate.title}` : humanize(run.stage)}
                  </TableCell>
                  <TableCell className="tabular-nums">{run.config.num_personas}</TableCell>
                  <TableCell className="text-muted-foreground">{new Date(run.created_at).toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
