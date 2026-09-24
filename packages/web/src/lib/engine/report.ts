import type { JudgedConversation, SimulationReport, Turn } from "./types";

/** Engine scores and rates are 0–1; render as whole percentages. */
export function pct(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  const v = value <= 1 ? value * 100 : value;
  return `${Math.round(v)}%`;
}

export function scoreTone(value: number | undefined | null): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  const v = value <= 1 ? value * 100 : value;
  if (v >= 80) return "text-pass";
  if (v >= 60) return "text-warn";
  return "text-fail";
}

export function judgeLabel(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function responsesJudged(report: SimulationReport): number {
  const judged = report.judged_conversations?.reduce((n, jc) => n + (jc.judged_turns?.length ?? 0), 0);
  return judged || report.summary?.total_turns || 0;
}

const isUser = (t: Turn) => (t.speaker ?? "").toLowerCase() === "user";

export interface FailureExample {
  conversationId: string;
  persona: string;
  personaType: string;
  userMessage: string;
  botMessage: string;
  score?: number;
  issues: string[];
  failedJudges: string[];
}

/** Failed bot turns with the user message that triggered them, worst first. */
export function failureExamples(report: SimulationReport, limit = 5): FailureExample[] {
  const out: FailureExample[] = [];
  for (const jc of report.judged_conversations ?? []) {
    const turns = jc.conversation?.turns ?? [];
    for (const jt of jc.judged_turns ?? []) {
      if ((jt.overall_label ?? "").toLowerCase() !== "fail") continue;
      const idx = turns.findIndex((t) => t.id && t.id === jt.turn?.id);
      const prevUser = idx > 0 ? [...turns.slice(0, idx)].reverse().find(isUser) : undefined;
      out.push({
        conversationId: jc.conversation?.id ?? "",
        persona: jc.persona?.name ?? "Unknown persona",
        personaType: jc.persona?.persona_type ?? "",
        userMessage: prevUser?.message ?? "",
        botMessage: jt.turn?.message ?? "",
        score: jt.overall_score,
        issues: jt.issues ?? [],
        failedJudges: (jt.judgments ?? []).filter((j) => j.passed === false).map((j) => judgeLabel(j.judge_name ?? "")),
      });
    }
  }
  return out.sort((a, b) => (a.score ?? 0) - (b.score ?? 0)).slice(0, limit);
}

export function conversationVerdict(jc: JudgedConversation): "pass" | "warn" | "fail" {
  const labels = (jc.judged_turns ?? []).map((t) => (t.overall_label ?? "").toLowerCase());
  if (labels.includes("fail")) return "fail";
  if (labels.includes("warning")) return "warn";
  return "pass";
}
