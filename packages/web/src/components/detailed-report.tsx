import Link from "next/link";
import { AlertTriangle, Download, FileJson, FileSpreadsheet, RotateCcw, ScrollText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { engine } from "@/lib/engine/client";
import {
  conversationVerdict,
  failureExamples,
  judgeLabel,
  pct,
  responsesJudged,
  scoreTone,
} from "@/lib/engine/report";
import type { JudgedConversation, ReportResponse } from "@/lib/engine/types";

const EXPORT_LABELS: Record<string, { label: string; icon: typeof Download }> = {
  html: { label: "Download HTML Report", icon: Download },
  jsonl: { label: "Download JSON Dataset", icon: FileJson },
  csv: { label: "Download CSV", icon: FileSpreadsheet },
  summary: { label: "Summary JSON", icon: FileJson },
  audit_trail: { label: "Approval Audit Trail", icon: ScrollText },
};

const VERDICT_STYLES = {
  pass: "bg-pass/10 text-pass",
  warn: "bg-warn/10 text-warn",
  fail: "bg-fail/10 text-fail",
};

function Section({ n, title, children, icon }: { n: number; title: string; children: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold border-b pb-2 flex items-center gap-2">
        {icon}
        {n}. {title}
      </h2>
      {children}
    </section>
  );
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground italic">{children}</p>;
}

function ConversationSample({ jc }: { jc: JudgedConversation }) {
  const verdict = conversationVerdict(jc);
  const judgedById = new Map((jc.judged_turns ?? []).map((jt) => [jt.turn?.id, jt]));

  return (
    <details className="group rounded-lg border bg-card">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="font-medium truncate">{jc.persona?.name ?? "Persona"}</div>
          <div className="text-xs text-muted-foreground">
            {judgeLabel(jc.persona?.persona_type ?? "")} · {jc.conversation?.turns?.length ?? 0} messages · score{" "}
            {pct(jc.overall_score)}
          </div>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase ${VERDICT_STYLES[verdict]}`}>
          {verdict}
        </span>
      </summary>
      <div className="space-y-3 border-t p-4">
        {(jc.conversation?.turns ?? []).map((turn, i) => {
          const isUser = (turn.speaker ?? "").toLowerCase() === "user";
          const judged = turn.id ? judgedById.get(turn.id) : undefined;
          const label = (judged?.overall_label ?? "").toLowerCase();
          return (
            <div key={turn.id ?? i} className={`flex ${isUser ? "justify-start" : "justify-end"}`}>
              <div
                className={`max-w-[85%] rounded-lg p-3 text-sm whitespace-pre-wrap ${
                  isUser ? "bg-muted rounded-tl-none" : label === "fail" ? "bg-fail/10 rounded-tr-none" : "bg-accent rounded-tr-none"
                }`}
              >
                <div className="mb-1 text-xs font-semibold text-muted-foreground">{isUser ? "User" : "Bot"}</div>
                {turn.message}
                {judged && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {(judged.judgments ?? []).map((j) => (
                      <span
                        key={j.judge_name}
                        className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${j.passed ? "bg-pass/10 text-pass" : "bg-fail/10 text-fail"}`}
                        title={j.message}
                      >
                        {judgeLabel(j.judge_name ?? "")} {pct(j.score)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </details>
  );
}

export function DetailedReport({ data }: { data: ReportResponse }) {
  const { report, simulation_id: id } = data;
  const summary = report.summary ?? {};
  const judges = Object.entries(report.score_by_judge ?? {});
  const weakestJudge = [...judges].sort((a, b) => a[1] - b[1])[0];
  const strongestJudge = [...judges].sort((a, b) => b[1] - a[1])[0];
  const failures = failureExamples(report);
  const personaTypes = Object.entries(report.score_by_persona_type ?? {}).sort((a, b) => a[1] - b[1]);
  const conversations = [...(report.judged_conversations ?? [])].sort(
    (a, b) => (a.overall_score ?? 0) - (b.overall_score ?? 0),
  );

  return (
    <div className="py-6 space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 border-b pb-6">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Detailed AI SimTest Report</h1>
          <p className="text-muted-foreground">Comprehensive breakdown of simulation results and failures for {data.name}.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {data.exports
            .filter((fmt) => EXPORT_LABELS[fmt])
            .map((fmt) => {
              const { label, icon: Icon } = EXPORT_LABELS[fmt];
              return (
                <Button key={fmt} asChild variant="outline">
                  <a href={engine.exportUrl(id, fmt)} download>
                    <Icon /> {label}
                  </a>
                </Button>
              );
            })}
          <Button asChild className="shadow-sm shadow-primary/20">
            <Link href="/">
              <RotateCcw /> Start New Test
            </Link>
          </Button>
        </div>
      </div>

      <div className="space-y-12">
        <Section n={1} title="Executive Summary">
          <Card>
            <CardContent className="space-y-2 text-muted-foreground leading-relaxed">
              <p>
                The simulation ran {summary.total_personas ?? 0} personas across {summary.total_conversations ?? 0}{" "}
                conversations, and {responsesJudged(report)} bot responses were judged. The overall pass rate was{" "}
                <strong className={scoreTone(summary.pass_rate)}>{pct(summary.pass_rate)}</strong> with an average score of{" "}
                <strong className={scoreTone(summary.average_score)}>{pct(summary.average_score)}</strong>.
              </p>
              {strongestJudge && weakestJudge && strongestJudge[0] !== weakestJudge[0] && (
                <p>
                  Strongest area: <strong className="text-foreground">{judgeLabel(strongestJudge[0])}</strong> (
                  {pct(strongestJudge[1])}). Weakest area: <strong className="text-foreground">{judgeLabel(weakestJudge[0])}</strong>{" "}
                  ({pct(weakestJudge[1])}).
                </p>
              )}
              <p>
                {plural(summary.critical_failures ?? 0, "response")} failed and {summary.warnings ?? 0} flagged with
                warnings; {plural(report.failure_patterns?.length ?? 0, "recurring failure pattern")} identified.
              </p>
            </CardContent>
          </Card>
        </Section>

        <Section n={2} title="Judge Scores">
          {judges.length ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {judges.map(([judge, score]) => (
                <Card key={judge} className="gap-3">
                  <CardHeader className="pb-0">
                    <CardTitle className="text-sm font-medium text-muted-foreground">{judgeLabel(judge)} Score</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    <div className={`text-2xl font-bold tabular-nums ${scoreTone(score)}`}>{pct(score)}</div>
                    <Progress value={score <= 1 ? score * 100 : score} className="h-1.5" />
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <Empty>No judge scores were reported.</Empty>
          )}
        </Section>

        <Section n={3} title="Failure Patterns">
          {report.failure_patterns?.length ? (
            <Card className="py-0 overflow-hidden">
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow>
                    <TableHead>Pattern</TableHead>
                    <TableHead>Frequency</TableHead>
                    <TableHead>Impact</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.failure_patterns.map((fp, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-medium whitespace-normal">{fp.description || fp.pattern_name}</TableCell>
                      <TableCell>{fp.frequency ?? 0} occurrences</TableCell>
                      <TableCell>
                        <Badge variant={["high", "critical"].includes((fp.severity ?? "").toLowerCase()) ? "destructive" : "secondary"} className="capitalize">
                          {fp.severity ?? "unknown"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          ) : (
            <Empty>No recurring failure patterns — nice.</Empty>
          )}
        </Section>

        <Section n={4} title="Critical Failures" icon={<AlertTriangle className="w-5 h-5 text-destructive" />}>
          {failures.length ? (
            <div className="space-y-4">
              {failures.map((f, i) => (
                <Card key={i} className="border-destructive/30 shadow-sm gap-0 py-0 overflow-hidden">
                  <CardHeader className="bg-destructive/5 py-4">
                    <CardTitle className="text-base flex flex-wrap items-center justify-between gap-2">
                      {f.issues[0] ?? `Failed response to ${f.persona}`}
                      <Badge variant="destructive">Score {pct(f.score)}</Badge>
                    </CardTitle>
                    <CardDescription>
                      {f.persona}
                      {f.personaType && ` · ${judgeLabel(f.personaType)}`}
                      {f.failedJudges.length > 0 && ` · Failed: ${f.failedJudges.join(", ")}`}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="py-4 space-y-3">
                    {f.userMessage && (
                      <div className="grid grid-cols-1 md:grid-cols-[100px_1fr] gap-2 items-baseline">
                        <span className="text-sm font-medium text-muted-foreground">User:</span>
                        <div className="bg-muted p-3 rounded-lg rounded-tl-none text-sm whitespace-pre-wrap">{f.userMessage}</div>
                      </div>
                    )}
                    <div className="grid grid-cols-1 md:grid-cols-[100px_1fr] gap-2 items-baseline">
                      <span className="text-sm font-medium text-muted-foreground">Bot:</span>
                      <div className="bg-fail/10 p-3 rounded-lg rounded-tl-none text-sm whitespace-pre-wrap">{f.botMessage}</div>
                    </div>
                    {f.issues.length > 1 && (
                      <ul className="list-disc pl-5 text-sm text-muted-foreground">
                        {f.issues.slice(1).map((issue, j) => (
                          <li key={j}>{issue}</li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <Empty>No failed responses.</Empty>
          )}
        </Section>

        <Section n={5} title="Persona Performance">
          {personaTypes.length ? (
            <Card className="py-0 overflow-hidden">
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow>
                    <TableHead>Persona Type</TableHead>
                    <TableHead>Average Score</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {personaTypes.map(([type, score]) => (
                    <TableRow key={type}>
                      <TableCell>{judgeLabel(type)}</TableCell>
                      <TableCell className={`font-semibold tabular-nums ${scoreTone(score)}`}>{pct(score)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          ) : (
            <Empty>No persona breakdown available.</Empty>
          )}
        </Section>

        <Section n={6} title="Conversation Samples">
          {conversations.length ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">Worst-scoring conversations first. Click to expand the transcript.</p>
              {conversations.map((jc, i) => (
                <ConversationSample key={jc.conversation?.id ?? i} jc={jc} />
              ))}
            </div>
          ) : (
            <Empty>No conversations recorded.</Empty>
          )}
        </Section>

        <Section n={7} title="Recommended Fixes">
          {report.recommendations?.length ? (
            <Card>
              <CardContent>
                <ul className="list-disc pl-5 space-y-2 text-foreground">
                  {report.recommendations.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : (
            <Empty>No recommendations generated.</Empty>
          )}
        </Section>
      </div>
    </div>
  );
}
