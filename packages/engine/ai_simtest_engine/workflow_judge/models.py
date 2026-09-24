"""
Functional / Workflow Judge Models v3 — Production-grade workflow evaluation.

V3 additions over v2:
- WorkflowStatus enum (PASSED/FAILED/SKIPPED_NOT_APPLICABLE/NEEDS_REVIEW)
- ScoreBreakdown model (full mathematical trace of scoring)
- StepEvidence model (turn-grounded step detection proof)
- BotTurn dataclass (indexed bot message for evaluation)
- FailureCategory enum (classify workflow failures)
- Applicability enforcement fields (skip_if_not_applicable, confidence, reason)
- Needs-review threshold and per-component confidence
- Placeholder adjudication fields (reviewer_verdict, reviewer_notes)
- Efficiency score (resolution quality)
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class WorkflowStepStatus(str, Enum):
    COMPLETED = "completed"
    MISSED = "missed"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

class RuleSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class MatchType(str, Enum):
    PHRASE = "phrase"
    WHOLE_WORD = "whole_word"
    REGEX = "regex"

class OrderMode(str, Enum):
    NONE = "none"
    SOFT = "soft"
    STRICT = "strict"

class ConditionStatus(str, Enum):
    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"

class EvaluationMode(str, Enum):
    LLM = "llm"
    FALLBACK_KEYWORD = "fallback_keyword"
    FALLBACK_TEXT = "fallback_text"

class HardRuleType(str, Enum):
    FORBIDDEN_PHRASE = "forbidden_phrase"
    REQUIRED_PHRASE = "required_phrase"
    FORBIDDEN_TOPIC = "forbidden_topic"
    REQUIRED_TOPIC = "required_topic"
    MAX_TURNS_TO_RESOLVE = "max_turns"
    MUST_ESCALATE = "must_escalate"
    MUST_NOT_ESCALATE = "must_not_escalate"

# V3: Workflow-level status (replaces bare boolean `passed`)
class WorkflowStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    NEEDS_REVIEW = "needs_review"

# V3: Failure taxonomy — classifies *why* a workflow failed
class FailureCategory(str, Enum):
    MISSED_STEP = "missed_step"
    UNSAFE_DATA_COLLECTION = "unsafe_data_collection"
    PREMATURE_RESOLUTION = "premature_resolution"
    WRONG_ORDER = "wrong_order"
    VAGUE_HANDOFF = "vague_handoff"
    RULE_VIOLATION = "rule_violation"
    INCOMPLETE_GUIDANCE = "incomplete_guidance"
    ESCALATION_FAILURE = "escalation_failure"

# V3: How a step was matched
class StepMatchMethod(str, Enum):
    LLM = "llm"
    KEYWORD = "keyword"
    RULE = "rule"
    HYBRID = "hybrid"
    UNMATCHED = "unmatched"

# V3: Topic evaluation mode for hard rules
class TopicEvalMode(str, Enum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"


# ─── V3: Turn-level data structures ──────────────────────────────────────────

@dataclass
class BotTurn:
    """Indexed bot message preserving original conversation position."""
    turn_index: int       # Position in the full conversation (0-based)
    message: str          # Bot's response text


class StepEvidence(BaseModel):
    """V3: Turn-grounded proof of step detection."""
    first_detected_turn: int = Field(default=-1, description="Turn index where step was first detected (-1 = not detected)")
    matched_by: str = Field(default="unmatched", description="How the step was matched: llm, keyword, rule, hybrid, unmatched")
    matched_evidence: str = Field(default="", description="Exact quote or description from the bot turn")


# ─── V3: Score transparency ──────────────────────────────────────────────────

class ScoreBreakdown(BaseModel):
    """V3: Full mathematical trace of how the final score was computed."""
    raw_step_score: float = Field(default=0.0)
    raw_rule_score: float = Field(default=0.0)
    raw_condition_score: float = Field(default=0.0)
    normalized_weights: List[float] = Field(default_factory=lambda: [0.5, 0.3, 0.2],
                                            description="[step_weight, rule_weight, condition_weight] after normalization")
    weighted_score: float = Field(default=0.0, description="step*sw + rule*rw + condition*cw before penalties")
    order_score: float = Field(default=1.0)
    order_penalty_applied: float = Field(default=0.0, description="Multiplier reduction from order penalty (0.0 = no penalty)")
    post_order_score: float = Field(default=0.0, description="Score after order penalty")
    critical_cap_applied: bool = Field(default=False)
    critical_cap_score: float = Field(default=0.0, description="Score after critical cap (if applied)")
    final_score: float = Field(default=0.0)
    pass_threshold: float = Field(default=0.7)
    threshold_met: bool = Field(default=False)


# ─── Core models (updated from v2) ───────────────────────────────────────────

class WorkflowStep(BaseModel):
    id: str = Field(..., description="Unique step identifier")
    name: str = Field(..., description="Human-readable step name")
    description: str = Field(default="")
    required: bool = Field(default=True)
    order: Optional[int] = Field(default=None, description="Expected order (None=any)")
    detection_hints: List[str] = Field(default_factory=list)
    model_config = {"extra": "allow"}


class HardRule(BaseModel):
    id: str = Field(...)
    name: str = Field(...)
    rule_type: HardRuleType = Field(...)
    value: str = Field(default="")
    values: List[str] = Field(default_factory=list)
    severity: str = Field(default="high")
    case_sensitive: bool = Field(default=False)
    match_type: str = Field(default="phrase", description="phrase, whole_word, or regex")
    description: str = Field(default="")
    # V3: optional semantic topic evaluation mode
    topic_eval_mode: str = Field(default="keyword", description="keyword or semantic (for topic rules)")
    model_config = {"extra": "allow"}

    @property
    def severity_enum(self) -> RuleSeverity:
        try: return RuleSeverity(self.severity.lower())
        except ValueError: return RuleSeverity.HIGH

    @property
    def match_type_enum(self) -> MatchType:
        try: return MatchType(self.match_type.lower())
        except ValueError: return MatchType.PHRASE

    @property
    def topic_eval_mode_enum(self) -> TopicEvalMode:
        try: return TopicEvalMode(self.topic_eval_mode.lower())
        except ValueError: return TopicEvalMode.KEYWORD


class SuccessCondition(BaseModel):
    id: str = Field(...)
    description: str = Field(...)
    required: bool = Field(default=True)
    model_config = {"extra": "allow"}


class WorkflowDefinition(BaseModel):
    id: str = Field(default="")
    name: str = Field(...)
    domain: str = Field(default="general")
    description: str = Field(default="")
    version: str = Field(default="1.0")
    steps: List[WorkflowStep] = Field(default_factory=list)
    hard_rules: List[HardRule] = Field(default_factory=list)
    success_conditions: List[SuccessCondition] = Field(default_factory=list)
    step_weight: float = Field(default=0.5)
    rule_weight: float = Field(default=0.3)
    condition_weight: float = Field(default=0.2)
    pass_threshold: float = Field(default=0.7)
    order_mode: str = Field(default="none", description="none, soft, or strict")
    activation_hints: List[str] = Field(default_factory=list, description="Keywords for applicability check")
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # V3: Applicability enforcement
    skip_if_not_applicable: bool = Field(default=True, description="Skip evaluation when workflow doesn't match conversation")
    # V3: Needs-review threshold
    needs_review_threshold: float = Field(default=0.5, description="Below this confidence → NEEDS_REVIEW status")
    model_config = {"extra": "allow"}

    @property
    def required_steps(self) -> List[WorkflowStep]: return [s for s in self.steps if s.required]
    @property
    def optional_steps(self) -> List[WorkflowStep]: return [s for s in self.steps if not s.required]
    @property
    def total_steps(self) -> int: return len(self.steps)
    @property
    def critical_rules(self) -> List[HardRule]: return [r for r in self.hard_rules if r.severity == "critical"]
    @property
    def order_mode_enum(self) -> OrderMode:
        try: return OrderMode(self.order_mode.lower())
        except ValueError: return OrderMode.NONE

    def normalized_weights(self) -> tuple:
        total = self.step_weight + self.rule_weight + self.condition_weight
        if total <= 0: return (0.5, 0.3, 0.2)
        return (self.step_weight / total, self.rule_weight / total, self.condition_weight / total)

    def is_applicable(self, conversation_text: str) -> bool:
        if not self.activation_hints: return True
        text_lower = conversation_text.lower()
        return any(h.lower() in text_lower for h in self.activation_hints)

    def applicability_detail(self, conversation_text: str) -> tuple:
        """V3: Return (applicable: bool, confidence: float, reason: str, matched_hints: list)."""
        if not self.activation_hints:
            return True, 1.0, "No activation hints defined — applies to all conversations", []
        text_lower = conversation_text.lower()
        matched = [h for h in self.activation_hints if h.lower() in text_lower]
        total = len(self.activation_hints)
        confidence = len(matched) / total if total > 0 else 0.0
        applicable = len(matched) > 0
        if applicable:
            reason = f"Matched {len(matched)}/{total} activation hints: {matched}"
        else:
            reason = f"No activation hints matched (checked: {self.activation_hints})"
        return applicable, round(confidence, 4), reason, matched


class StepResult(BaseModel):
    step_id: str
    step_name: str
    status: WorkflowStepStatus
    confidence: float = Field(default=0.0)
    evidence: str = Field(default="")
    required: bool = Field(default=True)
    # V3: Turn-grounded evidence
    evidence_detail: Optional[StepEvidence] = Field(default=None)


class TurnViolation(BaseModel):
    turn_index: int = Field(default=-1)
    turn_text: str = Field(default="")
    matched_phrase: str = Field(default="")


class HardRuleResult(BaseModel):
    rule_id: str
    rule_name: str
    passed: bool
    severity: str = "high"
    evidence: str = Field(default="")
    rule_type: str = ""
    turn_violations: List[TurnViolation] = Field(default_factory=list)


class ConditionResult(BaseModel):
    condition_id: str
    description: str
    met: bool  # backward compat
    status: str = Field(default="unmet", description="met, partial, or unmet")
    confidence: float = Field(default=0.0)
    reasoning: str = Field(default="")

    @property
    def score(self) -> float:
        if self.status == "met" or (self.status == "unmet" and self.met): return 1.0
        elif self.status == "partial": return 0.5
        return 0.0


class WorkflowResult(BaseModel):
    workflow_id: str = Field(default="")
    workflow_name: str = Field(default="")
    domain: str = Field(default="")
    # V3: status enum replaces bare boolean (passed kept for backward compat)
    passed: bool = Field(default=False)
    status: str = Field(default="failed", description="passed, failed, skipped_not_applicable, needs_review")
    score: float = Field(default=0.0)
    severity: str = Field(default="medium")
    step_score: float = Field(default=0.0)
    rule_score: float = Field(default=0.0)
    condition_score: float = Field(default=0.0)
    order_score: float = Field(default=1.0)
    step_results: List[StepResult] = Field(default_factory=list)
    rule_results: List[HardRuleResult] = Field(default_factory=list)
    condition_results: List[ConditionResult] = Field(default_factory=list)
    completed_steps: List[str] = Field(default_factory=list)
    missed_steps: List[str] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)
    reasoning: str = Field(default="")
    evaluation_mode: str = Field(default="fallback_keyword")
    critical_failure: bool = Field(default=False)
    critical_failures_count: int = Field(default=0)
    high_failures_count: int = Field(default=0)
    workflow_applicable: bool = Field(default=True)
    confidence_overall: float = Field(default=0.0)
    conversation_id: str = Field(default="")
    persona_name: str = Field(default="")
    total_turns: int = Field(default=0)
    # V3: Score transparency
    score_breakdown: Optional[ScoreBreakdown] = Field(default=None)
    # V3: Applicability detail
    applicability_confidence: float = Field(default=1.0)
    applicability_reason: str = Field(default="")
    # V3: Per-component confidence
    step_confidence: float = Field(default=0.0)
    rule_confidence: float = Field(default=1.0, description="Always 1.0 for deterministic rules")
    condition_confidence: float = Field(default=0.0)
    # V3: Failure taxonomy
    failure_categories: List[str] = Field(default_factory=list, description="List of FailureCategory values")
    # V3: Efficiency
    efficiency_score: float = Field(default=0.0, description="required_steps_completed / total_turns")
    # V3: Placeholder adjudication fields (for future human review)
    reviewer_verdict: Optional[str] = Field(default=None, description="Human reviewer override: passed/failed/needs_more_info")
    reviewer_notes: Optional[str] = Field(default=None, description="Human reviewer notes")

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "workflow": self.workflow_name, "domain": self.domain,
            "passed": self.passed, "status": self.status,
            "score": round(self.score, 3),
            "step_score": round(self.step_score, 3), "rule_score": round(self.rule_score, 3),
            "condition_score": round(self.condition_score, 3), "order_score": round(self.order_score, 3),
            "completed_steps": self.completed_steps, "missed_steps": self.missed_steps,
            "violations": self.violations, "reasoning": self.reasoning,
            "evaluation_mode": self.evaluation_mode, "critical_failure": self.critical_failure,
            "critical_failures_count": self.critical_failures_count,
            "workflow_applicable": self.workflow_applicable,
            "failure_categories": self.failure_categories,
            "efficiency_score": round(self.efficiency_score, 3),
        }
