import type { JudgedConversation } from "./types";

// Same rule as the engine (conversation_loops.STUCK_AT)
export const STUCK_AT = 2;

export type RiskId = "grounding" | "safety" | "quality" | "relevance" | "workflow" | "stuck";

export interface Risk {
  id: RiskId;
  label: string;
  /** What a hit means, in a tester's words */
  about: string;
  /** Judges whose failure counts toward this risk */
  judges: (name: string) => boolean;
}

export const RISKS: Risk[] = [
  { id: "grounding", label: "Made-up facts", about: "Claims your documents don't support", judges: (n) => n === "grounding" },
  {
    id: "safety",
    label: "Safety & rules",
    about: "Unsafe replies or broken guardrails",
    judges: (n) => n === "safety" || n.startsWith("guardrail") || n.startsWith("policy") || n.startsWith("compliance"),
  },
  { id: "quality", label: "Poor answers", about: "Unhelpful, unclear or incomplete", judges: (n) => n === "quality" },
  { id: "relevance", label: "Off-topic", about: "Didn't answer what was asked", judges: (n) => n === "relevance" },
  { id: "workflow", label: "Broken workflows", about: "Skipped or wrong process steps", judges: (n) => n.startsWith("workflow") },
  { id: "stuck", label: "Got stuck", about: "Repeated itself or made the customer ask again", judges: () => false },
];

export function isStuck(jc: JudgedConversation): boolean {
  return (jc.bot_repeats ?? 0) >= STUCK_AT || (jc.user_reasks ?? 0) >= STUCK_AT;
}

/** Whether the conversation shows the risk: any reply failed by one of its judges. */
export function hasRisk(jc: JudgedConversation, risk: Risk): boolean {
  if (risk.id === "stuck") return isStuck(jc);
  return (jc.judged_turns ?? []).some((jt) =>
    (jt.judgments ?? []).some((j) => j.passed === false && !!j.judge_name && risk.judges(j.judge_name.toLowerCase())),
  );
}

/** Risks this run could detect (its judges ran), with how many conversations hit each. */
export function riskSummary(conversations: JudgedConversation[]) {
  const ran = new Set<string>();
  for (const jc of conversations)
    for (const jt of jc.judged_turns ?? []) for (const j of jt.judgments ?? []) if (j.judge_name) ran.add(j.judge_name.toLowerCase());
  return RISKS.filter((r) => r.id === "stuck" || [...ran].some(r.judges)).map((r) => ({
    risk: r,
    hits: conversations.filter((jc) => hasRisk(jc, r)).length,
  }));
}
