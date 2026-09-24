"use client";

import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  formatDuration,
  formatMs,
  judgeLabel,
  pct,
  plural,
  releaseVerdict,
  responsesJudged,
  turnLabelCounts,
} from "@/lib/engine/report";
import type { ReportResponse } from "@/lib/engine/types";
import { ScoreBars, TurnLabelBar } from "./charts";
import { EmptyNote, Panel, SeverityBadge, StatTile, VerdictBanner } from "./parts";

export function OverviewTab({ data, onGoToFailures }: { data: ReportResponse; onGoToFailures: () => void }) {
  const { report, analysis = {} } = data;
  const summary = report.summary ?? {};
  const verdict = releaseVerdict(report, analysis.quality_gates_failed);
  const trend = analysis.trend?.available && analysis.trend.comparable ? analysis.trend : undefined;
  const delta = (label: string) => trend?.deltas.find((d) => d.label.toLowerCase() === label.toLowerCase());
  const latency = analysis.latency?.available ? analysis.latency : undefined;
  // No priced model calls means the cost is unknown, not zero
  const cost = analysis.cost?.total_calls ? analysis.cost.total_estimated_cost_usd : undefined;
  const judges = Object.entries(report.score_by_judge ?? {}).sort((a, b) => a[1] - b[1]);
  const personaTypes = Object.entries(report.score_by_persona_type ?? {}).sort((a, b) => a[1] - b[1]);
  const fixFirst = analysis.fix_first ?? [];

  return (
    <div className="space-y-6">
      <VerdictBanner verdict={verdict} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Pass rate" value={pct(summary.pass_rate)} delta={delta("pass rate")} sub="of judged turns" />
        <StatTile label="Average score" value={pct(summary.average_score)} delta={delta("average score")} sub="across all judges" />
        <StatTile
          label="Failed responses"
          value={summary.critical_failures ?? 0}
          delta={delta("critical failures")}
          sub={`${summary.warnings ?? 0} more flagged as warnings`}
        />
        <StatTile
          label="Conversations"
          value={summary.total_conversations ?? 0}
          sub={`${plural(summary.total_personas ?? 0, "persona")} · ${responsesJudged(report)} turns judged`}
        />
        <StatTile
          label="Latency p50 / p95"
          value={latency ? `${formatMs(latency.p50)} / ${formatMs(latency.p95)}` : "—"}
          sub={latency ? `max ${formatMs(latency.max)} over ${latency.count} replies` : "no latency recorded"}
        />
        <StatTile label="Run duration" value={formatDuration(summary.execution_time_seconds)} sub="end-to-end simulation" />
        {analysis.coverage?.grade && (
          <StatTile
            label="Test coverage"
            value={`${analysis.coverage.grade} · ${pct(analysis.coverage.overall_coverage)}`}
            sub={`${plural(analysis.coverage.gaps?.length ?? 0, "gap")} identified`}
          />
        )}
        {cost !== undefined && (
          <StatTile
            label="Estimated LLM cost"
            value={`$${cost.toFixed(cost < 1 ? 3 : 2)}`}
            sub={analysis.cost?.total_calls ? `${analysis.cost.total_calls} model calls` : undefined}
          />
        )}
      </div>

      {analysis.trend?.available && !analysis.trend.comparable && (
        <p className="text-xs text-muted-foreground">
          Not compared with the previous run: {analysis.trend.incomparable_reason || "its configuration differs."}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Judge scores" description="Average score per judge, weakest first.">
          {judges.length ? (
            <ScoreBars caption="Judge scores" target={0.8} rows={judges.map(([k, v]) => ({ label: judgeLabel(k), value: v }))} />
          ) : (
            <EmptyNote>No judge scores were reported.</EmptyNote>
          )}
        </Panel>
        <div className="grid gap-4">
          <Panel title="Turn outcomes" description={`Every judged bot reply, by overall label.`}>
            <TurnLabelBar counts={turnLabelCounts(report)} />
          </Panel>
          <Panel title="Score by persona type">
            {personaTypes.length ? (
              <ScoreBars caption="Score by persona type" rows={personaTypes.map(([k, v]) => ({ label: judgeLabel(k), value: v }))} />
            ) : (
              <EmptyNote>No persona breakdown available.</EmptyNote>
            )}
          </Panel>
        </div>
      </div>

      <Panel
        title="Fix these first"
        description="Highest-impact failures, ranked by severity, frequency and how many conversations they reach."
        action={
          <Button variant="ghost" size="sm" onClick={onGoToFailures}>
            All failures <ArrowRight />
          </Button>
        }
      >
        {fixFirst.length ? (
          <ol className="divide-y">
            {fixFirst.slice(0, 3).map((item) => (
              <li key={item.rank} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold tabular-nums">
                  {item.rank}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-foreground">{item.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {plural(item.frequency, "occurrence")} · reaches {pct(item.reach)} of conversations
                  </div>
                </div>
                <SeverityBadge severity={item.severity} />
              </li>
            ))}
          </ol>
        ) : (
          <EmptyNote>No recurring failures to fix.</EmptyNote>
        )}
      </Panel>

      {(analysis.coverage || analysis.workflows?.length) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {analysis.coverage && (
            <Panel
              title="Test coverage"
              description={
                analysis.coverage.capped_reason ? `Grade capped: ${analysis.coverage.capped_reason}` : "How much of the bot's surface this run exercised."
              }
            >
              <ScoreBars
                caption="Coverage by dimension"
                rows={Object.entries(analysis.coverage.dimension_scores ?? {}).map(([k, v]) => ({ label: judgeLabel(k), value: v }))}
              />
              {!!analysis.coverage.gaps?.length && (
                <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {analysis.coverage.gaps.slice(0, 4).map((g, i) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              )}
            </Panel>
          )}
          {!!analysis.workflows?.length && (
            <Panel title="Workflows" description="Business workflows the bot was graded on.">
              <ul className="divide-y">
                {analysis.workflows.map((wf, i) => {
                  const total = wf.total_conversations ?? 0;
                  const scope = wf.scope_check_message !== undefined;
                  return (
                    <li key={i} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-foreground">{wf.workflow}</div>
                        <div className="text-xs text-muted-foreground">
                          {scope ? "Scope check" : wf.role_label ?? wf.domain} ·{" "}
                          {scope
                            ? wf.scope_check_message
                            : `${wf.passed ?? 0}/${total} passed · avg ${pct(wf.avg_score)}`}
                        </div>
                      </div>
                      {!scope && (
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                            total && (wf.passed ?? 0) === total ? "bg-pass/10 text-pass" : "bg-fail/10 text-fail"
                          }`}
                        >
                          {total ? pct((wf.passed ?? 0) / total) : "n/a"}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}
