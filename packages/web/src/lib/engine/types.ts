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

// ── Regression suites (Phase 2 · Step 6) ──

export type CaseStatus = "regressed" | "still_failing" | "partly_fixed" | "error" | "fixed" | "still_passing";

export interface SuiteRun {
  run_id: string;
  at: string;
  bot_endpoint: string;
  bot_build: string;
  counts: Partial<Record<CaseStatus, number>>;
  verdict: RegressionResult["verdict"];
}

export interface SuiteSummary {
  id: string;
  name: string;
  created_at: string;
  source: { run_id?: string; run_name?: string; bot_endpoint?: string; bot_build?: string; run_at?: string };
  cases: number;
  failing_cases: number;
  warning_cases: number;
  guard_cases: number;
  guardrails: number;
  criteria: number;
  runs: SuiteRun[];
  last_run: SuiteRun | null;
}

export interface SuiteDetail extends SuiteSummary {
  cases_detail: { id: string; persona: string; original_label: string; tags: string[]; messages: number; first_issue: string }[];
}

export interface ReplyVerdict {
  reply: string;
  label: string;
  issue: string;
}

export interface RegressionCase {
  id: string;
  persona: string;
  status: CaseStatus;
  original_label: string;
  failing_before: number;
  failing_now: number;
  replies_fixed: number;
  replies_broken: number;
  conversation_id: string;
  error: string;
  changes: { index: number; customer: string; before: ReplyVerdict | null; after: ReplyVerdict | null }[];
}

export interface RegressionResult {
  suite_id: string;
  suite_name: string;
  source: SuiteSummary["source"];
  total_cases: number;
  counts: Record<CaseStatus, number>;
  replies_fixed: number;
  replies_broken: number;
  verdict: "regressions" | "all_fixed" | "progress" | "no_change";
  cases: RegressionCase[];
}

/** Two finished runs compared (GET /compare-runs). */
export interface RunDiff {
  before: { id: string; name: string; created_at: string; bot_build: string };
  after: { id: string; name: string; created_at: string; bot_build: string };
  comparable: boolean;
  /** Both runs cover the same saved conversations (a run and its suite replay, or two replays) */
  same_cases?: boolean;
  settings_changed: { label: string; before: unknown; after: unknown }[];
  build_changed: boolean;
  difference: {
    method: "paired" | "independent";
    mean: number | null;
    low: number | null;
    high: number | null;
    verdict: "better" | "worse" | "too close to call" | "not enough data";
  };
  metrics: {
    key: string;
    label: string;
    before: number | null;
    after: number | null;
    delta: number | null;
    higher_is_better: boolean;
    percentage: boolean;
    better: boolean | null;
  }[];
  judges: { judge: string; before: number | null; after: number | null; delta: number | null }[];
  failures: {
    new: { title: string; after: number }[];
    resolved: { title: string; before: number }[];
    persistent: { title: string; before: number; after: number }[];
  };
  note: string;
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
  /** The saved bot this run uses */
  bot_id?: string | null;
  /** Judge replies near the pass mark twice more and keep the majority (default on) */
  recheck_borderline?: boolean;
  /** Your own personas, added to the drafted ones */
  extra_personas?: PersonaInput[];
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
  regression?: { suite_id: string } | null;
  rag?: RagOptions | null;
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
  /** While conversations run: who is talking, what has been judged, the latest exchanges */
  live?: LiveProgress | null;
  pending_gate: { gate_key: string; gate_name: GateName; title: string } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  bot_build?: string;
  summary?: RunSummary | null;
  config: {
    bot_endpoint: string;
    /** The saved bot this run used */
    bot_id?: string | null;
    /** The template it was started from */
    template_id?: string | null;
    extra_personas?: string[];
    rag?: { speed: string; use_llm: boolean; require_citations: boolean; tools: number } | null;
    ci?: (CITrigger & { gates: CIGates }) | null;
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
    regression?: { suite_id: string; suite_name?: string; cases?: number } | null;
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
  /** A borderline reply judged more than once; the label is the majority. Not stable: unsure. */
  recheck?: { labels: string[]; scores: number[]; stable: boolean } | null;
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
  regression?: RegressionResult;
  rag_eval?: RagEvalResult | { error: string };
  ci?: CIVerdict;
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
  notifications?: NotificationDelivery[];
  ci?: CITrigger | null;
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

// ── Calibration centre (every human label, pooled across runs) ──

export type CalibrationLean = "too_strict" | "too_lenient" | "balanced" | "not_enough";

export interface CalibrationDisagreement {
  run_id: string;
  run_name: string;
  key: string;
  conversation_id: string;
  persona: string;
  turn_index: number;
  user_message: string;
  bot_reply: string;
  judge_score: number;
  judge_passed: boolean;
  judge_message: string;
  human_pass: boolean;
  note: string;
}

export interface CalibrationRunAgreement {
  run_id: string;
  run_name: string;
  run_at: string;
  labelled: number;
  agree: number;
  agreement_rate: number;
  low: number | null;
  high: number | null;
  pass_mark: number | null;
}

export interface CalibrationJudge {
  judge: string;
  labelled: number;
  agree: number;
  agreement_rate: number | null;
  low: number | null;
  high: number | null;
  kappa: number | null;
  human_pass_rate: number | null;
  judge_pass_rate: number | null;
  too_strict: number;
  too_lenient: number;
  lean: CalibrationLean;
  verdict: string;
  /** Set when every label went one way: what the agreement can and cannot show */
  caveat?: string;
  /** Quality and relevance: the verdict is the score against a pass mark */
  score_decided?: boolean;
  suggested_threshold: { threshold: number; agreement: number } | null;
  pass_marks_used: number[];
  runs: CalibrationRunAgreement[];
  disagreements: CalibrationDisagreement[];
}

export interface CalibrationCheckBrief {
  id: string;
  status: "running" | "completed" | "failed";
  created_at: string;
  finished_at: string | null;
  engine_version: string | null;
  labelled: number | null;
  checked: number | null;
  changed: number | null;
  errors: number | null;
  judges: {
    judge: string;
    agreement_then: number | null;
    agreement_now: number | null;
    changed: number;
    fingerprint: string | null;
  }[];
}

export interface CalibrationOverview {
  total_labels: number;
  runs_with_labels: number;
  min_labels: number;
  golden_examples: number;
  judges: CalibrationJudge[];
  checks: CalibrationCheckBrief[];
}

export interface CalibrationChange {
  run_id: string;
  run_name: string;
  key: string;
  persona: string;
  user_message: string;
  bot_reply: string;
  human_pass: boolean;
  note: string;
  before: { score: number; passed: boolean };
  after: { score: number | null; passed: boolean; message: string };
  effect: "now_agrees" | "now_disagrees";
}

export interface CalibrationCheckJudge {
  judge: string;
  labelled: number;
  checked: number;
  errors: number;
  agreement_then: number | null;
  agreement_now: number | null;
  changed: number;
  now_agree: number;
  now_disagree: number;
  verdict: string;
  changes: CalibrationChange[];
}

export interface CalibrationCheck {
  id: string;
  status: "running" | "completed" | "failed";
  created_at: string;
  finished_at: string | null;
  engine_version: string | null;
  judges: string[];
  progress: { total: number; done: number };
  summary: { labelled: number; checked: number; errors: number; changed: number; judges: CalibrationCheckJudge[] } | null;
  error: string;
  fingerprints: Record<string, string>;
}

// ── RAG & tool evaluation ──

export interface RagToolDefinition {
  name: string;
  description?: string;
  required_params?: string[];
  param_types?: Record<string, string>;
  requires_permission?: boolean;
  has_side_effects?: boolean;
  expected_sequence_position?: number;
  [key: string]: unknown;
}

export interface RagOptions {
  speed: "deterministic" | "fast" | "standard";
  use_llm: boolean;
  tool_definitions: RagToolDefinition[];
  require_citations: boolean;
  threshold?: number;
}

export interface RagMetricSummary {
  metric: string;
  kind: "rag" | "tool";
  checked: number;
  passed: number;
  pass_rate: number | null;
  average: number | null;
  reliability: "deterministic" | "hybrid" | "llm";
  example_issue: string;
}

export interface RagFailingReply {
  conversation_id: string;
  turn_index: number;
  user_message: string;
  bot_reply: string;
  evidence: "sources" | "tools" | "none";
  failed: { metric: string; score: number; issue: string }[];
}

export interface RagEvalResult {
  replies: number;
  rag_score: number | null;
  tool_score: number | null;
  replies_with_sources: number;
  replies_with_tools: number;
  evidence_modes: Record<string, number>;
  evidence_captured: boolean;
  tool_definitions: number;
  used_llm: boolean;
  eval_speed: string;
  metrics: RagMetricSummary[];
  failing_replies: RagFailingReply[];
  failing_total: number;
  errors: string[];
}

// ── Notifications ──

export type ChannelType = "slack" | "teams" | "email" | "generic_webhook";

/** A channel as the engine shows it: never its URL or password, only whether they are set. */
export interface NotificationChannel {
  id: string;
  name: string;
  type: ChannelType;
  enabled: boolean;
  events: string[];
  url_hint: string;
  url_set: boolean;
  mention_on_critical: string;
  smtp_host: string;
  smtp_port: number;
  use_tls: boolean;
  username: string;
  password_set: boolean;
  password_hint: string;
  sender: string;
  recipients: string[];
}

/** A channel as the dashboard saves it: a blank url or password keeps the saved one. */
export interface NotificationChannelInput extends Omit<NotificationChannel, "url_hint" | "url_set" | "password_set" | "password_hint"> {
  url: string;
  password: string;
}

export interface NotificationDelivery {
  at: string;
  run_id: string;
  event_type: string;
  channel: string;
  channel_id: string;
  status: "success" | "failed" | "dry_run" | "suppressed_rate_limit" | "suppressed_dedupe" | "circuit_open";
  http_status: number | null;
  attempts: number;
  problem: string;
}

export interface NotificationSettings {
  enabled: boolean;
  dashboard_url: string;
  environment: string;
  channels: NotificationChannel[];
  problems: string[];
  events: { id: string; label: string }[];
  deliveries: NotificationDelivery[];
}

export interface NotificationTestResult extends Partial<NotificationDelivery> {
  delivered: boolean;
}

// ── CI ──

export interface CICheck {
  name: string;
  passed: boolean;
  actual: string;
  limit: string;
  detail?: string;
}

export interface CIVerdict {
  passed: boolean;
  exit_code: number;
  checks: CICheck[];
  headline: string;
}

export interface CITrigger {
  source: string;
  repository: string;
  commit: string;
  branch: string;
  pull_request: string;
  build_url: string;
}

export interface CIGates {
  min_pass_rate: number | null;
  max_critical_failures: number | null;
  fail_on_regression: boolean;
  max_regressed: number;
  fail_on_quality_gates: boolean;
}

export interface CIRunRow {
  id: string;
  name: string;
  status: SessionStatus;
  created_at: string;
  ci: CITrigger;
  gates: CIGates;
  regression: string | null;
  verdict: CIVerdict | null;
}

export interface CIInfo {
  engine_version: string;
  token_required: boolean;
  runs: CIRunRow[];
}

// ── Live progress while a run runs ──

export interface LivePersona {
  id: string;
  name: string;
  type: string;
  state: "waiting" | "talking" | "judging" | "done" | "skipped";
  exchanges: number;
  verdict: "PASS" | "WARNING" | "FAIL" | null;
}

export interface LiveExchange {
  seq: number;
  persona: string;
  persona_id: string;
  customer: string;
  bot: string;
  label: "PASS" | "WARNING" | "FAIL" | null;
  issue: string;
}

export interface LiveProgress {
  elapsed_seconds: number;
  conversations_total: number;
  conversations_done: number;
  talking: number;
  exchanges: number;
  replies_judged: number;
  labels: { PASS: number; WARNING: number; FAIL: number };
  personas: LivePersona[];
  feed: LiveExchange[];
}

// ── AI models and keys (Settings) ──

export type AIRoleId = "simulator" | "setup" | "judge";
export type AIProviderId = "openai" | "anthropic" | "google";

export interface AIProvider {
  id: AIProviderId;
  label: string;
  env_var: string;
  /** Where the key in use comes from: saved here, the engine's .env, or nowhere */
  source: "dashboard" | "env" | "none";
  set: boolean;
  /** Last four characters, or the ${ENV_VAR} it is read from; never the key */
  hint: string;
}

export interface AIRole {
  id: AIRoleId;
  label: string;
  about: string;
  model: string;
  source: "dashboard" | "env";
  default: string;
  provider: AIProviderId | "ollama" | "other";
}

export interface AISettingsView {
  providers: AIProvider[];
  roles: AIRole[];
  ollama_base_url: string;
  ollama_default: string;
  suggestions: Record<string, string[]>;
  problems: string[];
}

export interface AISettingsInput {
  keys: Partial<Record<AIProviderId, string>>;
  models: Partial<Record<AIRoleId, string>>;
  ollama_base_url: string;
  clear?: AIProviderId[];
}

export interface AIModelCheck {
  role: AIRoleId;
  model: string;
  ok: boolean;
  latency_ms?: number;
  problem?: string;
}

// ── Bot health check ──

export interface BotCheckRequest {
  /** A saved bot: its key is read on the engine when none is typed */
  bot_id?: string | null;
  bot_endpoint: string;
  bot_api_key?: string;
  bot_format: string;
  bot_response_path: string;
  bot_model?: string | null;
}

export interface BotCheckResult {
  ok: boolean;
  latency_ms?: number;
  sent?: string;
  reply?: string;
  slow?: boolean;
  kind?: string;
  problem?: string;
  fix?: string;
  status?: number;
  body?: string;
  suggestions?: string[];
}

// ── Saved bots ──

export interface SavedBotInput {
  name: string;
  bot_endpoint: string;
  bot_request_format: RequestFormat;
  bot_response_path: string;
  bot_model?: string | null;
  bot_version_header?: string | null;
  bot_info_url?: string | null;
  /** Engine environment variable holding the bot's key; blank: typed on each test */
  key_env: string;
}

export interface SavedBot extends SavedBotInput {
  id: string;
  host: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  key_env_set: boolean;
  last_check: { ok: boolean; at: string; latency_ms?: number; problem?: string; kind?: string } | null;
  runs: number;
  latest_run: { id: string; status: string; created_at: string; headline: RunSummary | null } | null;
}

// ── Personas ──

export type PersonaKind = "standard" | "edge_case" | "adversarial";

export interface PersonaInput {
  name: string;
  role: string;
  goals: string[];
  tone: string;
  persona_type: PersonaKind;
  technical_level?: "novice" | "intermediate" | "expert";
  special_characteristics?: string[];
  adversarial_tactics?: string[] | null;
  topics?: string[];
  system_prompt?: string;
}

export interface SavedPersona extends PersonaInput {
  id: string;
  created_at: string;
  from_run: string | null;
}

export interface SeenPersona extends PersonaInput {
  runs: number;
  pass: number;
  warning: number;
  fail: number;
  last_run_id: string | null;
  last_run_at: string | null;
  bots: string[];
}

// ── Test templates and schedules ──

export interface TemplateSchedule {
  enabled: boolean;
  cadence: "daily" | "weekdays" | "weekly";
  /** HH:MM in the engine's local time */
  time: string;
  /** Monday = 0, for weekly */
  weekday: number;
}

export interface TestTemplate {
  id: string;
  name: string;
  source_run: string | null;
  created_at: string;
  updated_at: string;
  schedule: TemplateSchedule;
  schedule_text: string;
  schedule_warning: string;
  next_run_at: string | null;
  last_run_id: string | null;
  last_run_at: string | null;
  last_problem: string;
  bot_name: string | null;
  key_from_env: boolean;
  summary: {
    bot_endpoint: string;
    bot_id: string | null;
    mode: string;
    num_personas: number;
    max_turns: number;
    auto_approve: boolean;
    kind: string;
    documentation_filename: string | null;
  };
  last_run: { id: string; status: string; headline: RunSummary | null } | null;
}

// ── Bot history (trend) ──

export type RiskCounts = Record<"grounding" | "safety" | "quality" | "relevance" | "workflow" | "stuck", number>;

export interface BotHistoryRun {
  id: string;
  name: string;
  created_at: string;
  template_id: string | null;
  pass_rate: number | null;
  average_score: number | null;
  critical_failures: number;
  conversations: number;
  build: string;
  risks: RiskCounts;
}
