"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { engine } from "@/lib/engine/client";
import { judgeLabel, pct } from "@/lib/engine/report";
import type { SimulationReport } from "@/lib/engine/types";

/**
 * How far to trust this run's verdicts: borderline replies that were judged
 * again (and how many came out differently), and how often these judges agree
 * with your team's labels on the Judge calibration page.
 */
export function JudgeTrust({ report }: { report: SimulationReport }) {
  const { data: calibration } = useQuery({ queryKey: ["calibration"], queryFn: engine.getCalibration, retry: false });
  const turns = (report.judged_conversations ?? []).flatMap((jc) => jc.judged_turns ?? []);
  const rechecked = turns.filter((t) => t.recheck);
  const unsure = rechecked.filter((t) => t.recheck && !t.recheck.stable).length;
  const ran = new Set(Object.keys(report.score_by_judge ?? {}));
  const measured = (calibration?.judges ?? []).filter(
    (j) => ran.has(j.judge) && j.labelled >= (calibration?.min_labels ?? 5) && j.agreement_rate != null,
  );
  const labelled = measured.reduce((n, j) => n + j.labelled, 0);
  const agree = measured.reduce((n, j) => n + j.agree, 0);
  const overall = labelled ? agree / labelled : null;
  if (!turns.length) return null;

  return (
    <section aria-labelledby="trust-heading" className="rounded-xl border bg-card p-5 shadow-xs">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Scale className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1 space-y-3">
          <h3 id="trust-heading" className="font-semibold text-foreground">
            Can you trust these verdicts?
          </h3>
          <dl className="grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">Borderline replies judged again</dt>
              <dd className="text-sm text-foreground">
                {rechecked.length === 0 ? (
                  <span className="text-muted-foreground">None: every verdict was clear of the pass mark, or re-checks were off.</span>
                ) : (
                  <>
                    <span className="font-semibold tabular-nums">{rechecked.length}</span> of {turns.length} replies were close to the
                    pass mark and judged three times.{" "}
                    {unsure ? (
                      <span className="font-medium text-warn">
                        {unsure} came out differently and {unsure === 1 ? "is" : "are"} marked unsure.
                      </span>
                    ) : (
                      <span className="text-pass">All came out the same.</span>
                    )}
                  </>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Agreement with your team</dt>
              <dd className="text-sm text-foreground">
                {overall == null ? (
                  <>
                    <span className="text-muted-foreground">Not measured yet. </span>
                    <Link href="/calibration" className="font-medium text-primary hover:underline">
                      Label a few replies
                    </Link>{" "}
                    <span className="text-muted-foreground">to see how often these judges agree with people.</span>
                  </>
                ) : (
                  <>
                    <span className="font-semibold tabular-nums">{pct(overall)}</span> of {labelled} labelled replies
                    <span className="text-muted-foreground">
                      {" "}
                      ({measured.map((j) => `${judgeLabel(j.judge)} ${pct(j.agreement_rate)}`).join(" · ")})
                    </span>
                    .{" "}
                    <Link href="/calibration" className="font-medium text-primary hover:underline">
                      Details
                    </Link>
                  </>
                )}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>
  );
}
