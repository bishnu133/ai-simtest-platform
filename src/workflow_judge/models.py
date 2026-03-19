"""
Functional / Workflow Judge Models v2 — With review improvements.

V2 additions: Severity enum, MatchType, ConditionStatus, OrderMode,
EvaluationMode, activation_hints, TurnViolation, critical_failure fields,
weight normalization, workflow applicability.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


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
    model_config = {"extra": "allow"}

    @property
    def severity_enum(self) -> RuleSeverity:
        try: return RuleSeverity(self.severity.lower())
        except ValueError: return RuleSeverity.HIGH

    @property
    def match_type_enum(self) -> MatchType:
        try: return MatchType(self.match_type.lower())
        except ValueError: return MatchType.PHRASE


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


class StepResult(BaseModel):
    step_id: str
    step_name: str
    status: WorkflowStepStatus
    confidence: float = Field(default=0.0)
    evidence: str = Field(default="")
    required: bool = Field(default=True)


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
    passed: bool = Field(default=False)
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

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "workflow": self.workflow_name, "domain": self.domain,
            "passed": self.passed, "score": round(self.score, 3),
            "step_score": round(self.step_score, 3), "rule_score": round(self.rule_score, 3),
            "condition_score": round(self.condition_score, 3), "order_score": round(self.order_score, 3),
            "completed_steps": self.completed_steps, "missed_steps": self.missed_steps,
            "violations": self.violations, "reasoning": self.reasoning,
            "evaluation_mode": self.evaluation_mode, "critical_failure": self.critical_failure,
            "critical_failures_count": self.critical_failures_count, "workflow_applicable": self.workflow_applicable,
        }
