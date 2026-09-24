"use client";

import { useMemo, useState } from "react";
import { ArrowUpDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { conversationVerdict, judgeLabel, pct, scoreTone } from "@/lib/engine/report";
import type { JudgedConversation, ReportResponse } from "@/lib/engine/types";

export const VERDICT_STYLES = {
  pass: "bg-pass/10 text-pass",
  warn: "bg-warn/10 text-warn",
  fail: "bg-fail/10 text-fail",
} as const;

const VERDICT_LABELS = { pass: "Pass", warn: "Warning", fail: "Fail" } as const;

type SortKey = "score" | "persona" | "turns" | "loops";

// Same rule as the engine (conversation_loops.STUCK_AT)
const STUCK_AT = 2;

function failedJudges(jc: JudgedConversation): string[] {
  const names = new Set<string>();
  for (const jt of jc.judged_turns ?? []) for (const j of jt.judgments ?? []) if (j.passed === false && j.judge_name) names.add(j.judge_name);
  return [...names];
}

export function ConversationsTab({ data, onOpen }: { data: ReportResponse; onOpen: (id: string) => void }) {
  const conversations = data.report.judged_conversations;
  const [query, setQuery] = useState("");
  const [verdict, setVerdict] = useState<"all" | "pass" | "warn" | "fail">("all");
  const [personaType, setPersonaType] = useState("all");
  const [stuckOnly, setStuckOnly] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "score", asc: true });

  const rows = useMemo(
    () =>
      (conversations ?? []).map((jc) => ({
        jc,
        id: jc.conversation?.id ?? "",
        persona: jc.persona?.name ?? "Persona",
        personaType: jc.persona?.persona_type ?? "unknown",
        verdict: conversationVerdict(jc),
        score: jc.overall_score ?? 0,
        turns: jc.conversation?.turns?.length ?? 0,
        failed: failedJudges(jc),
        repeats: jc.bot_repeats ?? 0,
        reasks: jc.user_reasks ?? 0,
        loops: (jc.bot_repeats ?? 0) + (jc.user_reasks ?? 0),
        stuck: (jc.bot_repeats ?? 0) >= STUCK_AT || (jc.user_reasks ?? 0) >= STUCK_AT,
        text: (jc.conversation?.turns ?? []).map((t) => t.message ?? "").join(" ").toLowerCase(),
      })),
    [conversations],
  );
  const personaTypes = useMemo(() => [...new Set(rows.map((r) => r.personaType))].sort(), [rows]);
  const counts = useMemo(() => {
    const c = { all: rows.length, pass: 0, warn: 0, fail: 0 };
    for (const r of rows) c[r.verdict] += 1;
    return { ...c, stuck: rows.filter((r) => r.stuck).length };
  }, [rows]);

  const q = query.trim().toLowerCase();
  const visible = rows
    .filter((r) => verdict === "all" || r.verdict === verdict)
    .filter((r) => personaType === "all" || r.personaType === personaType)
    .filter((r) => !stuckOnly || r.stuck)
    .filter((r) => !q || r.persona.toLowerCase().includes(q) || r.text.includes(q))
    .sort((a, b) => {
      const dir = sort.asc ? 1 : -1;
      if (sort.key === "persona") return a.persona.localeCompare(b.persona) * dir;
      return (a[sort.key] - b[sort.key]) * dir;
    });

  const sortButton = (key: SortKey, label: string) => (
    <button
      type="button"
      className="inline-flex items-center gap-1 font-semibold text-foreground"
      onClick={() => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}
      aria-label={`Sort by ${label}`}
    >
      {label} <ArrowUpDown className={`h-3.5 w-3.5 ${sort.key === key ? "text-foreground" : "text-muted-foreground"}`} />
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="relative w-full lg:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search persona or message text…"
            className="h-9 pl-9"
            aria-label="Search conversations"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by verdict">
            {(["all", "fail", "warn", "pass"] as const).map((v) => (
              <Button key={v} size="xs" variant={verdict === v ? "default" : "outline"} onClick={() => setVerdict(v)} aria-pressed={verdict === v}>
                {v === "all" ? "All" : VERDICT_LABELS[v]} {counts[v]}
              </Button>
            ))}
          </div>
          {counts.stuck > 0 && (
            <Button size="xs" variant={stuckOnly ? "default" : "outline"} onClick={() => setStuckOnly((v) => !v)} aria-pressed={stuckOnly}>
              Stuck {counts.stuck}
            </Button>
          )}
          <select
            className="h-7 rounded-md border border-input bg-background px-2 text-xs"
            value={personaType}
            onChange={(e) => setPersonaType(e.target.value)}
            aria-label="Filter by persona type"
          >
            <option value="all">All persona types</option>
            {personaTypes.map((t) => (
              <option key={t} value={t}>
                {judgeLabel(t)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border bg-card shadow-xs">
        <Table className="min-w-[720px]">
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead>{sortButton("persona", "Persona")}</TableHead>
              <TableHead>Verdict</TableHead>
              <TableHead>{sortButton("score", "Score")}</TableHead>
              <TableHead>{sortButton("turns", "Messages")}</TableHead>
              <TableHead>{sortButton("loops", "Loops")}</TableHead>
              <TableHead>Failed judges</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                  No conversations match these filters.
                </TableCell>
              </TableRow>
            ) : (
              visible.map((r) => (
                <TableRow
                  key={r.id}
                  className="cursor-pointer"
                  onClick={() => onOpen(r.id)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onOpen(r.id)}
                  tabIndex={0}
                  aria-label={`Open transcript for ${r.persona}`}
                >
                  <TableCell>
                    <div className="font-medium text-foreground">{r.persona}</div>
                    <div className="text-xs text-muted-foreground">{judgeLabel(r.personaType)}</div>
                  </TableCell>
                  <TableCell>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${VERDICT_STYLES[r.verdict]}`}>
                      {VERDICT_LABELS[r.verdict]}
                    </span>
                  </TableCell>
                  <TableCell className={`font-semibold tabular-nums ${scoreTone(r.score)}`}>{pct(r.score)}</TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">{r.turns}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {r.loops ? (
                      <span title="Bot repeated a reply · user said they were not answered">
                        <span className={r.stuck ? "font-semibold text-warn" : undefined}>
                          {r.repeats} repeat{r.repeats === 1 ? "" : "s"} · {r.reasks} re-ask{r.reasks === 1 ? "" : "s"}
                        </span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="whitespace-normal text-sm text-muted-foreground">
                    {r.failed.length ? r.failed.map(judgeLabel).join(", ") : "—"}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        Showing {visible.length} of {rows.length} conversations. Click a row to read the transcript with every judge&apos;s reasoning.
      </p>
    </div>
  );
}
