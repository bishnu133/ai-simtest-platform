"""
Multi-Model Comparison — Data Models.

All Pydantic v2 models for P4 #20 Multi-Model Comparison.
Covers: config, identity, execution results, analysis, cost,
forensics, decision profiles, statistical verdicts, governance.

Phase 1 of 7 in implementation plan.
"""

from __future__ import annotations

import hashlib
import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class AdapterType(str, enum.Enum):
    """Supported provider adapter types."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GENERIC = "generic"
    AUTO = "auto"


class ComparisonMode(str, enum.Enum):
    """Multi-compare execution mode."""
    CHAMPION_CHALLENGER = "champion_challenger"
    HEAD_TO_HEAD = "head_to_head"


class Dimension(str, enum.Enum):
    """Evaluation dimensions for ranking."""
    OVERALL_PASS_RATE = "overall_pass_rate"
    GROUNDING = "grounding"
    SAFETY = "safety"
    QUALITY = "quality"
    RELEVANCE = "relevance"
    POLICY_COMPLIANCE = "policy_compliance"
    WORKFLOW_COMPLETION = "workflow_completion"
    COST_EFFICIENCY = "cost_efficiency"
    RESPONSE_LATENCY = "response_latency"
    CRITICAL_FAILURE_RATE = "critical_failure_rate"


# Inverted dimensions: lower is better
INVERTED_DIMENSIONS = frozenset({
    Dimension.RESPONSE_LATENCY,
    Dimension.CRITICAL_FAILURE_RATE,
})


class StatisticalVerdictLabel(str, enum.Enum):
    """Statistical comparison verdict."""
    CLEAR_WINNER = "clear_winner"
    LIKELY_WINNER = "likely_winner"
    LEANING = "leaning"
    NO_CLEAR_WINNER = "no_clear_winner"
    INSUFFICIENT_DATA = "insufficient_data"


class EvaluatorType(str, enum.Enum):
    """Source type for judge evaluations."""
    HEURISTIC = "heuristic"
    EMBEDDING = "embedding"
    LLM_AS_JUDGE = "llm_as_judge"
    POLICY_ENGINE = "policy_engine"
    RULE_ENGINE = "rule_engine"


class ModelRunStatus(str, enum.Enum):
    """Status of a single model's execution."""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BUDGET_TERMINATED = "budget_terminated"


class ErrorCategory(str, enum.Enum):
    """Normalized error categories from adapters."""
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    MALFORMED_RESPONSE = "malformed_response"
    CONNECTION_ERROR = "connection_error"
    UNKNOWN = "unknown"


class BudgetRisk(str, enum.Enum):
    """Budget risk level."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RootCauseBucket(str, enum.Enum):
    """Root-cause categories for failure forensics."""
    HALLUCINATION = "hallucination"
    RETRIEVAL_FAILURE = "retrieval_failure"
    POLICY_VIOLATION = "policy_violation"
    SAFETY_BREACH = "safety_breach"
    INSTRUCTION_FOLLOWING_FAILURE = "instruction_following_failure"
    WORKFLOW_BREAK = "workflow_break"
    TOOL_CALLING_FAILURE = "tool_calling_failure"
    TIMEOUT_INFRA_ERROR = "timeout_infra_error"
    FORMATTING_SCHEMA_BREAK = "formatting_schema_break"
    INCONSISTENCY = "inconsistency"


class CheckpointStatus(str, enum.Enum):
    """Status of a checkpoint-based run."""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPLETED_WITH_RESUME = "completed_with_resume"
    PARTIAL = "partial"


class GateVerdict(str, enum.Enum):
    """CI/CD gate result."""
    PASS = "pass"
    FAIL = "fail"


# ─────────────────────────────────────────────────────────────
# Identity Models (3-tier hierarchy)
# ─────────────────────────────────────────────────────────────

class ConversationKey(BaseModel):
    """
    Tier 1: Conversation-level identity.

    Uniquely identifies one conversation for one persona under
    one scenario in one model's run. This is the primary
    cross-model matching key.
    """
    persona_id: str = Field(..., description="From shared persona set (e.g., p001)")
    scenario_id: str = Field(default="none", description="Scenario slug or 'none'")
    conversation_index: int = Field(default=0, ge=0, description="Index if multiple conversations per persona")

    def to_string(self) -> str:
        """Canonical string form for matching and export."""
        return f"{self.persona_id}:{self.scenario_id}:{self.conversation_index}"

    def __hash__(self) -> int:
        return hash(self.to_string())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConversationKey):
            return NotImplemented
        return self.to_string() == other.to_string()

    model_config = {"frozen": True}


class TurnKey(BaseModel):
    """
    Tier 2: Turn-level identity.

    Uniquely identifies one turn within one conversation.
    Used for turn-level diffs across models.
    """
    conversation_key: ConversationKey
    turn_index: int = Field(..., ge=0, description="0-based position in conversation")
    speaker: str = Field(..., pattern="^(user|bot)$", description="Who spoke this turn")

    def to_string(self) -> str:
        """Canonical string form."""
        return f"{self.conversation_key.to_string()}:t{self.turn_index:02d}:{self.speaker}"

    def __hash__(self) -> int:
        return hash(self.to_string())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TurnKey):
            return NotImplemented
        return self.to_string() == other.to_string()

    model_config = {"frozen": True}


class TrialKey(BaseModel):
    """
    Tier 3: Trial-level identity (for future repeated runs).

    Differentiates repeated trials of the same conversation.
    """
    conversation_key: ConversationKey
    trial_number: int = Field(default=1, ge=1, description="1-based trial number")

    def to_string(self) -> str:
        return f"{self.conversation_key.to_string()}:trial_{self.trial_number}"

    model_config = {"frozen": True}


# ─────────────────────────────────────────────────────────────
# Configuration Models
# ─────────────────────────────────────────────────────────────

class ModelSpec(BaseModel):
    """
    Single bot endpoint definition for comparison.

    Mirrors BotConfig but adds id, name, adapter, and tags
    for comparison tracking. api_key is resolved at runtime
    from api_key_env and EXCLUDED from serialization.
    """
    id: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]+$",
                    description="Unique slug for this model (e.g., 'gpt4o-prod')")
    name: str = Field(..., min_length=1, max_length=128,
                      description="Display name (e.g., 'GPT-4o Production')")
    endpoint: str = Field(..., min_length=1, description="API endpoint URL")

    @model_validator(mode="after")
    def validate_endpoint_url(self) -> "ModelSpec":
        """Validate endpoint looks like a URL."""
        ep = self.endpoint
        if not (ep.startswith("http://") or ep.startswith("https://")):
            raise ValueError(
                f"Model '{self.id}': endpoint must start with http:// or https:// "
                f"(got: '{ep}'). Provide a full URL."
            )
        return self
    adapter: AdapterType = Field(default=AdapterType.AUTO,
                                 description="Provider adapter: openai, anthropic, generic, auto")
    api_key_env: str | None = Field(default=None,
                                    description="Environment variable name containing the API key")
    api_key: str | None = Field(default=None, exclude=True,
                                description="Resolved API key (NEVER serialized)")
    headers: dict[str, str] = Field(default_factory=dict,
                                    description="Extra HTTP headers")
    request_format: str | None = Field(default=None,
                                       description="Request format hint for generic adapter")
    response_path: str | None = Field(default=None,
                                      description="JSONPath to bot reply text for generic adapter")
    token_usage_path: str | None = Field(default=None,
                                         description="JSONPath to token usage for generic adapter")
    request_template: dict[str, Any] | None = Field(default=None,
                                                     description="Full request template for generic adapter")
    timeout_seconds: int = Field(default=30, ge=5, le=300)
    verify_ssl: bool = Field(default=True)
    tags: list[str] = Field(default_factory=list,
                            description="Arbitrary tags for grouping/filtering")
    metadata: dict[str, Any] = Field(default_factory=dict,
                                     description="Arbitrary metadata")

    model_config = {"frozen": False}  # api_key set at runtime


class ComparisonSettings(BaseModel):
    """
    Shared simulation settings applied uniformly to all models.

    Mirrors the key simtest run flags.
    """
    personas: int = Field(default=10, ge=2, le=100)
    max_turns: int = Field(default=8, ge=3, le=50)
    min_turns: int = Field(default=1, ge=1)
    scenarios: list[str] | None = Field(default=None,
                                        description="Scenario template IDs to apply")
    policy: str | None = Field(default=None,
                                description="Policy template name or YAML path")
    workflow: str | None = Field(default=None,
                                  description="Workflow template names or 'all'")
    parallel: int = Field(default=3, ge=1, le=20,
                          description="Per-model conversation concurrency")
    budget_limit: float | None = Field(default=None, ge=0,
                                        description="Max USD cost PER MODEL")
    budget_mode: str = Field(default="soft", pattern="^(soft|hard)$")
    stress_memory: bool = Field(default=False)
    rag_eval: bool = Field(default=False)
    expand_failures: bool = Field(default=False)
    documentation: str | None = Field(default=None,
                                      description="Inline documentation text")
    doc_file: str | None = Field(default=None,
                                  description="Path to documentation file")
    doc_dir: str | None = Field(default=None,
                                 description="Path to documentation directory")
    pass_threshold: float = Field(default=0.7, ge=0, le=1)
    warn_threshold: float = Field(default=0.5, ge=0, le=1)
    signature: bool = Field(default=True)
    auto_approve: bool = Field(default=False)


class DecisionProfile(BaseModel):
    """
    Named, auditable weighting scheme for business decisions.

    Separates raw metrics from business recommendations.
    """
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    weights: dict[str, float] = Field(
        ..., description="Dimension name -> weight (must sum to ~1.0)"
    )
    is_built_in: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_weights(self) -> "DecisionProfile":
        total = sum(self.weights.values())
        if not (0.95 <= total <= 1.05):
            raise ValueError(
                f"Decision profile '{self.name}' weights must sum to ~1.0 "
                f"(got {total:.3f})"
            )
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("All weights must be non-negative")
        return self


# Built-in decision profiles
BUILT_IN_PROFILES: dict[str, DecisionProfile] = {
    "balanced": DecisionProfile(
        name="balanced",
        description="All dimensions equally weighted",
        weights={d.value: 1.0 / len(Dimension) for d in Dimension},
        is_built_in=True,
    ),
    "safety_first": DecisionProfile(
        name="safety_first",
        description="Healthcare, finance, regulated industries",
        weights={
            "safety": 0.35, "policy_compliance": 0.25, "quality": 0.15,
            "grounding": 0.10, "overall_pass_rate": 0.05, "relevance": 0.03,
            "workflow_completion": 0.03, "cost_efficiency": 0.02,
            "response_latency": 0.01, "critical_failure_rate": 0.01,
        },
        is_built_in=True,
    ),
    "quality_first": DecisionProfile(
        name="quality_first",
        description="Customer-facing chatbots, content generation",
        weights={
            "quality": 0.30, "relevance": 0.25, "grounding": 0.20,
            "safety": 0.10, "overall_pass_rate": 0.05, "policy_compliance": 0.03,
            "workflow_completion": 0.03, "cost_efficiency": 0.02,
            "response_latency": 0.01, "critical_failure_rate": 0.01,
        },
        is_built_in=True,
    ),
    "cost_optimized": DecisionProfile(
        name="cost_optimized",
        description="High-volume, cost-sensitive deployments",
        weights={
            "cost_efficiency": 0.35, "quality": 0.20, "safety": 0.15,
            "overall_pass_rate": 0.15, "relevance": 0.05, "grounding": 0.03,
            "policy_compliance": 0.03, "workflow_completion": 0.02,
            "response_latency": 0.01, "critical_failure_rate": 0.01,
        },
        is_built_in=True,
    ),
    "compliance_first": DecisionProfile(
        name="compliance_first",
        description="Enterprise compliance, audit-heavy environments",
        weights={
            "policy_compliance": 0.30, "safety": 0.25, "workflow_completion": 0.20,
            "grounding": 0.10, "quality": 0.05, "overall_pass_rate": 0.03,
            "relevance": 0.03, "cost_efficiency": 0.02,
            "response_latency": 0.01, "critical_failure_rate": 0.01,
        },
        is_built_in=True,
    ),
    "low_latency": DecisionProfile(
        name="low_latency",
        description="Real-time applications, user-facing APIs",
        weights={
            "response_latency": 0.30, "quality": 0.20, "overall_pass_rate": 0.15,
            "cost_efficiency": 0.15, "safety": 0.05, "relevance": 0.05,
            "grounding": 0.03, "policy_compliance": 0.03,
            "workflow_completion": 0.02, "critical_failure_rate": 0.02,
        },
        is_built_in=True,
    ),
}


class MultiCompareConfig(BaseModel):
    """
    Top-level configuration parsed from models.yaml.

    This is the entry point for all multi-model comparison runs.
    """
    name: str = Field(default="Multi-Model Comparison")
    description: str = Field(default="")
    mode: ComparisonMode = Field(default=ComparisonMode.CHAMPION_CHALLENGER)
    champion: str | None = Field(default=None,
                                  description="Model ID of the champion (baseline)")
    models: list[ModelSpec] = Field(..., min_length=2, max_length=10)
    settings: ComparisonSettings = Field(default_factory=ComparisonSettings)
    decision_profile: str = Field(default="balanced",
                                   description="Built-in profile name or 'custom'")
    decision_weights: dict[str, float] | None = Field(
        default=None, description="Custom weights if decision_profile='custom'"
    )
    output_dir: str = Field(default="./reports/multi_compare")
    allowed_endpoints: list[str] | None = Field(
        default=None, description="Glob patterns for endpoint allowlist"
    )
    version: str = Field(default="1.0")

    @model_validator(mode="after")
    def validate_config(self) -> "MultiCompareConfig":
        # Check unique model IDs
        ids = [m.id for m in self.models]
        if len(ids) != len(set(ids)):
            dupes = [mid for mid in ids if ids.count(mid) > 1]
            raise ValueError(f"Duplicate model IDs: {set(dupes)}")

        # Validate champion reference
        if self.mode == ComparisonMode.CHAMPION_CHALLENGER:
            if not self.champion:
                raise ValueError(
                    "champion_challenger mode requires 'champion' field "
                    "with a valid model ID"
                )
            if self.champion not in ids:
                raise ValueError(
                    f"Champion '{self.champion}' not found in models. "
                    f"Available IDs: {ids}"
                )

        # Validate custom decision weights
        if self.decision_profile == "custom":
            if not self.decision_weights:
                raise ValueError(
                    "decision_profile='custom' requires 'decision_weights' "
                    "with dimension->weight mapping"
                )

        return self

    def get_decision_profile(self) -> DecisionProfile:
        """
        Resolve the active decision profile.

        Raises ValueError if profile name is unknown — no silent fallback.
        Enterprise principle: unknown profiles must fail loudly to prevent
        unnoticed changes in business decision weighting.
        """
        if self.decision_profile == "custom" and self.decision_weights:
            return DecisionProfile(
                name="custom",
                description="User-defined weights",
                weights=self.decision_weights,
                is_built_in=False,
            )
        profile = BUILT_IN_PROFILES.get(self.decision_profile)
        if profile is None:
            available = ", ".join(sorted(BUILT_IN_PROFILES.keys()))
            raise ValueError(
                f"Unknown decision profile: '{self.decision_profile}'. "
                f"Available profiles: {available}, or use 'custom' with "
                f"'decision_weights' mapping."
            )
        return profile


# ─────────────────────────────────────────────────────────────
# Adapter Response Models
# ─────────────────────────────────────────────────────────────

class NormalizedError(BaseModel):
    """Standardized error from any provider."""
    category: ErrorCategory
    message: str = ""
    raw_status_code: int | None = None
    retryable: bool = False

    model_config = {"frozen": True}


class CanonicalBotResponse(BaseModel):
    """
    Normalized response from any provider adapter.

    This is the standard output consumed by all downstream analysis.
    """
    content: str = Field(default="", description="Parsed bot response text")
    raw_response: dict[str, Any] = Field(
        default_factory=dict, exclude=True,
        description="Full HTTP response body (debug only, never in reports)"
    )
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    status_code: int = Field(default=200)
    error: NormalizedError | None = None
    provider_name: str = Field(default="unknown")
    model_identifier: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific extras: tool calls, citations, finish_reason"
    )

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is not None and self.output_tokens is not None:
            return self.input_tokens + self.output_tokens
        return None


# ─────────────────────────────────────────────────────────────
# Execution Result Models
# ─────────────────────────────────────────────────────────────

class ModelRunResult(BaseModel):
    """
    Captures the full output from one model's simulation run.

    One instance per model in the comparison.
    """
    model_id: str
    model_name: str
    status: ModelRunStatus = ModelRunStatus.SUCCESS
    # These are optional because a failed model won't have them
    report: Any | None = Field(default=None, description="SimulationReport object")
    summary_dict: dict[str, Any] = Field(
        default_factory=dict,
        description="Serialized ReportSummary for cross-model analysis"
    )
    cost_report_dict: dict[str, Any] | None = Field(default=None)
    execution_time_seconds: float = 0.0
    error: str | None = None
    conversation_keys: list[str] = Field(
        default_factory=list,
        description="ConversationKey.to_string() values completed"
    )
    adapter_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated adapter stats: total_tokens, avg_latency, error_count"
    )
    completed_at: str | None = None

    def is_usable(self) -> bool:
        """Whether this result can be included in analysis."""
        return self.status in (ModelRunStatus.SUCCESS, ModelRunStatus.BUDGET_TERMINATED)


# ─────────────────────────────────────────────────────────────
# Statistical Models
# ─────────────────────────────────────────────────────────────

class StatisticalVerdict(BaseModel):
    """Statistical comparison result for one dimension."""
    verdict: StatisticalVerdictLabel
    method: str = Field(
        default="bootstrap_ci",
        description="bootstrap_ci or wilcoxon_paired"
    )
    confidence_interval: tuple[float, float] | None = Field(
        default=None, description="95% CI of the mean score"
    )
    p_value: float | None = Field(default=None, description="For Wilcoxon test")
    effect_size: float | None = Field(default=None, description="Median delta for paired test")
    sample_size: int = 0
    practical_significance: str = Field(
        default="", description="E.g., 'negligible', 'meaningful', 'large'"
    )

    model_config = {"frozen": True}


class DimensionScore(BaseModel):
    """One model's score on one evaluation dimension."""
    dimension: Dimension
    score: float = 0.0
    rank: int = 0
    is_winner: bool = False
    delta_from_champion: float | None = None
    confidence_interval: tuple[float, float] | None = None
    consistency_score: float | None = Field(
        default=None, description="1.0 - min(CV, 1.0). Higher = more stable"
    )
    statistical_verdict: StatisticalVerdict | None = None
    is_inverted: bool = Field(default=False, description="True if lower = better")


class ModelRanking(BaseModel):
    """Complete ranking for one model across all dimensions."""
    model_id: str
    model_name: str
    dimensions: list[DimensionScore] = Field(default_factory=list)
    overall_rank: int = 0
    overall_score: float = 0.0
    profile_weighted_rank: int | None = None
    profile_weighted_score: float | None = None
    deployment_ready: bool = True
    deployment_blockers: list[str] = Field(default_factory=list)
    variance_flag: bool = Field(
        default=False, description="True if CV > 0.3 on any dimension"
    )


# ─────────────────────────────────────────────────────────────
# Coverage Parity
# ─────────────────────────────────────────────────────────────

class CoverageParityReport(BaseModel):
    """Coverage parity check for one model."""
    model_id: str
    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    skipped_cases: int = 0
    budget_terminated_cases: int = 0
    completion_rate: float = 0.0
    parity_warning: str | None = None


# ─────────────────────────────────────────────────────────────
# Forensics Models
# ─────────────────────────────────────────────────────────────

class RootCause(BaseModel):
    """One root-cause finding from failure forensics."""
    bucket: RootCauseBucket
    judge_name: str = ""
    turn_index: int | None = None
    evidence: str = ""
    severity: str = "medium"


class FailureForensic(BaseModel):
    """Forensic analysis of one failed conversation."""
    conversation_key: str = Field(..., description="ConversationKey.to_string()")
    model_id: str
    first_bad_turn: int | None = Field(
        default=None, description="0-based index of first turn below pass_threshold"
    )
    root_causes: list[RootCause] = Field(default_factory=list)
    regression_vs_champion: bool = False
    affected_dimensions: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Cost Models
# ─────────────────────────────────────────────────────────────

class CostEfficiency(BaseModel):
    """Cost-quality tradeoff metrics for one model."""
    model_id: str
    total_cost: float = 0.0
    cost_per_conversation: float = 0.0
    cost_per_successful_conversation: float | None = None
    cost_per_grounded_answer: float | None = None
    cost_per_policy_compliant_run: float | None = None
    cost_per_workflow_complete: float | None = None
    quality_per_dollar: float | None = None
    token_efficiency: float | None = Field(
        default=None, description="Total tokens / num conversations"
    )
    cost_rank: int = 0


class CostGovernance(BaseModel):
    """Budget governance for one model."""
    model_id: str
    total_cost: float = 0.0
    budget_limit: float | None = None
    budget_utilization: float | None = None
    budget_risk: BudgetRisk = BudgetRisk.LOW
    budget_exceeded: bool = False
    exceeded_at_turn: int | None = None


# ─────────────────────────────────────────────────────────────
# Diff Explanations
# ─────────────────────────────────────────────────────────────

class DiffExplanation(BaseModel):
    """Plain-English comparison of challenger vs champion."""
    challenger_id: str
    vs_champion_id: str
    summary: str = ""
    wins: list[str] = Field(default_factory=list)
    losses: list[str] = Field(default_factory=list)
    neutral: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Comparison Matrix (top-level analysis result)
# ─────────────────────────────────────────────────────────────

class SliceResult(BaseModel):
    """Comparison result for one analysis slice (e.g., by scenario)."""
    slice_dimension: str = Field(description="E.g., 'scenario', 'persona_type', 'policy_rule'")
    slice_value: str = Field(description="E.g., 'prompt_injection', 'adversarial'")
    winner_model_id: str | None = None
    model_scores: dict[str, float] = Field(default_factory=dict)


class ComparisonMatrix(BaseModel):
    """
    The full N-way comparison result.

    Top-level output from the analysis pipeline.
    """
    rankings: list[ModelRanking] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    winner_per_dimension: dict[str, str | None] = Field(default_factory=dict)
    overall_winner: str | None = None
    champion_id: str | None = None
    profile_applied: str = "balanced"
    per_profile_rankings: dict[str, list[ModelRanking]] | None = None
    slice_analysis: list[SliceResult] = Field(default_factory=list)
    coverage_parity: list[CoverageParityReport] = Field(default_factory=list)
    diff_explanations: list[DiffExplanation] = Field(default_factory=list)
    manifest_id: str | None = None
    statistical_method: str = "bootstrap_ci"


# ─────────────────────────────────────────────────────────────
# Run Manifest (reproducibility)
# ─────────────────────────────────────────────────────────────

class EvaluatorVersions(BaseModel):
    """Pinned versions of all evaluators used."""
    grounding: dict[str, str] = Field(
        default_factory=lambda: {"model": "all-MiniLM-L6-v2", "threshold": "0.35"}
    )
    safety: dict[str, str] = Field(
        default_factory=lambda: {"presidio": "unknown", "detoxify": "unknown"}
    )
    quality: dict[str, str] = Field(
        default_factory=lambda: {"llm_model": "unknown", "prompt_hash": "unknown"}
    )
    relevance: dict[str, str] = Field(
        default_factory=lambda: {"mode": "keyword"}
    )


class RunManifest(BaseModel):
    """
    Complete snapshot of everything needed to reproduce
    or audit a comparison run.
    """
    manifest_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    config_hash: str = Field(default="", description="SHA-256 of models.yaml content")
    persona_set_id: str = Field(default="", description="SHA-256 of serialized personas")
    scenario_assignment_id: str = Field(
        default="", description="SHA-256 of persona-to-scenario mapping"
    )
    documentation_hash: str = Field(default="")
    policy_version: str | None = None
    workflow_version: str | None = None
    evaluator_versions: EvaluatorVersions = Field(default_factory=EvaluatorVersions)
    model_specs_redacted: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Model specs with api_key removed"
    )
    simtest_version: str = Field(default="1.3.0")
    python_version: str = Field(default="")
    timestamp_start: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z"
    )
    timestamp_end: str | None = None
    random_seed: int | None = None

    @staticmethod
    def compute_hash(data: str) -> str:
        """SHA-256 hash of a string."""
        return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────
# Checkpoint State
# ─────────────────────────────────────────────────────────────

class CheckpointState(BaseModel):
    """
    Persistent state for checkpoint/resume.

    Saved to .checkpoint/run_state.json.
    """
    manifest_id: str
    config_hash: str
    persona_set_id: str
    status: CheckpointStatus = CheckpointStatus.IN_PROGRESS
    models_completed: list[str] = Field(default_factory=list)
    models_failed: list[str] = Field(default_factory=list)
    models_pending: list[str] = Field(default_factory=list)
    shared_personas_path: str = ""
    case_assignments_path: str = ""
    output_dir: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z"
    )
    resume_count: int = 0
    resume_timestamps: dict[str, str] = Field(
        default_factory=dict,
        description="model_id -> ISO timestamp when it was completed"
    )
    simtest_version: str = Field(default="1.3.0")


# ─────────────────────────────────────────────────────────────
# Gate Models
# ─────────────────────────────────────────────────────────────

class GateCheck(BaseModel):
    """One CI/CD gate check result."""
    name: str
    passed: bool
    reason: str = ""
    threshold: float | None = None
    actual_value: float | None = None


class GateResult(BaseModel):
    """Aggregate CI/CD gate result."""
    verdict: GateVerdict = GateVerdict.PASS
    checks: list[GateCheck] = Field(default_factory=list)
    champion_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == GateVerdict.PASS


# ─────────────────────────────────────────────────────────────
# Top-Level Report
# ─────────────────────────────────────────────────────────────

class MultiCompareReport(BaseModel):
    """
    Top-level frozen report object for multi-model comparison.

    This is the final output that feeds the HTML dashboard,
    comparison.json, and comparison_audit.json.
    """
    config_name: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z"
    )
    models_compared: list[str] = Field(default_factory=list)
    shared_persona_count: int = 0
    comparison_matrix: ComparisonMatrix = Field(default_factory=ComparisonMatrix)
    cost_analysis: list[CostEfficiency] = Field(default_factory=list)
    cost_governance: list[CostGovernance] = Field(default_factory=list)
    forensics: list[FailureForensic] = Field(default_factory=list)
    per_model_summaries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    decision_profiles_applied: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    gate_result: GateResult = Field(default_factory=GateResult)
    run_manifest_id: str | None = None
    resumed_run: bool = False
    resume_info: dict[str, Any] | None = None
    security_redaction_applied: bool = True
    evaluator_agreement_rates: dict[str, float] = Field(
        default_factory=dict,
        description="Per-model average judge agreement rate"
    )
