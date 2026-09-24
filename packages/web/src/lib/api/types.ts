export type ConversationVerdict = "pass" | "fail" | "error" | "pending";

export interface ConversationSummary {
  id: string;
  tenant_id: string;
  workspace_id: string;
  run_id: string;
  persona_id: string;
  persona_name: string;
  persona_type: string;
  verdict: ConversationVerdict;
  pass_rate: number;
  turn_count: number;
  judge_scores: Record<string, number>;
  failure_reason: string | null;
  failure_category: string | null;
  created_at: string;
  updated_at: string;
  tags: Record<string, string>;
}

export interface ConversationListResponse {
  items: ConversationSummary[];
  next_cursor: string | null;
  has_more: boolean;
}

export type Severity = "info" | "warning" | "critical";

export interface RegressionSignal {
  type: string;
  severity: Severity;
  metric: string;
  payload: Record<string, unknown>;
}

export type ComparisonStatus = "pending" | "running" | "completed" | "failed";

export interface ComparisonRecord {
  id: string;
  workspace_id: string;
  tenant_id: string;
  left_run_id: string;
  right_run_id: string;
  status: ComparisonStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  engine_version: string;
  regression_signals: RegressionSignal[];
  verdict: "regression" | "improvement" | "neutral" | "inconclusive" | null;
  evidence_count: number | null;
}

export interface ComparisonListResponse {
  items: ComparisonRecord[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface JudgeBreakdown { judge_name: string; pass_count: number; fail_count: number; avg_score: number; }
export interface FailurePattern { cluster_id: string; label: string; count: number; sample_conversation_ids: string[]; }
export interface CoverageMetric { dimension: string; covered: number; total: number; }
export interface RunOverview { run_id: string; status: string; pass_rate: number; total_conversations: number; started_at: string | null; completed_at: string | null; }
export interface DashboardSummary {
  overview: RunOverview;
  judges: JudgeBreakdown[];
  top_failures: FailurePattern[];
  coverage: CoverageMetric[];
  engine_version: string;
  asset_versions_used: string[];
  run_status: string;
  generated_at: string;
  data_source_version: string;
  artifact_generated_at: string;
}
