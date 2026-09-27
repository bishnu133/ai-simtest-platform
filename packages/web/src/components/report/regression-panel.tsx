"use client";

import Link from "next/link";
import { CheckCircle2, CircleAlert, CircleMinus, TrendingUp, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { STATUS_LABELS, SuiteVerdict } from "@/components/setup/suite-verdict";
import { plural } from "@/lib/engine/report";
import type { CaseStatus, RegressionResult, ReplyVerdict } from "@/lib/engine/types";
import { Panel } from "./parts";

const STATUS_ICON: Record<CaseStatus, { Icon: typeof CheckCircle2; tone: string }> = {
  regressed: { Icon: XCircle, tone: "text-fail" },
  still_failing: { Icon: XCircle, tone: "text-fail" },
  partly_fixed: { Icon: TrendingUp, tone: "text-warn" },
  error: { Icon: CircleAlert, tone: "text-warn" },
  fixed: { Icon: CheckCircle2, tone: "text-pass" },
  still_passing: { Icon: CheckCircle2, tone: "text-pass" },
};

const ORDER: CaseStatus[] = ["regressed", "still_failing", "partly_fixed", "error", "fixed", "still_passing"];

function Status({ status }: { status: CaseStatus }) {
  const { Icon, tone } = STATUS_ICON[status];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${tone}`}>
      <Icon className="h-3.5 w-3.5" aria-hidden /> {STATUS_LABELS[status]}
    </span>
  );
}

function Reply({ title, v }: { title: string; v: ReplyVerdict | null }) {
  const label = (v?.label ?? "").toUpperCase();
  const tone = label === "FAIL" ? "text-fail" : label === "PASS" ? "text-pass" : "text-warn";
  return (
    <div className="min-w-0 rounded-md border bg-background/60 p-2.5">
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <span>{title}</span>
        {v ? <span className={tone}>{label}</span> : <span>No reply</span>}
      </div>
      {v && <p className="line-clamp-4 whitespace-pre-wrap text-sm text-foreground">{v.reply}</p>}
      {v?.issue && <p className="mt-1 text-xs text-fail">{v.issue}</p>}
    </div>
  );
}

export function RegressionPanel({
  result,
  onOpen,
}: {
  result: RegressionResult;
  onOpen: (conversationId: string) => void;
}) {
  const attention = result.cases.filter((c) => c.changes.length || ["regressed", "still_failing", "error"].includes(c.status));
  const quiet = result.cases.length - attention.length;
  return (
    <Panel
      title="Regression suite"
      description={
        <>
          Replayed <span className="font-medium text-foreground">{result.suite_name}</span>
          {result.source.run_name ? (
            <>
              {" "}(saved from{" "}
              {result.source.run_id ? (
                <Link href={`/simulations/${result.source.run_id}`} className="text-primary hover:underline">
                  {result.source.run_name}
                </Link>
              ) : (
                result.source.run_name
              )}
              )
            </>
          ) : null}
          : the same customer messages, judged the way that run was.
        </>
      }
      action={<SuiteVerdict run={result} />}
    >
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label="Cases by outcome">
        {ORDER.map((s) => (
          <li key={s} className="rounded-lg border p-3">
            <Status status={s} />
            <div className="mt-1 text-2xl font-bold tabular-nums text-foreground">{result.counts[s] ?? 0}</div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm text-muted-foreground">
        {plural(result.replies_fixed, "reply", "replies")} that failed now pass; {plural(result.replies_broken, "reply", "replies")}{" "}
        that passed now fail, across {plural(result.total_cases, "case")}.
      </p>

      <div className="mt-5 space-y-3">
        {attention.map((c) => (
          <details key={c.id} className="group rounded-lg border" open={c.status === "regressed"}>
            <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 p-3">
              <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                <Status status={c.status} />
                <span className="font-medium text-foreground">{c.persona || c.id}</span>
                <span className="text-xs text-muted-foreground">
                  failing replies {c.failing_before} → {c.failing_now}
                  {c.error ? ` · ${c.error}` : ""}
                </span>
              </span>
              <Button
                variant="ghost"
                size="xs"
                onClick={(e) => {
                  e.preventDefault();
                  onOpen(c.conversation_id);
                }}
              >
                View replay
              </Button>
            </summary>
            {c.changes.length > 0 && (
              <ol className="space-y-3 border-t p-3">
                {c.changes.map((ch) => (
                  <li key={ch.index} className="space-y-2">
                    <p className="text-sm">
                      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Message {ch.index}
                      </span>{" "}
                      <span className="text-muted-foreground">{ch.customer}</span>
                    </p>
                    <div className="grid gap-2 md:grid-cols-2">
                      <Reply title="Before" v={ch.before} />
                      <Reply title="Now" v={ch.after} />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </details>
        ))}
        {quiet > 0 && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CircleMinus className="h-3.5 w-3.5" aria-hidden /> {plural(quiet, "more case")} with no reply that changed.
          </p>
        )}
      </div>
    </Panel>
  );
}
