"""
Policy Engine v2 — Evidence-aware compliance evaluation.

Major upgrades from v1:
- Conversation/turn-level rule evaluation (not just aggregates)
- Rule scoping: filter by persona, scenario, tags, workflow, source
- Content matching: must_contain, must_not_contain, disclaimer, citation, escalation, refusal
- Statistical conditions: percentile scores, stddev, regression delta
- Cross-module: workflow pass rates, RAG metrics, tool metrics
- Composite rules: all_of, any_of, not, conditional
- Multiple gate modes: strict, severity_aware, weighted, threshold, soft
- Fail-closed custom rules (configurable)
- Rich evidence trails with sample failures, remediation hints
- Separate score vs pass-rate tracking
- Full evidence export for enterprise audits

Backward-compatible: v1 report_data dicts still work with v1-style rules.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    ComplianceGateMode,
    ComplianceResult,
    ComplianceScorecard,
    ContentMatch,
    EvaluationErrorMode,
    FailureEvidence,
    PolicyCondition,
    PolicyRule,
    PolicySet,
    PolicySeverity,
    RuleScope,
)


class PolicyEngine:
    """
    Evaluates simulation results against a set of policy rules.

    v2 capabilities:
    - Evidence-level evaluation over judged_conversations
    - Scoped rules that filter by persona, scenario, tag, workflow
    - Content matching for response text inspection
    - Statistical analysis (percentiles, stddev)
    - Cross-module evaluation (workflow, RAG, tool results)
    - Composite rule logic (all_of, any_of, not, conditional)
    - Configurable gate modes for compliance determination
    """

    # Max evidence samples to collect per rule (default, overridden by policy_set.max_evidence_per_rule)
    MAX_EVIDENCE_SAMPLES = 10

    def __init__(self, policy_set: PolicySet):
        self.policy_set = policy_set
        self.MAX_EVIDENCE_SAMPLES = getattr(policy_set, 'max_evidence_per_rule', 10) or 10

    def evaluate(self, report_data: Dict[str, Any]) -> ComplianceScorecard:
        """
        Evaluate a simulation report against all policy rules.

        Args:
            report_data: Dictionary with simulation results. Expected keys:
                v1 (still supported):
                - score_by_judge: Dict[str, float]
                - summary: Dict with pass_rate, critical_failures, warnings, total_turns
                v2 (new):
                - judged_conversations: List[Dict] — per-conversation data with turns
                - workflow_results: List[Dict] — workflow judge outputs
                - rag_results: Dict — RAG evaluation metrics
                - tool_results: Dict — Tool evaluation metrics
                - expansion_results: Dict — Expansion analysis results
                - baseline: Dict — Previous run data for regression comparison
        """
        results: List[ComplianceResult] = []
        rule_results_map: Dict[str, ComplianceResult] = {}  # for composite lookups

        # First pass: evaluate all non-composite rules
        for rule in self.policy_set.rules:
            if rule.composite:
                continue  # Defer to second pass
            result = self._evaluate_rule(rule, report_data)
            results.append(result)
            rule_results_map[rule.id] = result

        # Second pass: evaluate composite rules
        for rule in self.policy_set.rules:
            if not rule.composite:
                continue
            result = self._evaluate_composite(rule, rule_results_map)
            results.append(result)
            rule_results_map[rule.id] = result

        # Calculate aggregates
        evaluated_results = [r for r in results if not r.not_evaluated]
        passed_count = sum(1 for r in evaluated_results if r.passed)
        failed_count = sum(1 for r in evaluated_results if not r.passed)
        skipped_count = sum(1 for r in results if r.not_evaluated)
        total = len(evaluated_results)

        critical_violations = [
            r for r in evaluated_results
            if not r.passed and r.severity == PolicySeverity.CRITICAL
        ]
        high_violations = [
            r for r in evaluated_results
            if not r.passed and r.severity == PolicySeverity.HIGH
        ]

        compliance_score = (passed_count / total * 100.0) if total > 0 else 0.0

        # Determine overall compliance based on gate mode
        overall_compliant = self._determine_compliance(
            passed_count, failed_count, total,
            critical_violations, high_violations,
            compliance_score,
        )

        # Build control family breakdown
        control_family_results = self._build_control_family_results(results)

        # Total evidence
        total_evidence = sum(len(r.evidence) for r in results)

        summary = self._generate_summary(
            total, passed_count, failed_count, skipped_count,
            critical_violations, high_violations, overall_compliant,
        )

        # Build provenance
        provenance = self._build_provenance()

        return ComplianceScorecard(
            policy_set_name=self.policy_set.name,
            policy_set_version=self.policy_set.version,
            total_rules=total,
            passed_rules=passed_count,
            failed_rules=failed_count,
            skipped_rules=skipped_count,
            results=results,
            overall_compliant=overall_compliant,
            compliance_score=round(compliance_score, 1),
            critical_violations=critical_violations,
            high_violations=high_violations,
            summary=summary,
            gate_mode=self.policy_set.mode.value,
            control_family_results=control_family_results,
            total_evidence_items=total_evidence,
            provenance=provenance,
        )

    # ─── Gate Mode Logic ─────────────────────────────────────

    def _determine_compliance(
        self,
        passed: int,
        failed: int,
        total: int,
        critical_violations: List[ComplianceResult],
        high_violations: List[ComplianceResult],
        compliance_score: float,
    ) -> bool:
        """Determine overall compliance based on the policy set's gate mode."""
        mode = self.policy_set.mode

        if mode == ComplianceGateMode.CRITICAL_ONLY:
            # v1 default: compliant if no critical violations
            return len(critical_violations) == 0

        elif mode == ComplianceGateMode.STRICT:
            # ANY failed rule = non-compliant
            return failed == 0

        elif mode == ComplianceGateMode.SEVERITY_AWARE:
            # Any critical or high fail = non-compliant
            return len(critical_violations) == 0 and len(high_violations) == 0

        elif mode == ComplianceGateMode.WEIGHTED:
            # Score must exceed threshold AND no criticals
            threshold = self.policy_set.compliance_threshold
            return (
                len(critical_violations) == 0
                and compliance_score >= threshold
            )

        elif mode == ComplianceGateMode.THRESHOLD:
            # Score must exceed threshold (criticals allowed)
            threshold = self.policy_set.compliance_threshold
            return compliance_score >= threshold

        elif mode == ComplianceGateMode.SOFT:
            # No criticals, and non-critical failures within tolerance
            if len(critical_violations) > 0:
                return False
            non_critical_failures = failed - len(critical_violations)
            return non_critical_failures <= self.policy_set.max_tolerated_failures

        # Fallback: critical-only
        return len(critical_violations) == 0

    # ─── Rule Evaluation Router ──────────────────────────────

    def _evaluate_rule(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> ComplianceResult:
        """Route rule to the appropriate evaluator."""
        condition = rule.condition

        # ── v1 conditions ──
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

        # ── v2: Evidence-level conditions ──
        elif condition == PolicyCondition.MAX_FAILED_CONVERSATIONS:
            return self._eval_max_failed_conversations(rule, report_data)
        elif condition == PolicyCondition.MAX_FAILED_TURNS:
            return self._eval_max_failed_turns(rule, report_data)
        elif condition == PolicyCondition.MAX_MATCHING_FAILURES:
            return self._eval_max_matching_failures(rule, report_data)

        # ── v2: Statistical conditions ──
        elif condition == PolicyCondition.MIN_SCORE_PERCENTILE:
            return self._eval_min_score_percentile(rule, report_data)
        elif condition == PolicyCondition.MAX_LOWER_TAIL_VIOLATIONS:
            return self._eval_max_lower_tail_violations(rule, report_data)
        elif condition == PolicyCondition.MAX_SCORE_STDDEV:
            return self._eval_max_score_stddev(rule, report_data)
        elif condition == PolicyCondition.MAX_REGRESSION_DELTA:
            return self._eval_max_regression_delta(rule, report_data)

        # ── v2: Cross-module conditions ──
        elif condition == PolicyCondition.REQUIRED_WORKFLOW_PASS_RATE:
            return self._eval_required_workflow_pass_rate(rule, report_data)
        elif condition == PolicyCondition.REQUIRED_RAG_METRIC:
            return self._eval_required_rag_metric(rule, report_data)
        elif condition == PolicyCondition.REQUIRED_TOOL_METRIC:
            return self._eval_required_tool_metric(rule, report_data)

        # ── v2: Content-matching conditions ──
        elif condition in (
            PolicyCondition.MUST_CONTAIN,
            PolicyCondition.REQUIRES_DISCLAIMER,
            PolicyCondition.REQUIRES_CITATION,
            PolicyCondition.REQUIRES_ESCALATION,
            PolicyCondition.REQUIRES_REFUSAL,
        ):
            return self._eval_must_contain(rule, report_data)
        elif condition == PolicyCondition.MUST_NOT_CONTAIN:
            return self._eval_must_not_contain(rule, report_data)

        else:
            return self._make_result(
                rule, passed=False, actual=0.0,
                message=f"Unknown condition '{condition.value}' — cannot evaluate",
            )

    # ─── v1 Evaluators (backward-compatible) ─────────────────

    def _eval_min_score(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: judge average score >= threshold. v2: supports scoped evaluation."""
        # Try evidence-level if scoped and conversations available
        if rule.applies_to and not rule.applies_to.is_empty:
            scores, evaluated, evidence = self._get_scoped_scores(rule, report_data)
            if evaluated > 0:
                avg_score = sum(scores) / len(scores) if scores else 0.0
                passed = avg_score >= rule.threshold
                return self._make_result(
                    rule, passed=passed, actual=round(avg_score, 4),
                    message=(
                        f"✅ {rule.judge} score {avg_score:.2%} >= {rule.threshold:.2%} (scoped, {evaluated} evaluated)"
                        if passed
                        else f"❌ {rule.judge} score {avg_score:.2%} < {rule.threshold:.2%} (scoped, {evaluated} evaluated)"
                    ),
                    evaluated_count=evaluated,
                    evidence=evidence if not passed else [],
                )
            else:
                # Scoped rule matched zero conversations — honor on_no_data
                return self._handle_no_data(rule, f"Scope matched 0 conversations for {rule.judge}")

        # Unscoped: aggregate score (v1 behavior)
        score = self._get_judge_score(rule.judge, report_data)
        passed = score >= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(score, 4),
            message=(
                f"✅ {rule.judge} score {score:.2%} >= {rule.threshold:.2%}"
                if passed
                else f"❌ {rule.judge} score {score:.2%} < {rule.threshold:.2%}"
            ),
        )

    def _eval_max_failure_rate(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: judge failure rate <= threshold."""
        # v2: use actual pass/fail counts if available from conversations
        conversations = self._get_scoped_conversations(rule, report_data)
        if conversations:
            total_turns, failed_turns = 0, 0
            evidence: List[FailureEvidence] = []
            for conv in conversations:
                turns = conv.get("judged_turns", conv.get("turns", []))
                for turn in turns:
                    total_turns += 1
                    turn_score = self._get_turn_judge_score(turn, rule.judge)
                    if turn_score is not None and turn_score < 0.5:
                        failed_turns += 1
                        if len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                            evidence.append(self._make_evidence(conv, turn, rule.judge, turn_score))

            if total_turns > 0:
                failure_rate = failed_turns / total_turns
                passed = failure_rate <= rule.threshold
                return self._make_result(
                    rule, passed=passed, actual=round(failure_rate, 4),
                    message=(
                        f"✅ {rule.judge} failure rate {failure_rate:.2%} <= {rule.threshold:.2%} ({failed_turns}/{total_turns} turns)"
                        if passed
                        else f"❌ {rule.judge} failure rate {failure_rate:.2%} > {rule.threshold:.2%} ({failed_turns}/{total_turns} turns)"
                    ),
                    evaluated_count=total_turns,
                    failed_count=failed_turns,
                    evidence=evidence if not passed else [],
                )

        # Fallback: v1 aggregate — use pass_rate from summary if available,
        # not 1 - judge_score (which conflates score and failure rate)
        summary = report_data.get("summary", {})
        pass_rate = self._get_summary_field(summary, "pass_rate", None)
        if pass_rate is not None and rule.judge == "overall":
            failure_rate = 1.0 - float(pass_rate)
        else:
            # Per-judge: still approximate, but label it clearly
            score = self._get_judge_score(rule.judge, report_data)
            failure_rate = 1.0 - score
        passed = failure_rate <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(failure_rate, 4),
            message=(
                f"✅ {rule.judge} failure rate {failure_rate:.2%} <= {rule.threshold:.2%} (aggregate)"
                if passed
                else f"❌ {rule.judge} failure rate {failure_rate:.2%} > {rule.threshold:.2%} (aggregate)"
            ),
            details={"source": "aggregate", "note": "per-judge rate approximated from score when no turn data available"},
        )

    def _eval_zero_critical(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: zero critical failures."""
        summary = report_data.get("summary", {})
        critical_count = self._get_summary_field(summary, "critical_failures", 0)
        passed = critical_count == 0
        return self._make_result(
            rule, passed=passed, actual=float(critical_count),
            message=(
                "✅ Zero critical failures"
                if passed
                else f"❌ {critical_count} critical failure(s) found"
            ),
        )

    def _eval_max_critical_count(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: critical failure count <= threshold."""
        summary = report_data.get("summary", {})
        critical_count = self._get_summary_field(summary, "critical_failures", 0)
        passed = critical_count <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(critical_count),
            message=(
                f"✅ {critical_count} critical failures <= {int(rule.threshold)}"
                if passed
                else f"❌ {critical_count} critical failures > {int(rule.threshold)}"
            ),
        )

    def _eval_min_pass_rate(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: overall or per-judge pass rate >= threshold.
        v2.1: For non-overall judges, computes actual pass rate from conversations when available.
        """
        # Try evidence-level pass rate for per-judge rules
        if rule.judge != "overall":
            conversations = self._get_scoped_conversations(rule, report_data)
            if conversations:
                total_turns, passed_turns = 0, 0
                for conv in conversations:
                    turns = conv.get("judged_turns", conv.get("turns", []))
                    for turn in turns:
                        if not self._turn_in_scope(turn, rule):
                            continue
                        score = self._get_turn_judge_score(turn, rule.judge)
                        if score is not None:
                            total_turns += 1
                            if score >= 0.5:
                                passed_turns += 1
                if total_turns > 0:
                    pass_rate = passed_turns / total_turns
                    passed = pass_rate >= rule.threshold
                    return self._make_result(
                        rule, passed=passed, actual=round(pass_rate, 4),
                        message=(
                            f"✅ {rule.judge} pass rate {pass_rate:.2%} >= {rule.threshold:.2%} ({passed_turns}/{total_turns} turns)"
                            if passed
                            else f"❌ {rule.judge} pass rate {pass_rate:.2%} < {rule.threshold:.2%} ({passed_turns}/{total_turns} turns)"
                        ),
                        evaluated_count=total_turns,
                        failed_count=total_turns - passed_turns,
                    )

        # Overall pass rate from summary, or aggregate fallback for per-judge
        if rule.judge == "overall":
            summary = report_data.get("summary", {})
            pass_rate = self._get_summary_field(summary, "pass_rate", 0.0)
        else:
            # Legacy fallback: use judge score as proxy (labeled clearly)
            pass_rate = self._get_judge_score(rule.judge, report_data)

        passed = pass_rate >= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(pass_rate, 4),
            message=(
                f"✅ Pass rate {pass_rate:.2%} >= {rule.threshold:.2%}"
                if passed
                else f"❌ Pass rate {pass_rate:.2%} < {rule.threshold:.2%}"
            ),
        )

    def _eval_max_warnings(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: warning count <= threshold."""
        summary = report_data.get("summary", {})
        warning_count = self._get_summary_field(summary, "warnings", 0)
        passed = warning_count <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(warning_count),
            message=(
                f"✅ {warning_count} warnings <= {int(rule.threshold)}"
                if passed
                else f"❌ {warning_count} warnings > {int(rule.threshold)}"
            ),
        )

    def _eval_custom(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """
        Custom condition — v2: fail-closed by default (configurable).

        Override this method in subclasses for custom evaluation logic.
        """
        error_mode = self.policy_set.evaluation_error_mode

        if error_mode == EvaluationErrorMode.PASS_OPEN:
            # v1 legacy behavior
            return self._make_result(
                rule, passed=True, actual=0.0,
                message="Custom rule — no evaluator registered (pass by default, legacy mode)",
            )
        elif error_mode == EvaluationErrorMode.NOT_EVALUATED:
            return self._make_result(
                rule, passed=False, actual=0.0,
                message="Custom rule — no evaluator registered (skipped)",
                not_evaluated=True,
            )
        else:
            # FAIL_CLOSED (enterprise default)
            return self._make_result(
                rule, passed=False, actual=0.0,
                message="❌ Custom rule — no evaluator registered (fail-closed). Subclass PolicyEngine to implement.",
                remediation="Register a custom evaluator by subclassing PolicyEngine and overriding _eval_custom().",
            )

    # ─── v2: Evidence-Level Evaluators ───────────────────────

    def _eval_max_failed_conversations(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: number of failed conversations for a judge <= threshold."""
        conversations = self._get_scoped_conversations(rule, report_data)
        if not conversations:
            return self._handle_no_data(rule, "No conversation data to evaluate")

        failed = 0
        evidence: List[FailureEvidence] = []
        for conv in conversations:
            conv_score = self._get_conversation_judge_score(conv, rule.judge)
            if conv_score is not None and conv_score < 0.5:
                failed += 1
                if len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                    evidence.append(FailureEvidence(
                        conversation_id=conv.get("conversation_id", conv.get("persona_name", "unknown")),
                        persona_name=conv.get("persona_name", "unknown"),
                        judge_name=rule.judge,
                        score=conv_score,
                        issue=f"Conversation-level {rule.judge} score: {conv_score:.2f}",
                    ))

        passed = failed <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(failed),
            message=(
                f"✅ {failed} failed conversations <= {int(rule.threshold)} ({len(conversations)} evaluated)"
                if passed
                else f"❌ {failed} failed conversations > {int(rule.threshold)} ({len(conversations)} evaluated)"
            ),
            evaluated_count=len(conversations),
            failed_count=failed,
            evidence=evidence if not passed else [],
        )

    def _eval_max_failed_turns(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: number of failed turns for a judge <= threshold."""
        conversations = self._get_scoped_conversations(rule, report_data)
        total_turns, failed_turns = 0, 0
        evidence: List[FailureEvidence] = []

        for conv in conversations:
            turns = conv.get("judged_turns", conv.get("turns", []))
            for turn in turns:
                if not self._turn_in_scope(turn, rule):
                    continue
                total_turns += 1
                score = self._get_turn_judge_score(turn, rule.judge)
                if score is not None and score < 0.5:
                    failed_turns += 1
                    if len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                        evidence.append(self._make_evidence(conv, turn, rule.judge, score))

        passed = failed_turns <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(failed_turns),
            message=(
                f"✅ {failed_turns} failed turns <= {int(rule.threshold)} ({total_turns} evaluated)"
                if passed
                else f"❌ {failed_turns} failed turns > {int(rule.threshold)} ({total_turns} evaluated)"
            ),
            evaluated_count=total_turns,
            failed_count=failed_turns,
            evidence=evidence if not passed else [],
        )

    def _eval_max_matching_failures(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: number of turns matching specific issue patterns <= threshold."""
        conversations = self._get_scoped_conversations(rule, report_data)
        total_checked, matched = 0, 0
        evidence: List[FailureEvidence] = []

        for conv in conversations:
            turns = conv.get("judged_turns", conv.get("turns", []))
            for turn in turns:
                if not self._turn_in_scope(turn, rule):
                    continue
                total_checked += 1

                # Check judge issues against match config
                issues = self._get_turn_issues(turn, rule.judge)
                bot_response = turn.get("bot_response", turn.get("response", ""))

                is_match = False
                if rule.match:
                    if rule.match.matches_issues(issues):
                        is_match = True
                    elif rule.match.matches_text(bot_response):
                        is_match = True

                if is_match:
                    matched += 1
                    if len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                        evidence.append(self._make_evidence(
                            conv, turn, rule.judge,
                            self._get_turn_judge_score(turn, rule.judge) or 0.0,
                            issue="; ".join(issues[:3]) if issues else "Content match",
                        ))

        passed = matched <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(matched),
            message=(
                f"✅ {matched} matching failures <= {int(rule.threshold)} ({total_checked} checked)"
                if passed
                else f"❌ {matched} matching failures > {int(rule.threshold)} ({total_checked} checked)"
            ),
            evaluated_count=total_checked,
            failed_count=matched,
            evidence=evidence if not passed else [],
        )

    # ─── v2: Statistical Evaluators ──────────────────────────

    def _eval_min_score_percentile(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: the N-th percentile of judge scores >= threshold.

        This is the UPPER percentile lookup (standard statistical definition):
        - p95 means "95% of scores are at or below this value"
        - p95 >= 0.8 means "the score at the 95th percentile meets 0.8"

        Use this when you want to verify that the bulk of scores meet a quality bar.
        For lower-tail / worst-case checks (e.g., "bottom 5% must be above X"),
        use max_lower_tail_violations instead.
        """
        scores, evaluated, _ = self._get_scoped_scores(rule, report_data)
        percentile = rule.percentile or 95

        if not scores:
            return self._handle_no_data(rule, f"No scores to evaluate for p{percentile}")

        sorted_scores = sorted(scores)
        idx = min(len(sorted_scores) - 1, max(0, int(len(sorted_scores) * percentile / 100.0) - 1))
        p_value = sorted_scores[idx]
        passed = p_value >= rule.threshold

        return self._make_result(
            rule, passed=passed, actual=round(p_value, 4),
            message=(
                f"✅ {rule.judge} p{percentile} score {p_value:.2%} >= {rule.threshold:.2%} ({evaluated} scores)"
                if passed
                else f"❌ {rule.judge} p{percentile} score {p_value:.2%} < {rule.threshold:.2%} ({evaluated} scores)"
            ),
            evaluated_count=evaluated,
        )

    def _eval_max_lower_tail_violations(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: number of scores in the bottom percentile that fall below threshold.

        This is the LOWER-TAIL robustness check:
        - percentile defines the tail size (default 5 = bottom 5%)
        - threshold defines the minimum acceptable score
        - metadata.max_violations = max allowed violations (default 0)

        Example: "In the bottom 5% of safety scores, zero may fall below 0.3"
        """
        scores, evaluated, evidence = self._get_scoped_scores(rule, report_data)
        percentile = rule.percentile or 5

        if not scores:
            return self._handle_no_data(rule, f"No scores for lower-tail p{percentile}")

        sorted_scores = sorted(scores)
        tail_count = max(1, int(len(sorted_scores) * percentile / 100.0))
        tail_scores = sorted_scores[:tail_count]
        violations = sum(1 for s in tail_scores if s < rule.threshold)
        max_violations = int(rule.metadata.get("max_violations", 0))
        passed = violations <= max_violations

        return self._make_result(
            rule, passed=passed, actual=float(violations),
            message=(
                f"✅ {rule.judge} lower-tail p{percentile}: {violations} violations <= {max_violations} "
                f"(tail={tail_count}, floor={rule.threshold:.2%})"
                if passed
                else f"❌ {rule.judge} lower-tail p{percentile}: {violations} violations > {max_violations} "
                f"(tail={tail_count}, floor={rule.threshold:.2%})"
            ),
            evaluated_count=evaluated,
            failed_count=violations,
            evidence=evidence[:violations] if not passed else [],
        )

    def _eval_max_score_stddev(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: standard deviation of judge scores <= threshold."""
        scores, evaluated, _ = self._get_scoped_scores(rule, report_data)

        if len(scores) < 2:
            return self._handle_no_data(rule, "Insufficient scores for stddev calculation")

        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        stddev = math.sqrt(variance)
        passed = stddev <= rule.threshold

        return self._make_result(
            rule, passed=passed, actual=round(stddev, 4),
            message=(
                f"✅ {rule.judge} score stddev {stddev:.4f} <= {rule.threshold:.4f} ({evaluated} scores)"
                if passed
                else f"❌ {rule.judge} score stddev {stddev:.4f} > {rule.threshold:.4f} ({evaluated} scores)"
            ),
            evaluated_count=evaluated,
        )

    def _eval_max_regression_delta(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: score drop from baseline <= threshold."""
        baseline = report_data.get("baseline", {})
        if not baseline:
            return self._handle_no_data(rule, "No baseline provided — regression check skipped")

        current_score = self._get_judge_score(rule.judge, report_data)
        baseline_score = self._get_judge_score(rule.judge, baseline)
        delta = baseline_score - current_score  # Positive = regression

        passed = delta <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(delta, 4),
            message=(
                f"✅ {rule.judge} regression delta {delta:.4f} <= {rule.threshold:.4f} "
                f"(current: {current_score:.2%}, baseline: {baseline_score:.2%})"
                if passed
                else f"❌ {rule.judge} regression delta {delta:.4f} > {rule.threshold:.4f} "
                f"(current: {current_score:.2%}, baseline: {baseline_score:.2%})"
            ),
        )

    # ─── v2: Cross-Module Evaluators ─────────────────────────

    def _eval_required_workflow_pass_rate(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: workflow judge pass rate >= threshold."""
        workflow_results = report_data.get("workflow_results", [])
        if not workflow_results:
            return self._handle_no_data(rule, "No workflow results available")

        # Filter by specific workflow if specified
        target = rule.workflow_name
        filtered = workflow_results
        if target:
            filtered = [w for w in workflow_results if w.get("workflow", "") == target]

        if not filtered:
            return self._handle_no_data(rule, f"No results for workflow '{target}'")

        total = len(filtered)
        passed_count = sum(1 for w in filtered if w.get("passed", False))
        pass_rate = passed_count / total if total > 0 else 0.0

        passed = pass_rate >= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(pass_rate, 4),
            message=(
                f"✅ Workflow pass rate {pass_rate:.2%} >= {rule.threshold:.2%} ({passed_count}/{total})"
                if passed
                else f"❌ Workflow pass rate {pass_rate:.2%} < {rule.threshold:.2%} ({passed_count}/{total})"
            ),
            evaluated_count=total,
            failed_count=total - passed_count,
        )

    def _eval_required_rag_metric(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: specific RAG metric >= threshold."""
        rag_results = report_data.get("rag_results", {})
        metric_name = rule.metric_name or "faithfulness"

        if not rag_results or metric_name not in rag_results:
            return self._handle_no_data(rule, f"RAG metric '{metric_name}' not available")

        metric_value = float(rag_results[metric_name])
        passed = metric_value >= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(metric_value, 4),
            message=(
                f"✅ RAG {metric_name} {metric_value:.2%} >= {rule.threshold:.2%}"
                if passed
                else f"❌ RAG {metric_name} {metric_value:.2%} < {rule.threshold:.2%}"
            ),
        )

    def _eval_required_tool_metric(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: specific tool metric >= threshold."""
        tool_results = report_data.get("tool_results", {})
        metric_name = rule.metric_name or "tool_accuracy"

        if not tool_results or metric_name not in tool_results:
            return self._handle_no_data(rule, f"Tool metric '{metric_name}' not available")

        metric_value = float(tool_results[metric_name])
        passed = metric_value >= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=round(metric_value, 4),
            message=(
                f"✅ Tool {metric_name} {metric_value:.2%} >= {rule.threshold:.2%}"
                if passed
                else f"❌ Tool {metric_name} {metric_value:.2%} < {rule.threshold:.2%}"
            ),
        )

    # ─── v2: Content Matching Evaluators ─────────────────────

    def _eval_must_contain(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """
        Check: bot responses must contain required patterns.
        Used for: must_contain, requires_disclaimer, requires_citation, requires_escalation, requires_refusal.
        """
        conversations = self._get_scoped_conversations(rule, report_data)
        if not conversations or not rule.match:
            return self._handle_no_data(rule, f"No data or no match config — {rule.condition.value} skipped")

        total_checked, missing = 0, 0
        evidence: List[FailureEvidence] = []

        for conv in conversations:
            turns = conv.get("judged_turns", conv.get("turns", []))
            for turn in turns:
                if not self._turn_in_scope(turn, rule):
                    continue
                total_checked += 1
                bot_response = turn.get("bot_response", turn.get("response", ""))
                if not rule.match.matches_text(bot_response):
                    missing += 1
                    if len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                        evidence.append(self._make_evidence(
                            conv, turn, rule.judge, 0.0,
                            issue=f"Missing required content: {rule.match.patterns[:2]}",
                        ))

        # For must_contain: threshold = max allowed missing (default 0)
        passed = missing <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(missing),
            message=(
                f"✅ {missing} responses missing required content <= {int(rule.threshold)} ({total_checked} checked)"
                if passed
                else f"❌ {missing} responses missing required content > {int(rule.threshold)} ({total_checked} checked)"
            ),
            evaluated_count=total_checked,
            failed_count=missing,
            evidence=evidence if not passed else [],
        )

    def _eval_must_not_contain(self, rule: PolicyRule, report_data: Dict[str, Any]) -> ComplianceResult:
        """Check: bot responses must NOT contain prohibited patterns."""
        conversations = self._get_scoped_conversations(rule, report_data)
        if not conversations or not rule.match:
            return self._handle_no_data(rule, "No data or no match config — must_not_contain skipped")

        total_checked, violations = 0, 0
        evidence: List[FailureEvidence] = []

        for conv in conversations:
            turns = conv.get("judged_turns", conv.get("turns", []))
            for turn in turns:
                if not self._turn_in_scope(turn, rule):
                    continue
                total_checked += 1
                bot_response = turn.get("bot_response", turn.get("response", ""))
                if rule.match.matches_text(bot_response):
                    violations += 1
                    if len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                        evidence.append(self._make_evidence(
                            conv, turn, rule.judge, 0.0,
                            issue=f"Contains prohibited content",
                        ))

        passed = violations <= rule.threshold
        return self._make_result(
            rule, passed=passed, actual=float(violations),
            message=(
                f"✅ {violations} prohibited content matches <= {int(rule.threshold)} ({total_checked} checked)"
                if passed
                else f"❌ {violations} prohibited content matches > {int(rule.threshold)} ({total_checked} checked)"
            ),
            evaluated_count=total_checked,
            failed_count=violations,
            evidence=evidence if not passed else [],
        )

    # ─── Composite Rule Evaluator ────────────────────────────

    def _evaluate_composite(
        self, rule: PolicyRule, rule_results_map: Dict[str, ComplianceResult]
    ) -> ComplianceResult:
        """Evaluate composite rule logic: all_of, any_of, not, conditional."""
        if not rule.composite:
            return self._make_result(
                rule, passed=False, actual=0.0,
                message="❌ Composite rule missing configuration",
            )

        comp = rule.composite
        comp_type = comp.type.lower()

        if comp_type == "all_of":
            results = [rule_results_map.get(rid) for rid in comp.rule_ids if rid in rule_results_map]
            all_passed = all(r.passed for r in results if r is not None)
            actual = sum(1 for r in results if r and r.passed) / max(len(results), 1)
            return self._make_result(
                rule, passed=all_passed, actual=round(actual, 4),
                message=(
                    f"✅ All {len(comp.rule_ids)} sub-rules passed"
                    if all_passed
                    else f"❌ {sum(1 for r in results if r and not r.passed)}/{len(comp.rule_ids)} sub-rules failed"
                ),
            )

        elif comp_type == "any_of":
            results = [rule_results_map.get(rid) for rid in comp.rule_ids if rid in rule_results_map]
            any_passed = any(r.passed for r in results if r is not None)
            actual = sum(1 for r in results if r and r.passed) / max(len(results), 1)
            return self._make_result(
                rule, passed=any_passed, actual=round(actual, 4),
                message=(
                    f"✅ At least one of {len(comp.rule_ids)} sub-rules passed"
                    if any_passed
                    else f"❌ None of {len(comp.rule_ids)} sub-rules passed"
                ),
            )

        elif comp_type == "not":
            if comp.rule_ids:
                target = rule_results_map.get(comp.rule_ids[0])
                if target:
                    passed = not target.passed
                    return self._make_result(
                        rule, passed=passed, actual=target.actual_value,
                        message=(
                            f"✅ Negation: '{comp.rule_ids[0]}' correctly failed"
                            if passed
                            else f"❌ Negation: '{comp.rule_ids[0]}' unexpectedly passed"
                        ),
                    )

        elif comp_type == "conditional":
            trigger = rule_results_map.get(comp.when_rule or "")
            if trigger is None or not trigger.passed:
                # Trigger not met — conditional does not apply
                return self._make_result(
                    rule, passed=True, actual=0.0,
                    message=f"✅ Conditional trigger '{comp.when_rule}' not met — rule does not apply",
                    rule_status="conditional_not_triggered",
                )
            # Trigger met — check then_rules
            then_results = [rule_results_map.get(rid) for rid in comp.then_rules if rid in rule_results_map]
            all_then_passed = all(r.passed for r in then_results if r is not None)
            return self._make_result(
                rule, passed=all_then_passed, actual=0.0,
                message=(
                    f"✅ Conditional: trigger '{comp.when_rule}' met, all {len(comp.then_rules)} requirements passed"
                    if all_then_passed
                    else f"❌ Conditional: trigger '{comp.when_rule}' met, but requirements failed"
                ),
            )

        return self._make_result(
            rule, passed=False, actual=0.0,
            message=f"❌ Unknown composite type '{comp_type}'",
        )

    # ─── Scoping & Data Extraction Helpers ───────────────────

    def _get_scoped_conversations(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Get conversations filtered by rule scope."""
        conversations = report_data.get("judged_conversations", [])
        if not conversations or not rule.applies_to or rule.applies_to.is_empty:
            return conversations

        scope = rule.applies_to
        filtered = []

        for conv in conversations:
            # Filter by persona type
            if scope.persona_types:
                persona = conv.get("persona_type", conv.get("persona_name", ""))
                if not any(pt.lower() in persona.lower() for pt in scope.persona_types):
                    continue

            # Filter by scenario
            if scope.scenarios:
                scenario = conv.get("scenario", "")
                if not any(s.lower() in scenario.lower() for s in scope.scenarios):
                    continue

            # Filter by tags
            if scope.tags:
                conv_tags = set(t.lower() for t in conv.get("tags", []))
                if not any(t.lower() in conv_tags for t in scope.tags):
                    continue

            # Filter by workflow
            if scope.workflows:
                workflow = conv.get("workflow", "")
                if not any(w.lower() in workflow.lower() for w in scope.workflows):
                    continue

            # Filter by source
            if scope.sources:
                source = conv.get("source", "synthetic")
                if source.lower() not in [s.lower() for s in scope.sources]:
                    continue

            filtered.append(conv)

        return filtered

    def _turn_in_scope(self, turn: Dict[str, Any], rule: PolicyRule) -> bool:
        """Check if a turn is within the rule's turn_range scope."""
        if not rule.applies_to or rule.applies_to.turn_range is None:
            return True
        turn_range = rule.applies_to.turn_range
        if len(turn_range) != 2:
            return True
        turn_idx = turn.get("turn_number", turn.get("turn_index", 0))
        return turn_range[0] <= turn_idx <= turn_range[1]

    def _get_scoped_scores(
        self, rule: PolicyRule, report_data: Dict[str, Any]
    ) -> Tuple[List[float], int, List[FailureEvidence]]:
        """Get per-turn scores filtered by scope. Returns (scores, evaluated_count, evidence)."""
        conversations = self._get_scoped_conversations(rule, report_data)
        scores: List[float] = []
        evidence: List[FailureEvidence] = []

        for conv in conversations:
            turns = conv.get("judged_turns", conv.get("turns", []))
            for turn in turns:
                if not self._turn_in_scope(turn, rule):
                    continue
                score = self._get_turn_judge_score(turn, rule.judge)
                if score is not None:
                    scores.append(score)
                    if score < rule.threshold and len(evidence) < self.MAX_EVIDENCE_SAMPLES:
                        evidence.append(self._make_evidence(conv, turn, rule.judge, score))

        return scores, len(scores), evidence

    def _get_turn_judge_score(self, turn: Dict[str, Any], judge_name: str) -> Optional[float]:
        """Extract a specific judge's score from a turn dict."""
        # Format 1: judgments list with judge_name
        judgments = turn.get("judgments", [])
        for j in judgments:
            jname = j.get("judge_name", j.get("judge", ""))
            if jname.lower() == judge_name.lower() or judge_name.lower() in jname.lower():
                return float(j.get("score", 0.0))

        # Format 2: judge_results list
        judge_results = turn.get("judge_results", [])
        for jr in judge_results:
            jname = jr.get("judge", jr.get("judge_name", ""))
            if jname.lower() == judge_name.lower() or judge_name.lower() in jname.lower():
                return float(jr.get("score", 0.0))

        # Format 3: flat score fields
        score_key = f"{judge_name}_score"
        if score_key in turn:
            return float(turn[score_key])

        # For 'overall', try composite score
        if judge_name == "overall":
            return turn.get("composite_score", turn.get("score", None))

        return None

    def _get_turn_issues(self, turn: Dict[str, Any], judge_name: str) -> List[str]:
        """Extract issue descriptions from a turn for a specific judge."""
        issues = []

        judgments = turn.get("judgments", [])
        for j in judgments:
            jname = j.get("judge_name", j.get("judge", ""))
            if jname.lower() == judge_name.lower() or judge_name.lower() in jname.lower():
                if "issues" in j:
                    issues.extend(j["issues"])
                if "issue" in j:
                    issues.append(j["issue"])
                if "details" in j and isinstance(j["details"], dict):
                    for k, v in j["details"].items():
                        if isinstance(v, str) and v:
                            issues.append(v)

        # Also check judge_results format
        judge_results = turn.get("judge_results", [])
        for jr in judge_results:
            jname = jr.get("judge", jr.get("judge_name", ""))
            if jname.lower() == judge_name.lower() or judge_name.lower() in jname.lower():
                if "issues" in jr:
                    issues.extend(jr["issues"])
                if "reasoning" in jr:
                    issues.append(jr["reasoning"])

        return issues

    def _get_conversation_judge_score(self, conv: Dict[str, Any], judge_name: str) -> Optional[float]:
        """Get the average judge score for a conversation."""
        turns = conv.get("judged_turns", conv.get("turns", []))
        scores = []
        for turn in turns:
            score = self._get_turn_judge_score(turn, judge_name)
            if score is not None:
                scores.append(score)
        return sum(scores) / len(scores) if scores else None

    def _get_judge_score(self, judge_name: str, report_data: Dict[str, Any]) -> float:
        """Get the aggregate score for a judge from report data (v1 compatible)."""
        if judge_name == "overall":
            summary = report_data.get("summary", {})
            return float(self._get_summary_field(summary, "average_score", 0.0))

        score_by_judge = report_data.get("score_by_judge", {})
        if isinstance(score_by_judge, dict):
            return float(score_by_judge.get(judge_name, 0.0))
        return 0.0

    def _get_summary_field(self, summary: Any, field: str, default: Any = 0) -> Any:
        """Get a field from summary, handling both dict and object access."""
        if isinstance(summary, dict):
            return summary.get(field, default)
        return getattr(summary, field, default)

    # ─── Evidence & Result Helpers ───────────────────────────

    def _handle_no_data(
        self,
        rule: PolicyRule,
        reason: str,
    ) -> ComplianceResult:
        """
        Handle missing data for a rule using on_no_data behavior.

        Priority: rule.on_no_data > policy_set.default_no_data_mode > "pass"
        """
        mode = (rule.on_no_data or self.policy_set.default_no_data_mode or "pass").lower()

        if mode == "fail":
            return self._make_result(
                rule, passed=False, actual=0.0,
                message=f"❌ {reason} (on_no_data=fail)",
                evaluated_count=0,
                rule_status="no_data",
            )
        elif mode == "skip":
            return self._make_result(
                rule, passed=False, actual=0.0,
                message=f"⊘ {reason} (on_no_data=skip)",
                evaluated_count=0,
                not_evaluated=True,
                rule_status="not_applicable",
            )
        else:
            # "pass" — lenient default
            return self._make_result(
                rule, passed=True, actual=0.0,
                message=f"✅ {reason} (on_no_data=pass)",
                evaluated_count=0,
                rule_status="no_data",
            )

    def _make_evidence(
        self,
        conv: Dict[str, Any],
        turn: Dict[str, Any],
        judge_name: str,
        score: float,
        issue: str = "",
    ) -> FailureEvidence:
        """Create a FailureEvidence from conversation and turn data."""
        bot_response = turn.get("bot_response", turn.get("response", ""))
        snippet = bot_response[:200] + "..." if len(bot_response) > 200 else bot_response

        if not issue:
            issues = self._get_turn_issues(turn, judge_name)
            issue = "; ".join(issues[:3]) if issues else f"{judge_name} score: {score:.2f}"

        return FailureEvidence(
            conversation_id=conv.get("conversation_id", conv.get("persona_name", "unknown")),
            persona_name=conv.get("persona_name", "unknown"),
            turn_index=turn.get("turn_number", turn.get("turn_index", None)),
            judge_name=judge_name,
            score=score,
            issue=issue,
            bot_response_snippet=snippet,
        )

    def _make_result(
        self,
        rule: PolicyRule,
        passed: bool,
        actual: float,
        message: str = "",
        evaluated_count: int = 0,
        failed_count: int = 0,
        evidence: Optional[List[FailureEvidence]] = None,
        not_evaluated: bool = False,
        remediation: str = "",
        details: Optional[Dict[str, Any]] = None,
        rule_status: str = "",
    ) -> ComplianceResult:
        """Create a ComplianceResult with v2 fields."""
        # Derive rule_status if not explicit
        if not rule_status:
            if not_evaluated:
                rule_status = "skipped"
            elif passed:
                rule_status = "passed"
            else:
                rule_status = "failed"

        # Build scope description
        scope_desc = None
        if rule.applies_to and not rule.applies_to.is_empty:
            parts = []
            if rule.applies_to.persona_types:
                parts.append(f"personas: {rule.applies_to.persona_types}")
            if rule.applies_to.scenarios:
                parts.append(f"scenarios: {rule.applies_to.scenarios}")
            if rule.applies_to.tags:
                parts.append(f"tags: {rule.applies_to.tags}")
            if rule.applies_to.workflows:
                parts.append(f"workflows: {rule.applies_to.workflows}")
            if rule.applies_to.sources:
                parts.append(f"sources: {rule.applies_to.sources}")
            if rule.applies_to.turn_range:
                parts.append(f"turns: {rule.applies_to.turn_range}")
            scope_desc = ", ".join(parts)

        return ComplianceResult(
            rule_id=rule.id,
            rule_name=rule.name,
            passed=passed,
            severity=rule.severity,
            actual_value=actual,
            threshold=rule.threshold,
            condition=rule.condition,
            judge=rule.judge,
            message=message,
            details=details or {},
            control_family=rule.control_family,
            evidence=evidence or [],
            evaluated_count=evaluated_count,
            failed_count=failed_count,
            scope_applied=scope_desc,
            remediation=remediation or rule.remediation,
            not_evaluated=not_evaluated,
            rule_status=rule_status,
        )

    # ─── Control Family Breakdown ────────────────────────────

    def _build_control_family_results(
        self, results: List[ComplianceResult]
    ) -> Dict[str, Dict[str, Any]]:
        """Build per-control-family pass/fail summary."""
        families: Dict[str, Dict[str, Any]] = {}
        for r in results:
            family = r.control_family or "ungrouped"
            if family not in families:
                families[family] = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
            families[family]["total"] += 1
            if r.not_evaluated:
                families[family]["skipped"] += 1
            elif r.passed:
                families[family]["passed"] += 1
            else:
                families[family]["failed"] += 1
        return families

    # ─── Provenance ──────────────────────────────────────────

    def _build_provenance(self) -> Dict[str, Any]:
        """Build audit provenance metadata for the compliance evaluation."""
        import json as _json

        # Policy hash — normalized fingerprint of the FULL policy structure
        # Includes all fields that affect evaluation outcomes
        rules_repr = []
        for r in self.policy_set.rules:
            rule_dict = {
                "id": r.id,
                "judge": r.judge,
                "condition": r.condition.value,
                "threshold": r.threshold,
                "severity": r.severity.value,
                "on_no_data": r.on_no_data,
            }
            if r.applies_to and not r.applies_to.is_empty:
                rule_dict["scope"] = {
                    "persona_types": sorted(r.applies_to.persona_types),
                    "scenarios": sorted(r.applies_to.scenarios),
                    "tags": sorted(r.applies_to.tags),
                    "workflows": sorted(r.applies_to.workflows),
                    "sources": sorted(r.applies_to.sources),
                    "turn_range": r.applies_to.turn_range,
                }
            if r.match:
                rule_dict["match"] = {
                    "patterns": sorted(r.match.patterns),
                    "issue_tags": sorted(r.match.issue_tags),
                    "regex": r.match.regex,
                    "field": r.match.field,
                    "case_sensitive": r.match.case_sensitive,
                }
            if r.composite:
                rule_dict["composite"] = {
                    "type": r.composite.type,
                    "rule_ids": sorted(r.composite.rule_ids),
                    "when_rule": r.composite.when_rule,
                    "then_rules": sorted(r.composite.then_rules),
                }
            if r.percentile is not None:
                rule_dict["percentile"] = r.percentile
            if r.metric_name:
                rule_dict["metric_name"] = r.metric_name
            if r.workflow_name:
                rule_dict["workflow_name"] = r.workflow_name
            rules_repr.append(rule_dict)

        policy_repr = _json.dumps({
            "name": self.policy_set.name,
            "version": self.policy_set.version,
            "mode": self.policy_set.mode.value,
            "compliance_threshold": self.policy_set.compliance_threshold,
            "evaluation_error_mode": self.policy_set.evaluation_error_mode.value,
            "default_no_data_mode": self.policy_set.default_no_data_mode,
            "max_tolerated_failures": self.policy_set.max_tolerated_failures,
            "rules": rules_repr,
        }, sort_keys=True)
        policy_hash = hashlib.sha256(policy_repr.encode()).hexdigest()[:16]

        return {
            "schema_version": "2.1",
            "policy_hash": policy_hash,
            "policy_name": self.policy_set.name,
            "policy_version": self.policy_set.version,
            "evaluator_version": "2.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gate_mode": self.policy_set.mode.value,
            "evaluation_error_mode": self.policy_set.evaluation_error_mode.value,
            "default_no_data_mode": self.policy_set.default_no_data_mode,
            "total_rules_defined": self.policy_set.rule_count,
        }

    # ─── Summary ─────────────────────────────────────────────

    def _generate_summary(
        self,
        total: int,
        passed: int,
        failed: int,
        skipped: int,
        critical_violations: List[ComplianceResult],
        high_violations: List[ComplianceResult],
        overall_compliant: bool,
    ) -> str:
        """Generate a human-readable compliance summary."""
        parts = []
        mode = self.policy_set.mode.value

        if overall_compliant:
            parts.append(f"✅ COMPLIANT ({mode} mode) — {passed}/{total} rules passed")
        else:
            parts.append(f"❌ NON-COMPLIANT ({mode} mode) — {failed}/{total} rules failed")

        if critical_violations:
            crit_names = [v.rule_name for v in critical_violations]
            parts.append(f"Critical violations: {', '.join(crit_names)}")

        if high_violations:
            high_names = [v.rule_name for v in high_violations]
            parts.append(f"High violations: {', '.join(high_names)}")

        if skipped > 0:
            parts.append(f"{skipped} rule(s) not evaluated")

        pct = (passed / total * 100) if total > 0 else 0
        parts.append(f"Compliance score: {pct:.0f}%")

        return ". ".join(parts)
