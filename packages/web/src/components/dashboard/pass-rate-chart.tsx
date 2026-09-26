"use client";

import Link from "next/link";
import { pct } from "@/lib/engine/report";
import { stuckShare } from "@/lib/engine/runs";
import type { SimulationStatus } from "@/lib/engine/types";

const TARGET = 0.8;
const TICKS = [1, 0.5, 0];

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Pass rate of each completed run, oldest to newest: one series, so no legend
 * (the panel title names it). Columns anchor to the baseline with 4px rounded
 * tops and a gap between them; the 80% target is a recessive rule. Hover or
 * focus shows the run; a table carries the same data for screen readers.
 */
export function PassRateByRunChart({ runs }: { runs: SimulationStatus[] }) {
  return (
    <figure aria-label="Pass rate by run" className="space-y-2">
      <div className="grid grid-cols-[2.5rem_1fr] gap-2">
        <div className="relative h-48 text-right text-[11px] tabular-nums text-muted-foreground" aria-hidden>
          {TICKS.map((t) => (
            <span key={t} className="absolute right-0 -translate-y-1/2" style={{ top: `${(1 - t) * 100}%` }}>
              {pct(t)}
            </span>
          ))}
        </div>
        <div className="relative h-48">
          {TICKS.map((t) => (
            <div key={t} className="absolute inset-x-0 border-t border-border" style={{ top: `${(1 - t) * 100}%` }} aria-hidden />
          ))}
          <div
            className="absolute inset-x-0 border-t border-dashed border-foreground/40"
            style={{ top: `${(1 - TARGET) * 100}%` }}
            aria-hidden
          />
          <div className="absolute inset-0 flex items-end gap-[2px] px-1">
            {runs.map((run) => {
              const value = run.summary?.pass_rate ?? 0;
              const stuck = stuckShare(run);
              return (
                <Link
                  key={run.simulation_id}
                  href={`/simulations/${run.simulation_id}`}
                  className="group relative flex h-full min-w-0 flex-1 items-end justify-center outline-none"
                  aria-label={`${run.name}: pass rate ${pct(value)}`}
                >
                  <span
                    className="w-full max-w-10 rounded-t-[4px] bg-primary transition-[filter] group-hover:brightness-110 group-focus-visible:ring-2 group-focus-visible:ring-ring"
                    style={{ height: `${Math.max(value, 0.01) * 100}%` }}
                  />
                  <span
                    role="tooltip"
                    className="pointer-events-none absolute bottom-full z-10 mb-2 hidden w-56 rounded-md border bg-popover p-2.5 text-xs text-popover-foreground shadow-md group-hover:block group-focus-visible:block"
                  >
                    <span className="block truncate font-semibold">{run.name}</span>
                    <span className="block text-muted-foreground">{new Date(run.created_at).toLocaleString()}</span>
                    <span className="mt-1.5 grid grid-cols-2 gap-x-2 gap-y-0.5">
                      <span className="text-muted-foreground">Pass rate</span>
                      <span className="text-right font-semibold tabular-nums">{pct(value)}</span>
                      <span className="text-muted-foreground">Stuck</span>
                      <span className="text-right tabular-nums">{stuck != null ? pct(stuck) : "—"}</span>
                      <span className="text-muted-foreground">Conversations</span>
                      <span className="text-right tabular-nums">{run.summary?.total_conversations ?? "—"}</span>
                      {run.bot_build && (
                        <>
                          <span className="text-muted-foreground">Build</span>
                          <span className="truncate text-right font-mono">{run.bot_build.slice(0, 12)}</span>
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
      <div className="grid grid-cols-[2.5rem_1fr] gap-2" aria-hidden>
        <span />
        <div className="flex gap-[2px] px-1 text-[11px] text-muted-foreground">
          {runs.map((run, i) => (
            <span key={run.simulation_id} className="min-w-0 flex-1 truncate text-center">
              {runs.length <= 8 || i % Math.ceil(runs.length / 6) === 0 || i === runs.length - 1 ? shortDate(run.created_at) : ""}
            </span>
          ))}
        </div>
      </div>
      <figcaption className="flex items-center gap-2 pl-12 text-xs text-muted-foreground">
        <span className="inline-block w-5 border-t border-dashed border-foreground/40" aria-hidden /> Target {pct(TARGET)}
      </figcaption>
      <table className="sr-only">
        <caption>Pass rate by run, oldest first</caption>
        <thead>
          <tr>
            <th scope="col">Run</th>
            <th scope="col">Date</th>
            <th scope="col">Pass rate</th>
            <th scope="col">Stuck conversations</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.simulation_id}>
              <th scope="row">{run.name}</th>
              <td>{new Date(run.created_at).toLocaleString()}</td>
              <td>{pct(run.summary?.pass_rate ?? 0)}</td>
              <td>{stuckShare(run) != null ? pct(stuckShare(run)) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
