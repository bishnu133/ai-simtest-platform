"use client";

import { useState } from "react";
import { ChevronDown, CircleAlert, MessageSquareText, TriangleAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { pct, plural } from "@/lib/engine/report";
import type { RagEvalResult, RagMetricSummary } from "@/lib/engine/types";
import { EmptyNote, Panel } from "./parts";

/** Plain names and what each metric checks, as shown to a tester. */
export const RAG_METRICS: Record<string, { label: string; what: string }> = {
  faithfulness: { label: "Backed by sources", what: "Every figure in the reply appears in what the bot retrieved or its tools returned." },
  citation_accuracy: { label: "Citations hold up", what: "Each cited source was retrieved and says what it is cited for." },
  attribution_completeness: { label: "Cites consistently", what: "When the bot cites, every factual sentence carries a citation." },
  source_coverage: { label: "Uses its sources", what: "The retrieved sources show up in the reply." },
  context_utilization: { label: "Draws on retrieval", what: "The reply draws on at least one retrieved source." },
  context_relevance: { label: "Retrieved the right thing", what: "At least one retrieved source is about the question." },
  answer_relevance: { label: "Answers the question", what: "The reply engages with what was asked (or the tools it called do)." },
  temporal_awareness: { label: "Dates qualified", what: "Dated information is qualified (\"as of\"), not stated as current." },
  conflicting_evidence: { label: "Handles conflicts", what: "When sources disagree or add conditions, the reply says so." },
  hallucination: { label: "No invented content", what: "An AI reading of unsupported statements." },
  noise_robustness: { label: "Ignores noise", what: "An AI reading of whether irrelevant sources misled the reply." },
  multi_hop_reasoning: { label: "Combines sources", what: "An AI reading of answers that need several sources." },
  parameter_accuracy: { label: "Right parameters", what: "Tools were called with their required parameters." },
  sequence_correctness: { label: "Right order", what: "Tools were called in the order they depend on." },
  tool_selection: { label: "Known tools", what: "The tools called exist in your definitions." },
  result_integration: { label: "Uses tool results", what: "Figures a tool returned reach the reply." },
  error_handling: { label: "Explains tool errors", what: "A failed tool call is acknowledged, not papered over." },
  retry_behavior: { label: "Recovers from blips", what: "A transient failure is retried or the customer is told what to do next." },
  timeout_handling: { label: "Handles timeouts", what: "A tool timeout that was not recovered is acknowledged." },
  unnecessary_calls: { label: "No duplicate calls", what: "The same successful call is not repeated." },
  parallel_execution: { label: "Parallel where possible", what: "Independent tool calls are not needlessly serialised." },
  permission_respect: { label: "Checks before acting", what: "A restricted tool is run on a record the customer named only after a check." },
  side_effect_awareness: { label: "Confirms big changes", what: "Sweeping changes (\"move all my money\") are confirmed first." },
  missing_calls: { label: "No missing calls", what: "An AI reading of tools that should have been called." },
  fallback_behavior: { label: "Degrades gracefully", what: "An AI reading of how the bot copes when tools fail." },
};

const metricLabel = (m: string) => RAG_METRICS[m]?.label ?? m.replace(/_/g, " ");

function PassRateChart({ metrics }: { metrics: RagMetricSummary[] }) {
  const rows = metrics.filter((m) => m.pass_rate !== null);
  if (!rows.length) return <EmptyNote>No metric had anything to check on these replies.</EmptyNote>;
  return (
    <figure aria-label="Pass rate per metric, weakest first" className="space-y-1.5">
      {rows.map((m) => {
        const rate = m.pass_rate ?? 0;
        return (
          <div
            key={m.metric}
            className="group grid grid-cols-[minmax(0,1fr)_6rem] items-center gap-x-3 sm:grid-cols-[minmax(8rem,13rem)_minmax(0,1fr)_7rem]"
          >
            <span className="col-span-2 truncate text-sm text-foreground sm:col-span-1" title={RAG_METRICS[m.metric]?.what}>
              {metricLabel(m.metric)}
              <span className="ml-1.5 text-[11px] text-muted-foreground">{m.kind === "tool" ? "tool" : ""}</span>
            </span>
            <div className="relative h-5 rounded bg-muted">
              <div
                className={`absolute inset-y-0 left-0 rounded ${rate >= 0.8 ? "bg-primary" : rate >= 0.5 ? "bg-warn" : "bg-fail"}`}
                style={{ width: `${Math.max(rate * 100, 1.5)}%` }}
                aria-hidden
              />
              <div
                role="tooltip"
                className="pointer-events-none absolute -top-2 left-2 z-10 hidden max-w-[22rem] -translate-y-full rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block"
              >
                <span className="font-medium">{metricLabel(m.metric)}</span>: {RAG_METRICS[m.metric]?.what ?? ""}
                {m.example_issue ? <span className="mt-1 block text-muted-foreground">e.g. {m.example_issue}</span> : null}
              </div>
            </div>
            <span className="text-right text-xs tabular-nums text-muted-foreground">
              <span className="text-sm font-semibold text-foreground">{pct(rate)}</span> · {m.passed}/{m.checked}
            </span>
          </div>
        );
      })}
      <figcaption className="pt-1 text-xs text-muted-foreground">
        Share of the replies each metric could check that passed. A metric with nothing to check on a reply (no citation,
        no tool call) leaves it out, so each counts its own replies.
      </figcaption>
      <div className="sr-only">
        <table>
          <caption>Pass rate per metric</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">Passed</th>
              <th scope="col">Checked</th>
              <th scope="col">Pass rate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.metric}>
                <td>{metricLabel(m.metric)}</td>
                <td>{m.passed}</td>
                <td>{m.checked}</td>
                <td>{pct(m.pass_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums text-foreground">{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

export function RagPanel({
  result,
  onOpen,
}: {
  result: RagEvalResult | { error: string };
  onOpen: (conversationId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if ("error" in result) {
    return (
      <Panel title="Retrieval and tools" description="RAG and tool evaluation did not finish; the rest of the run is unaffected.">
        <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {result.error}
        </p>
      </Panel>
    );
  }
  const r = result;
  const failing = open ? r.failing_replies : r.failing_replies.slice(0, 5);
  return (
    <Panel
      title="Retrieval and tools"
      description={`Every bot reply checked against what it retrieved and the tools it called${r.used_llm ? ", with an AI reading" : ", by rules"}.`}
    >
      <div className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-3">
          <Tile label="Replies checked" value={r.replies.toLocaleString()} sub={`${plural(r.failing_total, "reply", "replies")} with a failed check`} />
          <Tile
            label="Returned sources"
            value={r.replies_with_sources.toLocaleString()}
            sub={r.rag_score === null ? "no retrieval checks scored" : `retrieval checks average ${pct(r.rag_score)}`}
          />
          <Tile
            label="Called tools"
            value={r.replies_with_tools.toLocaleString()}
            sub={
              r.tool_score === null
                ? "no tool checks scored"
                : `tool checks average ${pct(r.tool_score)}${r.tool_definitions ? ` · ${plural(r.tool_definitions, "tool")} defined` : ""}`
            }
          />
        </div>

        {!r.evidence_captured && (
          <p className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-foreground">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
            <span>
              Your bot returned no sources or tool calls beside its replies, so replies were checked against your
              documentation and no tool check was scored. Return <code className="font-mono text-xs">sources</code> or{" "}
              <code className="font-mono text-xs">tool_calls</code> in the bot&apos;s JSON response to check retrieval itself.
            </span>
          </p>
        )}

        <PassRateChart metrics={r.metrics} />

        {r.failing_replies.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-semibold text-foreground">
              Replies that failed a check ({r.failing_total}), most failures first
            </h4>
            <ul className="divide-y rounded-lg border">
              {failing.map((f) => (
                <li key={`${f.conversation_id}:${f.turn_index}`} className="space-y-2 p-3">
                  <div className="space-y-1 text-sm">
                    {f.user_message && (
                      <p className="line-clamp-2 text-muted-foreground">
                        <span className="font-medium text-foreground">Customer: </span>
                        {f.user_message}
                      </p>
                    )}
                    <p className="line-clamp-3 text-foreground/90">
                      <span className="font-medium text-foreground">Bot: </span>
                      {f.bot_reply || <em className="text-muted-foreground">(empty reply)</em>}
                    </p>
                  </div>
                  <ul className="space-y-1">
                    {f.failed.map((x) => (
                      <li key={x.metric} className="flex items-start gap-1.5 text-xs">
                        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fail" aria-hidden />
                        <span>
                          <span className="font-semibold text-foreground">{metricLabel(x.metric)}</span>
                          <span className="text-muted-foreground"> — {x.issue}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                  <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => onOpen(f.conversation_id)}>
                    <MessageSquareText /> Open conversation
                  </Button>
                </li>
              ))}
            </ul>
            {r.failing_replies.length > 5 && (
              <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
                <ChevronDown className={open ? "rotate-180" : ""} />
                {open ? "Show fewer" : `Show ${r.failing_replies.length - 5} more`}
              </Button>
            )}
          </div>
        )}

        {r.errors.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {plural(r.errors.length, "check")} could not run and {r.errors.length === 1 ? "was" : "were"} left out: {r.errors[0]}
          </p>
        )}
      </div>
    </Panel>
  );
}
