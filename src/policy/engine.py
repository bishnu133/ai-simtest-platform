"""
Policy Engine — Evaluates simulation results against policy rules.

The PolicyEngine takes:
1. A PolicySet (loaded from YAML, built-in template, or dict)
2. A SimulationReport (from the judge engine)

And produces a ComplianceScorecard showing pass/fail per rule.

This is the core evaluation logic that maps policy conditions to judge data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import (
    ComplianceResult,
    ComplianceScorecard,
    PolicyCondition,
    PolicyRule,
    PolicySet,
    PolicySeverity,
)


class PolicyEngine:
    """
    Evaluates simulation results against a set of policy rules.

    Usage:
        engine = PolicyEngine(policy_set)
        scorecard = engine.evaluate(simulation_report)
    """

    def __init__(self, policy_set: PolicySet):
        self.policy_set = policy_set

    def evaluate(self, report_data: Dict[str, Any]) -> ComplianceScorecard:
        """
        Evaluate a simulation report against all policy rules.

        Args:
            report_data: Dictionary with simulation results. Expected keys:
                - score_by_judge: Dict[str, float] — average score per judge
                - summary: Dict with pass_rate, critical_failures, warnings, total_turns
                - judged_conversations: List of judged conversation dicts (optional)
                - failure_patterns: List of failure pattern dicts (optional)

                This can come from:
                - SimulationReport.model_dump() / .dict()
                - A loaded summary JSON file
                - A manually constructed dict for testing

        Returns:
            ComplianceScorecard with per-rule results.
        """
        results: List[ComplianceResult] = []

        for rule in self.policy_set.rules:
            result = self._evaluate_rule(rule, report_data)
            results.append(result)

        # Calculate aggregates
        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed)
        total = len(results)

        critical_violations = [
            r for r in results
            if not r.passed and r.severity == PolicySeverity.CRITICAL
        ]

        # Overall compliant = no critical violations
        overall_compliant = len(critical_violations) == 0

        # Compliance score = percentage of rules passed
        compliance_score = (passed_count / total * 100.0) if total > 0 else 0.0

        # Generate summary
        summary = self._generate_summary(
            total, passed_count, failed_count, critical_violations, overall_compliant
        )

        return ComplianceScorecard(
            policy_set_name=self.policy_set.name,
            policy_set_version=self.policy_set.version,
            total_rules=total,
            passed_rules=passed_count,
            failed_rules=failed_count,
            results=results,
            overall_compliant=overall_compliant,
            compliance_score=round(compliance_score, 1),
            critical_violations=critical_violations,
            summary=summary,
        )

    def _evaluate_rule(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Evaluate a single policy rule against report data."""

        condition = rule.condition

        if condition == PolicyCondition.MIN_SCORE:
            return self._eval_min_score(rule, report_data)
        elif condition == PolicyCondition.MAX_FAILURE_RATE:
            return self._eval_max_failure_rate(rule, report_data)
        elif condition == PolicyCondition.ZERO_CRITICAL:
            return self._eval_zero_critical(rule, report_data)
        elif condition == PolicyCondition.MAX_CRITICAL_COUNT:
            return self._eval_max_critical_count(rule, report_data)
        elif condition == PolicyCondition.MIN_PASS_RATE:
            return self._eval_min_pass_rate(rule, report_data)
        elif condition == PolicyCondition.MAX_WARNINGS:
            return self._eval_max_warnings(rule, report_data)
        elif condition == PolicyCondition.CUSTOM:
            return self._eval_custom(rule, report_data)
        else:
            # Unknown condition — mark as failed with explanation
            return ComplianceResult(
                rule_id=rule.id,
                rule_name=rule.name,
                passed=False,
                severity=rule.severity,
                actual_value=0.0,
                threshold=rule.threshold,
                condition=rule.condition,
                judge=rule.judge,
                message=f"Unknown condition '{condition.value}' — cannot evaluate",
            )

    def _eval_min_score(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Check: judge average score >= threshold."""
        score = self._get_judge_score(rule.judge, report_data)
        passed = score >= rule.threshold

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=round(score, 4),
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message=(
                f"✅ {rule.judge} score {score:.2%} >= {rule.threshold:.2%}"
                if passed
                else f"❌ {rule.judge} score {score:.2%} < {rule.threshold:.2%}"
            ),
        )

    def _eval_max_failure_rate(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Check: judge failure rate <= threshold."""
        score = self._get_judge_score(rule.judge, report_data)
        # Failure rate = 1 - score (assuming score is pass rate for that judge)
        failure_rate = 1.0 - score
        passed = failure_rate <= rule.threshold

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=round(failure_rate, 4),
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message=(
                f"✅ {rule.judge} failure rate {failure_rate:.2%} <= {rule.threshold:.2%}"
                if passed
                else f"❌ {rule.judge} failure rate {failure_rate:.2%} > {rule.threshold:.2%}"
            ),
        )

    def _eval_zero_critical(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Check: zero critical failures."""
        summary = report_data.get("summary", {})
        if isinstance(summary, dict):
            critical_count = summary.get("critical_failures", 0)
        else:
            # Handle SimulationReport object with .summary attribute
            critical_count = getattr(summary, "critical_failures", 0)

        passed = critical_count == 0

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=float(critical_count),
            threshold=0.0,
            condition=rule.condition,
            judge=rule.judge,
            message=(
                "✅ Zero critical failures"
                if passed
                else f"❌ {critical_count} critical failure(s) found"
            ),
        )

    def _eval_max_critical_count(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Check: critical failure count <= threshold."""
        summary = report_data.get("summary", {})
        if isinstance(summary, dict):
            critical_count = summary.get("critical_failures", 0)
        else:
            critical_count = getattr(summary, "critical_failures", 0)

        passed = critical_count <= rule.threshold

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=float(critical_count),
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message=(
                f"✅ {critical_count} critical failures <= {int(rule.threshold)}"
                if passed
                else f"❌ {critical_count} critical failures > {int(rule.threshold)}"
            ),
        )

    def _eval_min_pass_rate(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Check: overall pass rate >= threshold."""
        if rule.judge == "overall":
            summary = report_data.get("summary", {})
            if isinstance(summary, dict):
                pass_rate = summary.get("pass_rate", 0.0)
            else:
                pass_rate = getattr(summary, "pass_rate", 0.0)
        else:
            # Per-judge pass rate approximation from score
            pass_rate = self._get_judge_score(rule.judge, report_data)

        passed = pass_rate >= rule.threshold

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=round(pass_rate, 4),
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message=(
                f"✅ Pass rate {pass_rate:.2%} >= {rule.threshold:.2%}"
                if passed
                else f"❌ Pass rate {pass_rate:.2%} < {rule.threshold:.2%}"
            ),
        )

    def _eval_max_warnings(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Check: warning count <= threshold."""
        summary = report_data.get("summary", {})
        if isinstance(summary, dict):
            warning_count = summary.get("warnings", 0)
        else:
            warning_count = getattr(summary, "warnings", 0)

        passed = warning_count <= rule.threshold

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=float(warning_count),
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message=(
                f"✅ {warning_count} warnings <= {int(rule.threshold)}"
                if passed
                else f"❌ {warning_count} warnings > {int(rule.threshold)}"
            ),
        )

    def _eval_custom(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """
        Custom condition evaluation — placeholder for extensibility.

        Users can subclass PolicyEngine and override this method for custom logic.
        """
        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=True,  # Default: pass custom rules (override to implement)
            severity=rule.severity,
            actual_value=0.0,
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message="Custom rule — no evaluation logic defined (pass by default)",
        )

    def _get_judge_score(self, judge_name: str, report_data: Dict[str, Any]) -> float:
        """
        Get the average score for a judge from report data.

        Handles both dict and object access patterns.
        """
        # For 'overall', use the summary average_score
        if judge_name == "overall":
            summary = report_data.get("summary", {})
            if isinstance(summary, dict):
                return float(summary.get("average_score", 0.0))
            return float(getattr(summary, "average_score", 0.0))

        # Per-judge scores
        score_by_judge = report_data.get("score_by_judge", {})
        if isinstance(score_by_judge, dict):
            return float(score_by_judge.get(judge_name, 0.0))

        return 0.0

    def _generate_summary(
        self,
        total: int,
        passed: int,
        failed: int,
        critical_violations: List[ComplianceResult],
        overall_compliant: bool,
    ) -> str:
        """Generate a human-readable compliance summary."""
        parts = []

        if overall_compliant:
            parts.append(f"✅ COMPLIANT — {passed}/{total} rules passed")
        else:
            parts.append(f"❌ NON-COMPLIANT — {failed}/{total} rules failed")

        if critical_violations:
            crit_names = [v.rule_name for v in critical_violations]
            parts.append(f"Critical violations: {', '.join(crit_names)}")

        pct = (passed / total * 100) if total > 0 else 0
        parts.append(f"Compliance score: {pct:.0f}%")

        return ". ".join(parts)
