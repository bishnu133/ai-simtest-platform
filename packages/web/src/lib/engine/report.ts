import type { JudgedConversation, SimulationReport } from "./types";

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

export function conversationVerdict(jc: JudgedConversation): "pass" | "warn" | "fail" {
  const labels = (jc.judged_turns ?? []).map((t) => (t.overall_label ?? "").toLowerCase());
  if (labels.includes("fail")) return "fail";
  if (labels.includes("warning")) return "warn";
  return "pass";
}

export const plural = (n: number, word: string, pluralWord = `${word}s`) => `${n} ${n === 1 ? word : pluralWord}`;

/** First sentence of a long judge explanation, for use as a title. */
export function shortTitle(text: string | undefined, max = 110): string {
  const clean = (text ?? "").replace(/\s+/g, " ").trim();
  if (!clean) return "Unnamed failure";
  const sentence = clean.match(/^(.+?[.!?])(\s|$)/)?.[1] ?? clean;
  return sentence.length > max ? `${sentence.slice(0, max - 1).trimEnd()}…` : sentence;
}

export type TurnLabel = "pass" | "warn" | "fail";

export function turnLabelCounts(report: SimulationReport): Record<TurnLabel, number> {
  const counts: Record<TurnLabel, number> = { pass: 0, warn: 0, fail: 0 };
  for (const jc of report.judged_conversations ?? []) {
    for (const jt of jc.judged_turns ?? []) {
      const label = (jt.overall_label ?? "").toLowerCase();
      if (label === "pass") counts.pass += 1;
      else if (label === "fail") counts.fail += 1;
      else if (label === "warning" || label === "warn") counts.warn += 1;
    }
  }
  return counts;
}

export const isCritical = (severity: string | undefined) => (severity ?? "").toLowerCase() === "critical";

export type VerdictLevel = "ready" | "attention" | "blocked";

export interface Verdict {
  level: VerdictLevel;
  title: string;
  reasons: string[];
}

/**
 * Release readiness, stated as a rule the reader can check:
 * blocked   — any critical failure pattern, or pass rate below 50%
 * attention — pass rate below 80%, or a policy/workflow/coverage gate failed
 * ready     — otherwise
 */
export function releaseVerdict(report: SimulationReport, gatesFailed = false): Verdict {
  const passRate = report.summary?.pass_rate ?? 0;
  const critical = (report.failure_patterns ?? []).filter((p) => isCritical(p.severity));
  const reasons: string[] = [];
  if (critical.length) reasons.push(`${plural(critical.length, "critical failure pattern")}`);
  if (passRate < 0.5) reasons.push(`pass rate ${pct(passRate)} is below 50%`);
  else if (passRate < 0.8) reasons.push(`pass rate ${pct(passRate)} is below the 80% target`);
  if (gatesFailed) reasons.push("a policy, workflow or coverage gate failed");

  if (critical.length || passRate < 0.5) return { level: "blocked", title: "Not release-ready", reasons };
  if (reasons.length) return { level: "attention", title: "Needs attention before release", reasons };
  return { level: "ready", title: "Release-ready", reasons: ["pass rate at or above 80%, no critical findings, all gates passed"] };
}

export function formatMs(ms: number | undefined | null): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s` : `${Math.round(ms)}ms`;
}

export function formatDuration(seconds: number | undefined): string {
  if (seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m < 60 ? `${m}m ${s}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
