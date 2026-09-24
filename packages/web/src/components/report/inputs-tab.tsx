"use client";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { judgeLabel } from "@/lib/engine/report";
import type { ApprovedInput, ReportResponse } from "@/lib/engine/types";
import { EmptyNote, Panel } from "./parts";

const GATES: { key: string; title: string; description: string }[] = [
  { key: "bot_context", title: "Domain context", description: "What the AI inferred about the bot from your documentation." },
  { key: "success_criteria", title: "Success criteria", description: "What every response was judged against." },
  { key: "guardrail_rules", title: "Guardrails", description: "Boundaries the bot must not cross." },
  { key: "test_plan", title: "Test plan", description: "Topics and persona mix the simulation covered." },
];

const HIDDEN_KEYS = new Set(["type", "raw_summary", "source_doc_count", "source_chunk_count"]);

function cell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(cell).join("; ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function DecisionTag({ input }: { input: ApprovedInput }) {
  const modified = input.decision === "modified";
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${modified ? "bg-warn/10 text-warn" : "bg-pass/10 text-pass"}`}
    >
      {modified ? "Edited & approved" : "Approved"}
    </span>
  );
}

function RowsTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((k) => !HIDDEN_KEYS.has(k));
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader className="bg-muted/50">
          <TableRow>
            {columns.map((c) => (
              <TableHead key={c} className="whitespace-nowrap font-semibold text-foreground">
                {judgeLabel(c)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i}>
              {columns.map((c) => (
                <TableCell key={c} className="whitespace-normal align-top text-sm">
                  {cell(r[c])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function InputBody({ input }: { input: ApprovedInput }) {
  const data = input.data;
  if (Array.isArray(data)) {
    const objects = data.filter((d): d is Record<string, unknown> => !!d && typeof d === "object");
    const topics = objects.filter((o) => o.type === "topic" || !("type" in o));
    const extras = objects.filter((o) => "type" in o && o.type !== "topic");
    return (
      <div className="space-y-3">
        {topics.length ? <RowsTable rows={topics} /> : <EmptyNote>Nothing approved.</EmptyNote>}
        {extras.map((e, i) => (
          <p key={i} className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{judgeLabel(String(e.type))}:</span>{" "}
            {Object.entries(e)
              .filter(([k]) => k !== "type")
              .map(([k, v]) => `${judgeLabel(k)} ${cell(v)}`)
              .join(" · ")}
          </p>
        ))}
      </div>
    );
  }
  if (data && typeof data === "object") {
    return (
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[12rem_1fr]">
        {Object.entries(data as Record<string, unknown>)
          .filter(([k, v]) => !HIDDEN_KEYS.has(k) && cell(v) !== "—")
          .map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-medium text-muted-foreground">{judgeLabel(k)}</dt>
              <dd className="text-foreground">{cell(v)}</dd>
            </div>
          ))}
      </dl>
    );
  }
  return <EmptyNote>Nothing recorded.</EmptyNote>;
}

export function InputsTab({ data }: { data: ReportResponse }) {
  const inputs = data.inputs ?? {};
  return (
    <div className="space-y-4">
      {GATES.map((g) =>
        inputs[g.key] ? (
          <Panel key={g.key} title={g.title} description={g.description} action={<DecisionTag input={inputs[g.key]} />}>
            <InputBody input={inputs[g.key]} />
          </Panel>
        ) : null,
      )}
      <Panel title="Personas" description="The simulated users that ran, after your review.">
        {data.personas.length ? (
          <RowsTable
            rows={data.personas.map((p) => ({
              name: p.name,
              persona_type: judgeLabel(p.persona_type ?? ""),
              tone: p.tone,
              goals: p.goals,
            }))}
          />
        ) : (
          <EmptyNote>No personas recorded.</EmptyNote>
        )}
      </Panel>
      {!Object.keys(inputs).length && (
        <EmptyNote>This run did not record approved inputs (it may predate this dashboard version).</EmptyNote>
      )}
    </div>
  );
}
