"""
Functional / Workflow Judge Module v2 — Evaluate business workflow correctness.

V2 improvements: match_type (phrase/whole_word/regex), step order validation,
condition partial status, severity enum, evaluation mode tracking, per-turn
violation evidence, activation hints, weight normalization, critical failure fields.
"""
from .built_in import BUILT_IN_WORKFLOWS
from .judge import FunctionalJudge
from .llm_evaluator import LLMWorkflowEvaluator
from .loader import WorkflowLoadError, WorkflowLoader
from .models import (
    ConditionResult, ConditionStatus, EvaluationMode, HardRule, HardRuleResult,
    HardRuleType, MatchType, OrderMode, RuleSeverity, StepResult, SuccessCondition,
    TurnViolation, WorkflowDefinition, WorkflowResult, WorkflowStep, WorkflowStepStatus,
)
from .rule_engine import RuleEngine

__all__ = [
    "FunctionalJudge", "WorkflowLoader", "WorkflowLoadError", "RuleEngine",
    "LLMWorkflowEvaluator", "WorkflowDefinition", "WorkflowStep", "WorkflowStepStatus",
    "HardRule", "HardRuleType", "HardRuleResult", "SuccessCondition", "ConditionResult",
    "ConditionStatus", "StepResult", "WorkflowResult", "TurnViolation",
    "RuleSeverity", "MatchType", "OrderMode", "EvaluationMode", "BUILT_IN_WORKFLOWS",
]
