/**
 * Types for the AI SimTest engine's /wizard API (src/api/wizard.py in ai-simtest).
 * Report shapes mirror the engine's SimulationReport model_dump(mode="json");
 * fields are optional where the engine may omit them.
 */

export type SessionStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "aborted"
  | "failed"
  | "cancelled";

export const TERMINAL_STATUSES: SessionStatus[] = ["completed", "aborted", "failed", "cancelled"];

export type GateName = "bot_context" | "success_criteria" | "guardrail_rules" | "test_plan" | "personas";

export type GateDecision = "approved" | "modified" | "regenerate" | "rejected";

export type RequestFormat = "openai" | "anthropic" | "custom";

export interface CreateSimulationRequest {
  name: string;
  bot_endpoint: string;
  bot_api_key?: string;
  bot_request_format: RequestFormat;
  bot_response_path: string;
  documentation: string;
  documentation_filename: string;
  num_personas: number;
  min_turns: number;
  max_turns: number;
  max_parallel: number;
}

export interface SimulationStatus {
  simulation_id: string;
  name: string;
  status: SessionStatus;
  stage: string;
  stage_number: number;
  total_stages: number;
  engine_status: string | null;
  pending_gate: { gate_key: string; gate_name: GateName; title: string } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  config: {
    bot_endpoint: string;
    bot_request_format: string;
    documentation_filename: string;
    num_personas: number;
    min_turns: number;
    max_turns: number;
    max_parallel: number;
  };
}

export interface GateItem {
  id: string;
  content: unknown;
  explanation: string;
  confidence: "high" | "medium" | "low";
}

export interface PendingGate {
  gate_key: string;
  gate_name: GateName;
  stage_number: number;
  title: string;
  description: string;
  items: GateItem[];
  raw_data: Record<string, unknown>;
}

// ── Report ────────────────────────────────────────────────

export interface ReportSummary {
  simulation_name?: string;
  total_personas?: number;
  total_conversations?: number;
  total_turns?: number;
  pass_rate?: number;
  average_score?: number;
  critical_failures?: number;
  warnings?: number;
  execution_time_seconds?: number;
}

export interface FailurePattern {
  pattern_name?: string;
  description?: string;
  frequency?: number;
  severity?: string;
  example_conversation_ids?: string[];
}

export interface Turn {
  id?: string;
  speaker?: string;
  message?: string;
  latency_ms?: number | null;
}

export interface JudgeResult {
  judge_name?: string;
  passed?: boolean;
  score?: number;
  label?: string;
  message?: string;
}

export interface JudgedTurn {
  turn?: Turn;
  overall_score?: number;
  overall_label?: string;
  issues?: string[];
  judgments?: JudgeResult[];
}

export interface ReportPersona {
  id?: string;
  name?: string;
  persona_type?: string;
  tone?: string;
  goals?: string[];
}

export interface JudgedConversation {
  conversation?: { id?: string; persona_id?: string; turns?: Turn[] };
  persona?: ReportPersona;
  judged_turns?: JudgedTurn[];
  overall_score?: number;
  pass_rate?: number;
  failure_modes?: string[];
}

export interface SimulationReport {
  summary?: ReportSummary;
  failure_patterns?: FailurePattern[];
  score_by_judge?: Record<string, number>;
  score_by_persona_type?: Record<string, number>;
  most_problematic_personas?: unknown[];
  recommendations?: string[];
  judged_conversations?: JudgedConversation[];
}

export interface ReportResponse {
  simulation_id: string;
  name: string;
  report: SimulationReport;
  personas: ReportPersona[];
  exports: string[];
}
