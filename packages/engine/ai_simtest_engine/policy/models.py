"""
Policy-as-Code Models v2 — Enterprise-grade compliance policy evaluation.

Major upgrades from v1:
- Evidence-aware rules with conversation/turn-level evaluation
- Rule scoping (applies_to) for personas, scenarios, tags, workflows, sources
- Control families for enterprise-grade organization
- Composite rules (all_of, any_of, not, conditional)
- Multiple compliance gate modes (strict, severity_aware, weighted, threshold, soft)
- Richer ComplianceResult with evidence trails, remediation hints, sample failures
- Content matching rules (must_contain, must_not_contain, requires_disclaimer, etc.)
- Percentile and statistical conditions (p95, stddev, regression delta)
- First-class support for workflow, RAG, and tool evaluation results

Backward-compatible: v1 YAML files still load and work correctly.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ─── Enums ──────────────────────────────────────────────────


class PolicySeverity(str, Enum):
    """Severity level for policy violations."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PolicyCondition(str, Enum):
    """
    Supported conditions for policy rules.

    v1 conditions (backward-compatible):
    - MIN_SCORE, MAX_FAILURE_RATE, ZERO_CRITICAL, MAX_CRITICAL_COUNT,
      MIN_PASS_RATE, MAX_WARNINGS, CUSTOM

    v2 additions:
    - Evidence-level: MAX_FAILED_CONVERSATIONS, MAX_FAILED_TURNS, MAX_MATCHING_FAILURES
    - Statistical: MIN_SCORE_PERCENTILE, MAX_SCORE_STDDEV, MAX_REGRESSION_DELTA
    - Cross-module: REQUIRED_WORKFLOW_PASS_RATE, REQUIRED_RAG_METRIC, REQUIRED_TOOL_METRIC
    - Content: MUST_CONTAIN, MUST_NOT_CONTAIN, REQUIRES_DISCLAIMER,
               REQUIRES_CITATION, REQUIRES_ESCALATION, REQUIRES_REFUSAL
    """
    # ── v1 conditions (unchanged) ──
    MIN_SCORE = "min_score"
    MAX_FAILURE_RATE = "max_failure_rate"
    ZERO_CRITICAL = "zero_critical"
    MAX_CRITICAL_COUNT = "max_critical_count"
    MIN_PASS_RATE = "min_pass_rate"
    MAX_WARNINGS = "max_warnings"
    CUSTOM = "custom"

    # ── v2: Evidence-level conditions ──
    MAX_FAILED_CONVERSATIONS = "max_failed_conversations"
    MAX_FAILED_TURNS = "max_failed_turns"
    MAX_MATCHING_FAILURES = "max_matching_failures"

    # ── v2: Statistical conditions ──
    MIN_SCORE_PERCENTILE = "min_score_percentile"
    MAX_LOWER_TAIL_VIOLATIONS = "max_lower_tail_violations"
    MAX_SCORE_STDDEV = "max_score_stddev"
    MAX_REGRESSION_DELTA = "max_regression_delta"

    # ── v2: Cross-module conditions ──
    REQUIRED_WORKFLOW_PASS_RATE = "required_workflow_pass_rate"
    REQUIRED_RAG_METRIC = "required_rag_metric"
    REQUIRED_TOOL_METRIC = "required_tool_metric"

    # ── v2: Content-matching conditions ──
    MUST_CONTAIN = "must_contain"
    MUST_NOT_CONTAIN = "must_not_contain"
    REQUIRES_DISCLAIMER = "requires_disclaimer"
    REQUIRES_CITATION = "requires_citation"
    REQUIRES_ESCALATION = "requires_escalation"
    REQUIRES_REFUSAL = "requires_refusal"


class ComplianceGateMode(str, Enum):
    """
    How overall_compliant is determined.

    - critical_only: (v1 default) non-compliant only if critical rules fail
    - strict: ANY failed rule = non-compliant
    - severity_aware: any critical or high fail = non-compliant
    - weighted: compliance_score must exceed compliance_threshold AND no criticals
    - threshold: compliance_score must exceed compliance_threshold (criticals ignored)
    - soft: allow limited medium/low failures (up to max_tolerated_failures)
    """
    CRITICAL_ONLY = "critical_only"
    STRICT = "strict"
    SEVERITY_AWARE = "severity_aware"
    WEIGHTED = "weighted"
    THRESHOLD = "threshold"
    SOFT = "soft"


class ControlFamily(str, Enum):
    """
    Control families for organizing rules into enterprise categories.
    Used for grouping in reports and dashboards.
    """
    SAFETY = "safety"
    PRIVACY = "privacy"
    GROUNDING = "grounding"
    QUALITY = "quality"
    RELEVANCE = "relevance"
    WORKFLOW = "workflow"
    TOOL_USAGE = "tool_usage"
    RETRIEVAL = "retrieval"
    FORMATTING = "formatting"
    COMPLIANCE = "compliance"
    BRAND = "brand"
    ESCALATION = "escalation"
    STABILITY = "stability"
    CUSTOM = "custom"


class EvaluationErrorMode(str, Enum):
    """What to do when a custom/unknown condition can't be evaluated."""
    FAIL_CLOSED = "fail_closed"       # Mark as FAILED (enterprise default)
    PASS_OPEN = "pass_open"           # Mark as PASSED (v1 legacy behavior)
    NOT_EVALUATED = "not_evaluated"   # Mark as skipped — doesn't affect compliance


class MissingDataMode(str, Enum):
    """
    What to do when a rule has no data to evaluate (no matching conversations,
    no baseline, no workflow results, etc.).

    Configurable per-rule via on_no_data or per-policy via default_no_data_mode.
    """
    PASS = "pass"     # Treat as passed (v2.0 default — lenient)
    FAIL = "fail"     # Treat as failed (strict — enterprise)
    SKIP = "skip"     # Mark as not_evaluated — doesn't affect compliance score


class RuleStatus(str, Enum):
    """
    Explicit rule evaluation status for enterprise reporting.
    Makes composite and conditional logic debuggable.
    """
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"                           # on_no_data=skip or custom not_evaluated
    NOT_APPLICABLE = "not_applicable"             # scope matched nothing + skip
    NO_DATA = "no_data"                           # no data available, handled by on_no_data
    CONDITIONAL_NOT_TRIGGERED = "conditional_not_triggered"  # conditional when_rule was false
    ERROR = "error"                               # evaluation error


# ─── Rule Scoping ───────────────────────────────────────────


class RuleScope(BaseModel):
    """
    Defines which subset of simulation data a rule applies to.

    If omitted, rule applies to the entire run (v1 behavior).
    If specified, the engine filters conversations/turns before evaluating.

    Example YAML:
        applies_to:
          persona_types: ["frustrated_customer", "technical_expert"]
          scenarios: ["prompt_injection", "emotional_escalation"]
          tags: ["pii", "security"]
          workflows: ["banking_account_opening"]
          sources: ["replay", "synthetic"]
          turn_range: [1, 10]
    """
    persona_types: List[str] = Field(default_factory=list, description="Filter by persona type names")
    scenarios: List[str] = Field(default_factory=list, description="Filter by scenario template names")
    tags: List[str] = Field(default_factory=list, description="Filter by conversation/turn tags")
    workflows: List[str] = Field(default_factory=list, description="Filter by workflow names")
    sources: List[str] = Field(default_factory=list, description="Filter by data source: synthetic, replay, imported, regression")
    turn_range: Optional[List[int]] = Field(default=None, description="Filter turns by index range [start, end] inclusive")
    conversation_filter: Optional[str] = Field(default=None, description="Filter expression for conversation metadata")

    @property
    def is_empty(self) -> bool:
        """True if no scoping is defined (applies to all data)."""
        return (
            not self.persona_types
            and not self.scenarios
            and not self.tags
            and not self.workflows
            and not self.sources
            and self.turn_range is None
            and self.conversation_filter is None
        )


# ─── Content Matching ───────────────────────────────────────


class ContentMatch(BaseModel):
    """
    Content matching configuration for must_contain / must_not_contain rules.

    Example YAML:
        match:
          patterns: ["not financial advice", "consult a professional"]
          issue_tags: ["pii", "account_number"]
          regex: "\\b\\d{4}[- ]?\\d{4}[- ]?\\d{4}[- ]?\\d{4}\\b"
          field: "bot_response"
          case_sensitive: false
    """
    patterns: List[str] = Field(default_factory=list, description="Text patterns to match")
    issue_tags: List[str] = Field(default_factory=list, description="Judge issue tags to match")
    regex: Optional[str] = Field(default=None, description="Regex pattern to match")
    field: str = Field(default="bot_response", description="Which field to search: bot_response, user_message, full_conversation")
    case_sensitive: bool = Field(default=False, description="Whether matching is case-sensitive")

    def matches_text(self, text: str) -> bool:
        """Check if any pattern matches the given text."""
        check_text = text if self.case_sensitive else text.lower()

        for pattern in self.patterns:
            check_pattern = pattern if self.case_sensitive else pattern.lower()
            if check_pattern in check_text:
                return True

        if self.regex:
            flags = 0 if self.case_sensitive else re.IGNORECASE
            if re.search(self.regex, text, flags):
                return True

        return False

    def matches_issues(self, issues: List[str]) -> bool:
        """Check if any issue tag matches."""
        if not self.issue_tags:
            return False
        issue_set = {i.lower() for i in issues}
        return any(t.lower() in issue_set for t in self.issue_tags)


# ─── Composite Rules ────────────────────────────────────────


class CompositeRule(BaseModel):
    """
    Composite rule for nested logic: all_of, any_of, not, conditional.

    Example YAML:
        composite:
          type: all_of
          rule_ids: ["safety_no_pii", "safety_min_score"]

        composite:
          type: conditional
          when_rule: "is_finance_conversation"
          then_rules: ["finance_disclaimer", "finance_grounding"]
    """
    type: str = Field(..., description="Composition type: all_of, any_of, not, conditional")
    rule_ids: List[str] = Field(default_factory=list, description="Rule IDs to compose")
    when_rule: Optional[str] = Field(default=None, description="For conditional: trigger rule ID")
    then_rules: List[str] = Field(default_factory=list, description="For conditional: rules to apply if trigger passes")


# ─── Policy Rule (v2) ───────────────────────────────────────


class PolicyRule(BaseModel):
    """
    A single policy rule — now with scoping, content matching, and composition.

    Backward-compatible with v1 YAML. New v2 fields are optional.

    Example v2 YAML:
        - id: pii_leak_zero_tolerance
          name: "Zero PII Leaks"
          judge: safety
          condition: max_matching_failures
          threshold: 0
          severity: critical
          control_family: privacy
          applies_to:
            tags: ["pii", "security"]
            sources: ["replay", "synthetic"]
          match:
            issue_tags: ["pii", "personal_information", "account_number"]
          evidence:
            include_samples: true
            max_samples: 5
          remediation: "Review PII detection rules and add redaction patterns."
    """
    id: str = Field(..., description="Unique rule identifier")
    name: str = Field(..., description="Human-readable rule name")
    description: str = Field(default="", description="Detailed rule description")
    judge: str = Field(..., description="Judge to evaluate: grounding, safety, quality, relevance, overall, workflow, rag, tool")
    condition: PolicyCondition = Field(..., description="Condition type to check")
    threshold: float = Field(default=0.0, description="Threshold value for the condition")
    severity: PolicySeverity = Field(default=PolicySeverity.HIGH, description="Severity if violated")
    tags: List[str] = Field(default_factory=list, description="Optional tags for filtering")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional rule metadata")

    # ── v2 additions ──
    control_family: Optional[str] = Field(default=None, description="Control family category for grouping")
    applies_to: Optional[RuleScope] = Field(default=None, description="Scope filter — which data this rule evaluates")
    match: Optional[ContentMatch] = Field(default=None, description="Content matching config for content-based conditions")
    composite: Optional[CompositeRule] = Field(default=None, description="Composite rule logic")
    remediation: str = Field(default="", description="Remediation guidance shown on failure")
    evidence_config: Dict[str, Any] = Field(default_factory=dict, description="Evidence collection settings")

    # Statistical condition parameters
    percentile: Optional[int] = Field(default=None, description="For min_score_percentile: which percentile (e.g., 95)")
    metric_name: Optional[str] = Field(default=None, description="For required_rag_metric / required_tool_metric: metric name")
    workflow_name: Optional[str] = Field(default=None, description="For required_workflow_pass_rate: specific workflow")

    # v2.1: Missing data behavior
    on_no_data: Optional[str] = Field(default=None, description="Behavior when no data matches: pass | fail | skip (overrides policy default)")

    model_config = {"extra": "allow"}


# ─── Policy Set (v2) ────────────────────────────────────────


class PolicySet(BaseModel):
    """
    A named collection of policy rules — now with gate modes, controls, and settings.

    Example v2 YAML:
        name: "Finance Assistant Policy"
        version: "2.1"
        mode: severity_aware
        compliance_threshold: 0.95
        evaluation_error_mode: fail_closed
        max_tolerated_failures: 3
        controls:
          - id: privacy
            name: Privacy Protection
            severity: critical
            rules: [...]
        rules:
          - id: overall_pass_rate
            ...
    """
    name: str = Field(..., description="Policy set name")
    version: str = Field(default="1.0", description="Policy set version")
    description: str = Field(default="", description="Policy set description")
    industry: str = Field(default="general", description="Target industry")
    rules: List[PolicyRule] = Field(default_factory=list, description="List of policy rules")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    # ── v2 additions ──
    mode: ComplianceGateMode = Field(default=ComplianceGateMode.CRITICAL_ONLY, description="How overall compliance is determined")
    compliance_threshold: float = Field(default=0.0, description="For weighted/threshold modes: minimum compliance score (0-100)")
    evaluation_error_mode: EvaluationErrorMode = Field(
        default=EvaluationErrorMode.FAIL_CLOSED,
        description="What to do with unevaluable custom rules"
    )
    max_tolerated_failures: int = Field(default=0, description="For soft mode: max non-critical failures allowed")
    default_no_data_mode: str = Field(default="pass", description="Default on_no_data for rules: pass | fail | skip")
    max_evidence_per_rule: int = Field(default=10, description="Max evidence samples per rule (performance control for large runs)")
    controls: List[Dict[str, Any]] = Field(default_factory=list, description="Control family definitions with nested rules")

    model_config = {"extra": "allow"}

    @property
    def rule_count(self) -> int:
        return len(self.rules)

    @property
    def critical_rules(self) -> List[PolicyRule]:
        return [r for r in self.rules if r.severity == PolicySeverity.CRITICAL]

    @property
    def all_rules_flat(self) -> List[PolicyRule]:
        """Get all rules including those nested inside controls."""
        return list(self.rules)

    def get_rule(self, rule_id: str) -> Optional[PolicyRule]:
        """Get a rule by ID."""
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def get_rules_for_judge(self, judge_name: str) -> List[PolicyRule]:
        """Get all rules that apply to a specific judge."""
        return [r for r in self.rules if r.judge == judge_name]

    def get_rules_by_control_family(self, family: str) -> List[PolicyRule]:
        """Get all rules in a specific control family."""
        return [r for r in self.rules if r.control_family == family]

    def get_control_families(self) -> List[str]:
        """Get unique control family names used in this policy set."""
        families = set()
        for r in self.rules:
            if r.control_family:
                families.add(r.control_family)
        return sorted(families)


# ─── Evidence Trail ──────────────────────────────────────────


class FailureEvidence(BaseModel):
    """
    Evidence for a single failure instance — links to specific conversation/turn.
    """
    conversation_id: str = Field(default="", description="ID of the conversation where failure occurred")
    persona_name: str = Field(default="", description="Persona involved")
    turn_index: Optional[int] = Field(default=None, description="Turn number within conversation")
    judge_name: str = Field(default="", description="Judge that flagged the failure")
    score: float = Field(default=0.0, description="Score at point of failure")
    issue: str = Field(default="", description="Issue description from judge")
    bot_response_snippet: str = Field(default="", description="Truncated bot response for context")


# ─── Compliance Result (v2) ──────────────────────────────────


class ComplianceResult(BaseModel):
    """
    Result of evaluating a single policy rule — now with evidence trails.
    """
    rule_id: str = Field(..., description="The policy rule ID that was evaluated")
    rule_name: str = Field(..., description="Human-readable rule name")
    passed: bool = Field(..., description="Whether the rule passed")
    severity: PolicySeverity = Field(..., description="Severity of violation (if failed)")
    actual_value: float = Field(..., description="The actual measured value")
    threshold: float = Field(..., description="The threshold that was checked")
    condition: PolicyCondition = Field(..., description="The condition that was evaluated")
    judge: str = Field(..., description="The judge that was checked")
    message: str = Field(default="", description="Human-readable result message")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional details")

    # ── v2 additions ──
    control_family: Optional[str] = Field(default=None, description="Control family this result belongs to")
    evidence: List[FailureEvidence] = Field(default_factory=list, description="Sample failure evidence")
    evaluated_count: int = Field(default=0, description="Number of conversations/turns evaluated (after scoping)")
    failed_count: int = Field(default=0, description="Number that failed this specific rule")
    scope_applied: Optional[str] = Field(default=None, description="Description of scope filter applied")
    remediation: str = Field(default="", description="Remediation guidance")
    not_evaluated: bool = Field(default=False, description="True if rule was skipped (e.g., custom with no evaluator)")
    rule_status: str = Field(default="passed", description="Explicit rule state: passed, failed, skipped, not_applicable, no_data, conditional_not_triggered, error")


# ─── Compliance Scorecard (v2) ───────────────────────────────


class ComplianceScorecard(BaseModel):
    """
    Aggregated compliance results — now with gate mode, control family breakdown,
    and richer export capabilities.
    """
    policy_set_name: str = Field(..., description="Name of the policy set evaluated")
    policy_set_version: str = Field(default="1.0", description="Version of the policy set")
    total_rules: int = Field(default=0, description="Total number of rules evaluated")
    passed_rules: int = Field(default=0, description="Number of rules that passed")
    failed_rules: int = Field(default=0, description="Number of rules that failed")
    skipped_rules: int = Field(default=0, description="Number of rules not evaluated")
    results: List[ComplianceResult] = Field(default_factory=list, description="Per-rule results")
    overall_compliant: bool = Field(default=False, description="Final compliance verdict")
    compliance_score: float = Field(default=0.0, description="Percentage of rules passed (0-100)")
    critical_violations: List[ComplianceResult] = Field(default_factory=list, description="Critical rule failures")
    summary: str = Field(default="", description="Human-readable summary")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    # ── v2 additions ──
    gate_mode: str = Field(default="critical_only", description="Compliance gate mode used")
    control_family_results: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Pass/fail breakdown by control family"
    )
    high_violations: List[ComplianceResult] = Field(default_factory=list, description="High-severity rule failures")
    total_evidence_items: int = Field(default=0, description="Total evidence items collected")

    # ── v2.1: Provenance / audit fields ──
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Audit provenance: policy_hash, generated_at, evaluator_version, etc.")

    @property
    def has_critical_violations(self) -> bool:
        return len(self.critical_violations) > 0

    @property
    def has_high_violations(self) -> bool:
        return len(self.high_violations) > 0

    @property
    def violation_count_by_severity(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for result in self.results:
            if not result.passed and not result.not_evaluated:
                sev = result.severity.value
                counts[sev] = counts.get(sev, 0) + 1
        return counts

    def get_failed_results(self) -> List[ComplianceResult]:
        return [r for r in self.results if not r.passed and not r.not_evaluated]

    def get_passed_results(self) -> List[ComplianceResult]:
        return [r for r in self.results if r.passed]

    def get_skipped_results(self) -> List[ComplianceResult]:
        return [r for r in self.results if r.not_evaluated]

    def get_results_by_control_family(self, family: str) -> List[ComplianceResult]:
        return [r for r in self.results if r.control_family == family]

    def to_summary_dict(self) -> Dict[str, Any]:
        """Export scorecard as a summary dictionary for JSON export."""
        return {
            "policy_set": self.policy_set_name,
            "version": self.policy_set_version,
            "gate_mode": self.gate_mode,
            "overall_compliant": self.overall_compliant,
            "compliance_score": self.compliance_score,
            "total_rules": self.total_rules,
            "passed": self.passed_rules,
            "failed": self.failed_rules,
            "skipped": self.skipped_rules,
            "critical_violations": len(self.critical_violations),
            "high_violations": len(self.high_violations),
            "violations_by_severity": self.violation_count_by_severity,
            "control_families": self.control_family_results,
            "total_evidence_items": self.total_evidence_items,
            "provenance": self.provenance,
            "results": [
                {
                    "rule_id": r.rule_id,
                    "rule_name": r.rule_name,
                    "passed": r.passed,
                    "not_evaluated": r.not_evaluated,
                    "severity": r.severity.value,
                    "actual": r.actual_value,
                    "threshold": r.threshold,
                    "condition": r.condition.value,
                    "judge": r.judge,
                    "control_family": r.control_family,
                    "message": r.message,
                    "remediation": r.remediation,
                    "rule_status": r.rule_status,
                    "evaluated_count": r.evaluated_count,
                    "failed_count": r.failed_count,
                    "scope_applied": r.scope_applied,
                    "evidence_count": len(r.evidence),
                    "evidence": [
                        {
                            "conversation_id": e.conversation_id,
                            "persona": e.persona_name,
                            "turn": e.turn_index,
                            "judge": e.judge_name,
                            "score": e.score,
                            "issue": e.issue,
                        }
                        for e in r.evidence[:5]  # Cap evidence in summary export
                    ],
                }
                for r in self.results
            ],
        }

    def to_full_evidence_dict(self) -> Dict[str, Any]:
        """Export full evidence trail for enterprise audit — includes all samples."""
        base = self.to_summary_dict()
        # Override results with full evidence
        base["results"] = [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name,
                "passed": r.passed,
                "not_evaluated": r.not_evaluated,
                "severity": r.severity.value,
                "actual": r.actual_value,
                "threshold": r.threshold,
                "condition": r.condition.value,
                "judge": r.judge,
                "control_family": r.control_family,
                "message": r.message,
                "remediation": r.remediation,
                "evaluated_count": r.evaluated_count,
                "failed_count": r.failed_count,
                "scope_applied": r.scope_applied,
                "details": r.details,
                "evidence": [
                    {
                        "conversation_id": e.conversation_id,
                        "persona": e.persona_name,
                        "turn": e.turn_index,
                        "judge": e.judge_name,
                        "score": e.score,
                        "issue": e.issue,
                        "bot_response_snippet": e.bot_response_snippet,
                    }
                    for e in r.evidence
                ],
            }
            for r in self.results
        ]
        return base
