"""
Policy-as-Code Models — Data structures for compliance policy evaluation.

Policies are defined as YAML rules that map to judges. Each rule specifies:
- Which judge to check (grounding, safety, quality, relevance)
- What condition to evaluate (min_score, max_failures, zero_critical, etc.)
- What threshold to apply
- What severity to assign on violation

A PolicySet groups related rules (e.g., "HIPAA compliance", "airline safety").
The ComplianceScorecard aggregates results across all rules for a simulation run.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PolicySeverity(str, Enum):
    """Severity level for policy violations."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PolicyCondition(str, Enum):
    """
    Supported conditions for policy rules.

    Each condition defines how to evaluate judge results against a threshold.
    """
    MIN_SCORE = "min_score"                  # Judge average score >= threshold
    MAX_FAILURE_RATE = "max_failure_rate"     # Judge failure rate <= threshold (0-1)
    ZERO_CRITICAL = "zero_critical"          # Zero critical failures (threshold ignored)
    MAX_CRITICAL_COUNT = "max_critical_count" # Critical failure count <= threshold
    MIN_PASS_RATE = "min_pass_rate"          # Overall pass rate >= threshold (0-1)
    MAX_WARNINGS = "max_warnings"            # Warning count <= threshold
    CUSTOM = "custom"                        # Custom evaluation logic (for extensibility)


class PolicyRule(BaseModel):
    """
    A single policy rule that maps a condition to a judge.

    Example YAML:
        - id: safety_no_pii
          name: "No PII Leakage"
          description: "Bot must never expose personal information"
          judge: safety
          condition: zero_critical
          severity: critical
    """
    id: str = Field(..., description="Unique rule identifier")
    name: str = Field(..., description="Human-readable rule name")
    description: str = Field(default="", description="Detailed rule description")
    judge: str = Field(..., description="Judge to evaluate (grounding, safety, quality, relevance, or 'overall')")
    condition: PolicyCondition = Field(..., description="Condition type to check")
    threshold: float = Field(default=0.0, description="Threshold value for the condition")
    severity: PolicySeverity = Field(default=PolicySeverity.HIGH, description="Severity if violated")
    tags: List[str] = Field(default_factory=list, description="Optional tags for filtering")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional rule metadata")

    model_config = {"extra": "allow"}


class PolicySet(BaseModel):
    """
    A named collection of policy rules.

    Example YAML:
        name: "Healthcare Compliance"
        version: "1.0"
        description: "HIPAA-aligned policies for healthcare chatbots"
        rules:
          - id: safety_no_pii
            name: "No PII Leakage"
            ...
    """
    name: str = Field(..., description="Policy set name")
    version: str = Field(default="1.0", description="Policy set version")
    description: str = Field(default="", description="Policy set description")
    industry: str = Field(default="general", description="Target industry")
    rules: List[PolicyRule] = Field(default_factory=list, description="List of policy rules")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    model_config = {"extra": "allow"}

    @property
    def rule_count(self) -> int:
        return len(self.rules)

    @property
    def critical_rules(self) -> List[PolicyRule]:
        return [r for r in self.rules if r.severity == PolicySeverity.CRITICAL]

    def get_rule(self, rule_id: str) -> Optional[PolicyRule]:
        """Get a rule by ID."""
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def get_rules_for_judge(self, judge_name: str) -> List[PolicyRule]:
        """Get all rules that apply to a specific judge."""
        return [r for r in self.rules if r.judge == judge_name]


class ComplianceResult(BaseModel):
    """
    Result of evaluating a single policy rule against simulation data.
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


class ComplianceScorecard(BaseModel):
    """
    Aggregated compliance results for a full simulation run.

    This is the primary output of the Policy-as-Code engine — a complete
    compliance report showing pass/fail for every policy rule.
    """
    policy_set_name: str = Field(..., description="Name of the policy set evaluated")
    policy_set_version: str = Field(default="1.0", description="Version of the policy set")
    total_rules: int = Field(default=0, description="Total number of rules evaluated")
    passed_rules: int = Field(default=0, description="Number of rules that passed")
    failed_rules: int = Field(default=0, description="Number of rules that failed")
    results: List[ComplianceResult] = Field(default_factory=list, description="Per-rule results")
    overall_compliant: bool = Field(default=False, description="True if all critical rules pass")
    compliance_score: float = Field(default=0.0, description="Percentage of rules passed (0-100)")
    critical_violations: List[ComplianceResult] = Field(default_factory=list, description="Critical rule failures")
    summary: str = Field(default="", description="Human-readable summary")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    @property
    def has_critical_violations(self) -> bool:
        return len(self.critical_violations) > 0

    @property
    def violation_count_by_severity(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for result in self.results:
            if not result.passed:
                sev = result.severity.value
                counts[sev] = counts.get(sev, 0) + 1
        return counts

    def get_failed_results(self) -> List[ComplianceResult]:
        return [r for r in self.results if not r.passed]

    def get_passed_results(self) -> List[ComplianceResult]:
        return [r for r in self.results if r.passed]

    def to_summary_dict(self) -> Dict[str, Any]:
        """Export scorecard as a summary dictionary for JSON export."""
        return {
            "policy_set": self.policy_set_name,
            "version": self.policy_set_version,
            "overall_compliant": self.overall_compliant,
            "compliance_score": self.compliance_score,
            "total_rules": self.total_rules,
            "passed": self.passed_rules,
            "failed": self.failed_rules,
            "critical_violations": len(self.critical_violations),
            "violations_by_severity": self.violation_count_by_severity,
            "results": [
                {
                    "rule_id": r.rule_id,
                    "rule_name": r.rule_name,
                    "passed": r.passed,
                    "severity": r.severity.value,
                    "actual": r.actual_value,
                    "threshold": r.threshold,
                    "message": r.message,
                }
                for r in self.results
            ],
        }
