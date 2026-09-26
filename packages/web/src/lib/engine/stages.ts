import type { SimulationStatus } from "./types";

export const STEPS = ["Setup", "Context", "Criteria", "Guardrails", "Test Plan", "Personas", "Simulate", "Report"];

export const SIMULATE_STEP = 6;
export const REPORT_STEP = 7;

/** Map engine pipeline progress to the 8-step stepper from the design. */
export function stepForStatus(status: SimulationStatus): number {
  if (status.status === "completed") return REPORT_STEP;
  if (status.stage_number >= 6) return SIMULATE_STEP;
  return Math.max(1, status.stage_number);
}

/** What the engine is doing while no approval is pending (pipeline stages 0-5). */
export const GENERATING_LABELS: Record<string, string> = {
  queued: "Starting the pipeline…",
  discovering_bot: "Chatting with your bot to learn what it does…",
  loading_documents: "Reading your uploaded context…",
  analyzing_documents: "Inferring domain context from your documentation…",
  generating_criteria: "Drafting success criteria…",
  generating_guardrails: "Deriving guardrails and compliance boundaries…",
  generating_test_plan: "Building a test plan…",
  generating_personas: "Generating personas…",
};

/**
 * Simulation phases (stages 6-7). `engine_status` comes from the engine's
 * SimulationStatus while the run executes. Progress is the phase position,
 * not a time estimate.
 */
export const SIMULATION_PHASES: { key: string; label: string }[] = [
  { key: "generating_personas", label: "Preparing judges…" },
  { key: "running", label: "Running persona conversations against your bot…" },
  { key: "judging", label: "Judging response quality, safety and grounding…" },
  { key: "generating_report", label: "Clustering failures and preparing the report…" },
  { key: "exporting_results", label: "Exporting report files…" },
  { key: "analyzing_results", label: "Scoring coverage, workflows, policy and cost…" },
];

// A production replay has real customers, not personas
const REPLAY_LABELS: Record<string, string> = {
  running: "Sending the customers' messages to your bot again…",
  judging: "Judging the real conversations for quality, safety and grounding…",
};

export function simulationPhase(status: SimulationStatus) {
  const key = ["exporting_results", "analyzing_results"].includes(status.stage)
    ? status.stage
    : status.engine_status ?? "generating_personas";
  const index = Math.max(0, SIMULATION_PHASES.findIndex((p) => p.key === key));
  return {
    index,
    label: (status.config.replay && REPLAY_LABELS[SIMULATION_PHASES[index].key]) || SIMULATION_PHASES[index].label,
    percent: Math.round(((index + 0.5) / SIMULATION_PHASES.length) * 100),
  };
}

export const STATUS_STYLES: Record<string, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-accent text-accent-foreground",
  awaiting_approval: "bg-warn/10 text-warn",
  completed: "bg-pass/10 text-pass",
  aborted: "bg-muted text-muted-foreground",
  failed: "bg-fail/10 text-fail",
  cancelled: "bg-muted text-muted-foreground",
};

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
