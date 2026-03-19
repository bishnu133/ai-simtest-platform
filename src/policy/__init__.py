"""
Policy-as-Code Module — Define compliance policies in YAML, evaluate against judge results.

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
"""

from .built_in import BUILT_IN_POLICIES
from .engine import PolicyEngine
from .loader import PolicyLoadError, PolicyLoader
from .models import (
    ComplianceResult,
    ComplianceScorecard,
    PolicyCondition,
    PolicyRule,
    PolicySet,
    PolicySeverity,
)

__all__ = [
    "PolicyEngine",
    "PolicyLoader",
    "PolicyLoadError",
    "PolicyRule",
    "PolicySet",
    "PolicyCondition",
    "PolicySeverity",
    "ComplianceResult",
    "ComplianceScorecard",
    "BUILT_IN_POLICIES",
]
