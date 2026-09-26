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

export type GateName = "bot_discovery" | "bot_context" | "success_criteria" | "guardrail_rules" | "test_plan" | "personas";

/** How a run gets its bearings (the engine's --mode). */
export type RunMode = "partial" | "auto" | "manual";

export type GateDecision = "approved" | "modified" | "regenerate" | "rejected";

export type RequestFormat = "openai" | "anthropic" | "custom";

export type StressPatternId = "fact_seeding" | "contradiction" | "progressive_complexity";

/** Long conversations that test what the bot remembers (the engine's --stress-memory). */
export interface StressOptions {
  turns: number;
  patterns: StressPatternId[];
  facts: number;
  contradictions: number;
}

/** Another bot run against the same personas (model comparison). */
export interface CompareTarget {
  name: string;
  bot_endpoint: string;
  /** Blank: the main bot's key, sent only if this bot is on the same origin */
  bot_api_key?: string;
  bot_request_format?: string | null;
  bot_response_path?: string | null;
  /** Sent as "model" in the request; blank: the main bot's */
  model?: string | null;
}

export interface CompareOptions {
  baseline_name: string;
  targets: CompareTarget[];
}

export interface CompareBot {
  name: string;
  baseline: boolean;
  error: string;
  pass_rate?: number | null;
  average_score?: number | null;
  conversations?: number | null;
  critical_failures?: number;
  stuck_conversations?: number;
  judge_pass_rates?: Record<string, number>;
  latency_ms?: { median: number | null; p95: number | null };
}

export interface CompareVersus {
  name: string;
  paired_personas: number;
  wins: number;
  losses: number;
  ties: number;
  /** Mean per-persona difference in replies passed (challenger minus main bot) */
  mean_difference: number | null;
  range_low: number | null;
  range_high: number | null;
  verdict: "better" | "worse" | "too close to call" | "not enough data";
}

export interface CompareResult {
  baseline: string;
  bots: CompareBot[];
  versus_baseline: CompareVersus[];
  leader: string | null;
  decisive: boolean;
  diverging: {
    persona_id: string;
    persona: string;
    persona_type: string;
    spread: number;
    bots: { name: string; conversation_id: string; pass_share: number; first_issue: string }[];
  }[];
  method: string;
}

export type ReplayFormat = "auto" | "json" | "jsonl" | "csv" | "tsv" | "text" | "markdown";

/** Production replay: judge real conversations instead of simulating. */
export interface ReplayOptions {
  /** The uploaded log; the engine masks it and never stores or echoes it */
  conversations: string;
  filename: string;
  format: ReplayFormat;
  /** mask: replace personal data before anything reads it; detect: only report it */
  privacy: "mask" | "detect";
  /** Send the customer messages to the bot again and judge today's replies */
  resend: boolean;
  sample?: number | null;
  min_turns?: number | null;
  max_turns?: number | null;
  contains?: string | null;
}

/** What loading a replay found (settings plus counts; never the conversations). */
export interface ReplayLoad {
  filename: string;
  format: ReplayFormat;
  privacy: "mask" | "detect";
  resend: boolean;
  conversations_loaded: number;
  conversations_after_filter: number;
  conversations_judged: number;
  quality: { complete: number; partial: number; low: number };
  unknown_roles: string[];
  parse_errors: string[];
  pii: {
    strategy: string;
    engine: string;
    warnings: string[];
    detected: number;
    masked: number;
    by_type: Record<string, number>;
    conversations_with_pii: number;
    conversations_clean: number;
  };
}

export interface ScenarioTemplate {
  id: string;
  name: string;
  category: string;
  difficulty: string;
  description: string;
  min_turns: number;
}

export interface CreateSimulationRequest {
  name: string;
  mode?: RunMode;
  auto_approve?: boolean;
  success_criteria?: string[];
  guardrail_rules?: string[];
  topics?: string[];
  bot_endpoint: string;
  bot_api_key?: string;
  bot_request_format: RequestFormat;
  bot_response_path: string;
  /** Sent as "model" in the request body */
  bot_model?: string | null;
  documentation: string;
  documentation_filename: string;
  num_personas: number;
  min_turns: number;
  max_turns: number;
  max_parallel: number;
  capture_response_headers?: string[];
  workflows?: string[];
  no_workflow?: boolean;
  policy?: string | null;
  track_cost?: boolean;
  guardrail_llm?: boolean | null;
  relevance_llm?: boolean | null;
  quality_threshold?: number | null;
  turn_pass_threshold?: number | null;
  bot_version_header?: string | null;
  bot_info_url?: string | null;
  judge_weights?: Record<string, number> | null;
  scenarios?: string[];
  stress?: StressOptions | null;
  replay?: ReplayOptions | null;
  compare?: CompareOptions | null;
}

export interface EngineOptions {
  workflows: { id: string; name: string; domain: string }[];
  policies: { id: string; name: string; description: string }[];
  request_formats: string[];
  /** Engines before Phase 2 · Step 3 do not send these */
  scenarios?: ScenarioTemplate[];
  stress_patterns?: { id: StressPatternId; name: string; description: string }[];
  replay_formats?: ReplayFormat[];
  /** "patterns" when the engine lacks Presidio: names are only partly masked */
  pii_engine?: "presidio+patterns" | "patterns";
}

/** Headline numbers for a finished run, without fetching its report. */
export interface RunSummary {
  pass_rate?: number | null;
  average_score?: number | null;
  total_conversations?: number | null;
  total_turns?: number | null;
  critical_failures?: number | null;
  warnings?: number | null;
  stuck_conversations?: number | null;
  execution_time_seconds?: number | null;
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
  bot_build?: string;
  summary?: RunSummary | null;
  config: {
    bot_endpoint: string;
    mode?: RunMode;
    auto_approve?: boolean;
    success_criteria?: string[];
    guardrail_rules?: string[];
    topics?: string[];
    bot_request_format: string;
    documentation_filename: string;
    num_personas: number;
    min_turns: number;
    max_turns: number;
    max_parallel: number;
    capture_response_headers?: string[];
    workflows?: string[];
    no_workflow?: boolean;
    policy?: string | null;
    track_cost?: boolean;
    guardrail_llm?: boolean | null;
  relevance_llm?: boolean | null;
  quality_threshold?: number | null;
  turn_pass_threshold?: number | null;
    bot_version_header?: string | null;
    bot_info_url?: string | null;
    judge_weights?: Record<string, number> | null;
    scenarios?: string[];
    stress?: StressOptions | null;
    replay?: (Omit<ReplayOptions, "conversations"> & { conversations_loaded?: number; conversations_judged?: number }) | null;
    compare?: { baseline_name: string; targets: Omit<CompareTarget, "bot_api_key">[] } | null;
  };
  /** While a comparison runs: which bot, of how many */
  compare_progress?: { index: number; total: number; name: string } | null;
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
  stuck_conversations?: number;
  /** Memory stress: recall replies scored by exact match, left out of pass_rate */
  memory_checked_turns?: number;
  memory_recalled_turns?: number;
}

export interface FailurePattern {
  pattern_name?: string;
  description?: string;
  frequency?: number;
  severity?: string;
  example_conversation_ids?: string[];
  root_causes?: RootCause[];
}

/** Failing turns grouped by how the bot's reply opens (engine report_generator). */
export interface RootCause {
  reply_opening: string;
  example_reply: string;
  turns: number;
  conversation_ids: string[];
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
  severity?: string;
  message?: string;
  evidence?: unknown;
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
  bot_repeats?: number;
  user_reasks?: number;
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

// ── Post-simulation analysis (same helpers as the engine's HTML report) ──

export interface LatencyStats {
  available: boolean;
  count: number;
  p50: number;
  p90: number;
  p95: number;
  p99: number;
  mean: number;
  max: number;
  slowest_conversation_id: string;
}

export interface TriageItem {
  rank: number;
  title: string;
  detail: string;
  severity: string;
  frequency: number;
  reach: number;
  score: number;
  conversation_ids: string[];
  affected_conversations: number;
  source: string;
}

export interface TrendDelta {
  label: string;
  current: number;
  previous: number;
  delta: number;
  improved: boolean | null;
  higher_is_better: boolean;
  is_percentage: boolean;
  is_count: boolean;
  formatted: string;
}

export interface RunTrend {
  available: boolean;
  previous_timestamp: string;
  comparable: boolean;
  incomparable_reason: string;
  changes?: string[];
  previous_build?: string;
  current_build?: string;
  deltas: TrendDelta[];
}

export interface JudgeStat {
  name: string;
  /** "scored" judges set the pass rate; "detector" judges report findings. */
  kind: "scored" | "detector";
  turns: number;
  mean_score: number;
  pass_rate: number;
  failed_turns: number;
  /** Non-passing turns where this was the only failing judge. */
  sole_failures: number;
  weight: number | null;
  pass_rate_by_persona_type: Record<string, number>;
  /** Pass rate by position in the conversation, e.g. { "1-5": 0.3, "6-10": 0.2 } */
  pass_rate_by_turn?: Record<string, number>;
  /** Upper bound on the run's pass rate if this judge's failures were forgiven. */
  pass_rate_without?: number | null;
  /** Mean of each rubric criterion (0–1), weakest first. */
  criteria_means?: Record<string, number>;
  notes: string[];
}

export interface JudgeBreakdown {
  total_turns: number;
  non_passing_turns: number;
  multi_judge_failures: number;
  pass_rate_by_turn?: Record<string, number>;
  quality_threshold?: number;
  turn_pass_threshold?: number;
  judges: JudgeStat[];
}

export interface LoopStats {
  stuck_conversations: number;
  conversations_with_bot_repeats: number;
  conversations_with_user_reasks: number;
  bot_repeats: number;
  user_reasks: number;
}

export interface CoverageSummary {
  overall_coverage?: number;
  grade?: string;
  dimension_scores?: Record<string, number>;
  /** 0 means the dimension was not measured in this run. */
  dimension_weights?: Record<string, number>;
  gaps?: string[];
  recommendations?: string[];
  capped_reason?: string | null;
  notes?: string[];
}

export interface WorkflowSummary {
  workflow?: string;
  domain?: string;
  role?: string;
  role_label?: string;
  total_conversations?: number;
  passed?: number;
  failed?: number;
  avg_score?: number;
  critical_failures?: number;
  not_applicable?: number;
  status?: string;
  scope_check_clean?: boolean;
  scope_check_message?: string;
  rule_results?: { rule: string; severity: string; passed: number; checked: number }[];
}

export interface RunAnalysis {
  quality_gates_failed?: boolean;
  latency?: LatencyStats;
  fix_first?: TriageItem[];
  trend?: RunTrend;
  coverage?: CoverageSummary;
  cost?: Record<string, unknown> & { total_estimated_cost_usd?: number; total_calls?: number; total_tokens?: number };
  workflows?: WorkflowSummary[];
  bot_build?: { build: string; source: string };
  judge_breakdown?: JudgeBreakdown;
  loops?: LoopStats;
  scenarios?: ScenarioResult[];
  memory?: MemoryResult;
  replay?: ReplayLoad;
  compare?: CompareResult;
}

/** One scenario template's results, weakest first. */
export interface ScenarioResult {
  id: string;
  name: string;
  category: string;
  difficulty: string;
  description: string;
  conversations: number;
  /** Share of this scenario's replies judged PASS */
  pass_rate: number;
  average_score: number;
  stuck: number;
  conversation_ids: string[];
}

export interface MemoryMiss {
  conversation_id: string;
  persona: string;
  fact_id: string;
  category: string;
  seeded_at: number;
  asked_at: number;
  gap: number;
  score: number;
  /** Why it was missed */
  reason: MissReason;
  expected?: string[];
  question: string;
  reply: string;
}

export type MissReason = "deflected" | "wrong_value" | "ignored";

/** Memory stress results, read from the transcripts. */
export interface MemoryResult {
  turns: number;
  patterns: StressPatternId[];
  conversations: number;
  facts_shared: number;
  facts_asked: number;
  facts_recalled: number;
  facts_not_asked: number;
  recall_rate: number | null;
  contradictions_injected: number;
  contradictions_noticed: number;
  noticed_rate: number | null;
  by_fact: { fact_id: string; category: string; shared: number; asked: number; recalled: number }[];
  by_gap: { gap: string; asked: number; recalled: number }[];
  misses: MemoryMiss[];
  by_reason?: { reason: MissReason; label: string; count: number }[];
  per_conversation: { conversation_id: string; persona: string; facts_asked: number; facts_recalled: number; contradictions: number; noticed: number }[];
}

export interface ApprovedInput {
  title: string;
  decision: string;
  data: unknown;
}

export interface ReportResponse {
  simulation_id: string;
  name: string;
  report: SimulationReport;
  personas: ReportPersona[];
  analysis?: RunAnalysis;
  inputs?: Record<string, ApprovedInput>;
  exports: string[];
}

// ── Judge review (label a sample of replies to calibrate a judge) ──

export interface ReviewItem {
  key: string;
  conversation_id: string;
  persona: string;
  turn_index: number;
  user_message: string;
  bot_reply: string;
  judge_score: number;
  judge_passed: boolean;
  judge_message: string;
  human_pass: boolean | null;
  note: string;
}

export interface ReviewSummary {
  judge: string;
  labelled: number;
  agree: number;
  agreement_rate: number | null;
  judge_too_strict: number;
  judge_too_lenient: number;
  verdict: string;
  suggested_threshold: { threshold: number; agreement: number } | null;
  judge_boundary: { lowest_passing_score: number | null; highest_failing_score: number | null };
}

export interface ReviewResponse {
  judge: string;
  judges: string[];
  items: ReviewItem[];
  summary: ReviewSummary;
}
