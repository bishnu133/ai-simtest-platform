"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowUpDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDuration, pct, scoreTone } from "@/lib/engine/report";
import { botHost, isActive, needsReview, relativeTime, runDuration, stuckShare } from "@/lib/engine/runs";
import type { SimulationStatus } from "@/lib/engine/types";
import { StatusBadge } from "./status-badge";

/** Current time, refreshed every minute, for "3 hours ago" labels. */
export function useNow(intervalMs = 60000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

type Filter = "all" | "active" | "review" | "completed" | "failed";
type SortKey = "started" | "pass" | "stuck";

const FILTERS: { key: Filter; label: string; match: (r: SimulationStatus) => boolean }[] = [
  { key: "all", label: "All", match: () => true },
  { key: "active", label: "Running", match: (r) => isActive(r) && !needsReview(r) },
  { key: "review", label: "Needs review", match: needsReview },
  { key: "completed", label: "Completed", match: (r) => r.status === "completed" },
  { key: "failed", label: "Failed or stopped", match: (r) => ["failed", "aborted", "cancelled"].includes(r.status) },
];

export function RunsTable({
  runs,
  compact = false,
  limit,
}: {
  runs: SimulationStatus[];
  /** Home-page variant: no filters, fewer columns */
  compact?: boolean;
  limit?: number;
}) {
  const router = useRouter();
  const now = useNow();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "started", asc: false });

  const counts = useMemo(
    () => Object.fromEntries(FILTERS.map((f) => [f.key, runs.filter(f.match).length])) as Record<Filter, number>,
    [runs],
  );

  const q = query.trim().toLowerCase();
  const visible = runs
    .filter(FILTERS.find((f) => f.key === filter)!.match)
    .filter((r) => !q || r.name.toLowerCase().includes(q) || r.config.bot_endpoint.toLowerCase().includes(q) || (r.bot_build ?? "").includes(q))
    .sort((a, b) => {
      const dir = sort.asc ? 1 : -1;
      if (sort.key === "pass") return ((a.summary?.pass_rate ?? -1) - (b.summary?.pass_rate ?? -1)) * dir;
      if (sort.key === "stuck") return ((stuckShare(a) ?? -1) - (stuckShare(b) ?? -1)) * dir;
      return (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()) * dir;
    })
    .slice(0, limit);

  const sortHeader = (key: SortKey, label: string, className = "") => (
    <TableHead className={className} aria-sort={sort.key === key ? (sort.asc ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        className="inline-flex items-center gap-1 font-semibold text-foreground"
        onClick={() => setSort((s) => ({ key, asc: s.key === key ? !s.asc : false }))}
      >
        {label}
        <ArrowUpDown className={`h-3.5 w-3.5 ${sort.key === key ? "text-foreground" : "text-muted-foreground"}`} aria-hidden />
      </button>
    </TableHead>
  );

  return (
    <div className="space-y-3">
      {!compact && (
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
            {FILTERS.map((f) => (
              <Button
                key={f.key}
                size="xs"
                variant={filter === f.key ? "default" : "outline"}
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
              >
                {f.label} <span className="tabular-nums opacity-75">{counts[f.key]}</span>
              </Button>
            ))}
          </div>
          <div className="relative w-full md:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search name, bot or build…"
              className="h-9 pl-9"
              aria-label="Search runs"
            />
          </div>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border bg-card shadow-xs">
        <Table className={compact ? "" : "min-w-[860px]"}>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="font-semibold text-foreground">Run</TableHead>
              <TableHead className="font-semibold text-foreground">Status</TableHead>
              {sortHeader("pass", "Pass rate", "text-right")}
              {sortHeader("stuck", "Stuck", "text-right")}
              {!compact && <TableHead className="text-right font-semibold text-foreground">Conversations</TableHead>}
              {!compact && <TableHead className="text-right font-semibold text-foreground">Critical</TableHead>}
              {sortHeader("started", "Started")}
              {!compact && <TableHead className="text-right font-semibold text-foreground">Duration</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.length === 0 ? (
              <TableRow>
                <TableCell colSpan={compact ? 5 : 8} className="py-10 text-center text-sm text-muted-foreground">
                  {runs.length ? "No runs match these filters." : "No runs yet."}
                </TableCell>
              </TableRow>
            ) : (
              visible.map((run) => {
                const s = run.summary;
                const stuck = stuckShare(run);
                const href = `/simulations/${run.simulation_id}`;
                return (
                  <TableRow
                    key={run.simulation_id}
                    className="cursor-pointer"
                    onClick={() => router.push(href)}
                  >
                    <TableCell className="max-w-[320px]">
                      <Link href={href} className="font-medium text-foreground hover:text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
                        {run.name}
                      </Link>
                      <div className="truncate text-xs text-muted-foreground">
                        {botHost(run.config.bot_endpoint)}
                        {run.bot_build && <span className="font-mono"> · build {run.bot_build.slice(0, 12)}</span>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={run.status} />
                      {needsReview(run) && run.pending_gate && (
                        <div className="mt-0.5 text-xs text-muted-foreground">{run.pending_gate.title}</div>
                      )}
                    </TableCell>
                    <TableCell className={`text-right font-semibold tabular-nums ${s?.pass_rate != null ? scoreTone(s.pass_rate) : "text-muted-foreground"}`}>
                      {s?.pass_rate != null ? pct(s.pass_rate) : "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {stuck != null ? `${s?.stuck_conversations} (${pct(stuck)})` : "—"}
                    </TableCell>
                    {!compact && (
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {s?.total_conversations ?? run.config.num_personas}
                      </TableCell>
                    )}
                    {!compact && (
                      <TableCell className={`text-right tabular-nums ${s?.critical_failures ? "font-semibold text-fail" : "text-muted-foreground"}`}>
                        {s?.critical_failures ?? "—"}
                      </TableCell>
                    )}
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground" title={new Date(run.created_at).toLocaleString()}>
                      {relativeTime(run.created_at, now)}
                    </TableCell>
                    {!compact && (
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatDuration(runDuration(run))}
                      </TableCell>
                    )}
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
