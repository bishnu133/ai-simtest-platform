"""
Policy-as-Code Module v2 — Enterprise-grade compliance governance.

Evaluate AI chatbot behavior against YAML-defined policy rules with:
- Evidence-aware evaluation at conversation and turn level
- Rule scoping by persona, scenario, tag, workflow, source
- Content matching (must_contain, must_not_contain, disclaimers, citations)
- Statistical conditions (percentile scores, stddev, regression delta)
- Cross-module validation (workflow, RAG, tool evaluation results)
- Composite rules (all_of, any_of, not, conditional)
- Multiple gate modes (strict, severity_aware, weighted, threshold, soft)
- Control family organization for enterprise reporting
- Rich evidence trails with sample failures and remediation guidance
- Full evidence export for enterprise audits

Usage:
    from src.policy import PolicyLoader, PolicyEngine

    # Load built-in template
    policy_set = PolicyLoader.load_built_in("healthcare")

    # Or load from YAML file
    policy_set = PolicyLoader.load_from_file("my_policy.yaml")

    # Evaluate against simulation results
    engine = PolicyEngine(policy_set)
    scorecard = engine.evaluate(report_data)

    # Check compliance
    print(f"Compliant: {scorecard.overall_compliant}")
    print(f"Score: {scorecard.compliance_score}%")
    print(f"Gate mode: {scorecard.gate_mode}")
    print(f"Evidence items: {scorecard.total_evidence_items}")

    # Full evidence export for audit
    audit_data = scorecard.to_full_evidence_dict()
"""

from .built_in import BUILT_IN_POLICIES
from .engine import PolicyEngine
from .loader import PolicyLoadError, PolicyLoader
from .models import (
    ComplianceGateMode,
    ComplianceResult,
    ComplianceScorecard,
    ContentMatch,
    CompositeRule,
    ControlFamily,
    EvaluationErrorMode,
    FailureEvidence,
    MissingDataMode,
    PolicyCondition,
    PolicyRule,
    PolicySet,
    PolicySeverity,
    RuleScope,
    RuleStatus,
)

__all__ = [
    # Core
    "PolicyEngine",
    "PolicyLoader",
    "PolicyLoadError",
    # Models
    "PolicyRule",
    "PolicySet",
    "PolicyCondition",
    "PolicySeverity",
    "ComplianceResult",
    "ComplianceScorecard",
    # v2 models
    "ComplianceGateMode",
    "ControlFamily",
    "EvaluationErrorMode",
    "MissingDataMode",
    "RuleScope",
    "RuleStatus",
    "ContentMatch",
    "CompositeRule",
    "FailureEvidence",
    # Built-in templates
    "BUILT_IN_POLICIES",
]
