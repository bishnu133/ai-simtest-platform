"""
Functional / Workflow Judge Module v3 — Production-grade workflow evaluation.

V3 improvements over v2:
- Applicability enforcement (skip_if_not_applicable, confidence, reason)
- Turn-grounded step evidence (BotTurn indexing, first_detected_turn)
- Bot-only keyword fallback (eliminates user-message false positives)
- Turn-grounded order scoring (uses actual conversation positions)
- Score transparency (ScoreBreakdown — full mathematical trace)
- Failure taxonomy (FailureCategory classification)
- WorkflowStatus enum (PASSED/FAILED/SKIPPED/NEEDS_REVIEW)
- Needs-review threshold (low-confidence → NEEDS_REVIEW)
- Semantic topic evaluation (optional LLM-based topic checks)
- Efficiency scoring (steps completed / turns used)
- Enhanced loader validation (duplicate IDs, regex check, order gaps, etc.)
- HTML report: score path, applicability badges, turn refs, failure tags
- Placeholder adjudication fields (reviewer_verdict, reviewer_notes)
"""
from .workflow_summary_panel import inject_workflow_summary_into_report
from .built_in import BUILT_IN_WORKFLOWS
from .judge import FunctionalJudge
from .llm_evaluator import LLMWorkflowEvaluator
from .loader import WorkflowLoadError, WorkflowLoader
from .models import (
    BotTurn, ConditionResult, ConditionStatus, EvaluationMode,
    FailureCategory, HardRule, HardRuleResult, HardRuleType,
    MatchType, OrderMode, RuleSeverity, ScoreBreakdown,
    StepEvidence, StepMatchMethod, StepResult, SuccessCondition,
    TopicEvalMode, TurnViolation, WorkflowDefinition, WorkflowResult,
    WorkflowStatus, WorkflowStep, WorkflowStepStatus,
)
from .rule_engine import RuleEngine

__all__ = [
    # Core
    "FunctionalJudge", "WorkflowLoader", "WorkflowLoadError", "RuleEngine",
    "LLMWorkflowEvaluator", "BUILT_IN_WORKFLOWS",
    # Definition models
    "WorkflowDefinition", "WorkflowStep", "HardRule", "SuccessCondition",
    # Result models
    "WorkflowResult", "StepResult", "HardRuleResult", "ConditionResult",
    "ScoreBreakdown", "StepEvidence", "TurnViolation",
    # Enums
    "WorkflowStepStatus", "WorkflowStatus", "HardRuleType", "RuleSeverity",
    "MatchType", "OrderMode", "ConditionStatus", "EvaluationMode",
    "FailureCategory", "StepMatchMethod", "TopicEvalMode",
    # Data structures
    "BotTurn",
    "inject_workflow_summary_into_report",
]
