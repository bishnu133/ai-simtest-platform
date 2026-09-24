"""
Tests for P3 #15: Policy-as-Code Guardrails

Tests cover:
- Policy models (PolicyRule, PolicySet, ComplianceResult, ComplianceScorecard)
- Policy loader (YAML files, dicts, built-in templates, validation)
- Policy engine (all 7 condition types, edge cases, aggregate scoring)
- Built-in templates (general, healthcare, finance, airline)
- End-to-end: load policy → evaluate report → check scorecard
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from ai_simtest_engine.policy import (
    BUILT_IN_POLICIES,
    ComplianceResult,
    ComplianceScorecard,
    PolicyCondition,
    PolicyEngine,
    PolicyLoadError,
    PolicyLoader,
    PolicyRule,
    PolicySet,
    PolicySeverity,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_report_data():
    """A realistic simulation report as a dict (mimics summary JSON)."""
    return {
        "summary": {
            "simulation_id": "test-001",
            "simulation_name": "Test Simulation",
            "total_personas": 5,
            "total_conversations": 5,
            "total_turns": 30,
            "pass_rate": 0.80,
            "average_score": 0.75,
            "critical_failures": 0,
            "warnings": 3,
            "execution_time_seconds": 45.0,
        },
        "score_by_judge": {
            "grounding": 0.65,
            "safety": 0.95,
            "quality": 0.70,
            "relevance": 0.72,
        },
        "failure_patterns": [],
        "recommendations": [],
    }


@pytest.fixture
def failing_report_data():
    """A report with failures — triggers policy violations."""
    return {
        "summary": {
            "simulation_id": "test-002",
            "simulation_name": "Failing Simulation",
            "total_personas": 5,
            "total_conversations": 5,
            "total_turns": 30,
            "pass_rate": 0.40,
            "average_score": 0.45,
            "critical_failures": 5,
            "warnings": 15,
            "execution_time_seconds": 60.0,
        },
        "score_by_judge": {
            "grounding": 0.30,
            "safety": 0.50,
            "quality": 0.40,
            "relevance": 0.35,
        },
        "failure_patterns": [],
        "recommendations": [],
    }


@pytest.fixture
def sample_policy_dict():
    """A simple policy dict for testing."""
    return {
        "name": "Test Policy",
        "version": "1.0",
        "description": "Test policy for unit tests",
        "industry": "testing",
        "rules": [
            {
                "id": "safety_check",
                "name": "Safety Check",
                "description": "Safety must be above 80%",
                "judge": "safety",
                "condition": "min_score",
                "threshold": 0.8,
                "severity": "critical",
            },
            {
                "id": "quality_check",
                "name": "Quality Check",
                "description": "Quality must be above 60%",
                "judge": "quality",
                "condition": "min_score",
                "threshold": 0.6,
                "severity": "medium",
            },
        ],
    }


@pytest.fixture
def sample_yaml_file(sample_policy_dict, tmp_path):
    """Create a temporary YAML policy file."""
    yaml_path = tmp_path / "test_policy.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(sample_policy_dict, f)
    return yaml_path


# ============================================================================
# PolicyRule Model Tests
# ============================================================================

class TestPolicyRule:

    def test_create_basic_rule(self):
        rule = PolicyRule(
            id="test_rule",
            name="Test Rule",
            judge="safety",
            condition=PolicyCondition.MIN_SCORE,
            threshold=0.8,
            severity=PolicySeverity.CRITICAL,
        )
        assert rule.id == "test_rule"
        assert rule.judge == "safety"
        assert rule.condition == PolicyCondition.MIN_SCORE
        assert rule.threshold == 0.8
        assert rule.severity == PolicySeverity.CRITICAL

    def test_rule_default_values(self):
        rule = PolicyRule(
            id="minimal",
            name="Minimal Rule",
            judge="quality",
            condition=PolicyCondition.MIN_SCORE,
        )
        assert rule.threshold == 0.0
        assert rule.severity == PolicySeverity.HIGH
        assert rule.tags == []
        assert rule.description == ""

    def test_rule_with_tags(self):
        rule = PolicyRule(
            id="tagged",
            name="Tagged Rule",
            judge="grounding",
            condition=PolicyCondition.MIN_SCORE,
            tags=["hipaa", "pii"],
        )
        assert "hipaa" in rule.tags
        assert len(rule.tags) == 2


# ============================================================================
# PolicySet Model Tests
# ============================================================================

class TestPolicySet:

    def test_create_policy_set(self):
        rules = [
            PolicyRule(id="r1", name="Rule 1", judge="safety", condition=PolicyCondition.MIN_SCORE),
            PolicyRule(id="r2", name="Rule 2", judge="quality", condition=PolicyCondition.MIN_SCORE),
        ]
        ps = PolicySet(name="Test Set", rules=rules)
        assert ps.name == "Test Set"
        assert ps.rule_count == 2

    def test_critical_rules_property(self):
        rules = [
            PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE, severity=PolicySeverity.CRITICAL),
            PolicyRule(id="r2", name="R2", judge="quality", condition=PolicyCondition.MIN_SCORE, severity=PolicySeverity.MEDIUM),
            PolicyRule(id="r3", name="R3", judge="grounding", condition=PolicyCondition.MIN_SCORE, severity=PolicySeverity.CRITICAL),
        ]
        ps = PolicySet(name="Test", rules=rules)
        assert len(ps.critical_rules) == 2

    def test_get_rule_by_id(self):
        rules = [
            PolicyRule(id="alpha", name="Alpha", judge="safety", condition=PolicyCondition.MIN_SCORE),
            PolicyRule(id="beta", name="Beta", judge="quality", condition=PolicyCondition.MIN_SCORE),
        ]
        ps = PolicySet(name="Test", rules=rules)
        assert ps.get_rule("alpha") is not None
        assert ps.get_rule("alpha").name == "Alpha"
        assert ps.get_rule("nonexistent") is None

    def test_get_rules_for_judge(self):
        rules = [
            PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE),
            PolicyRule(id="r2", name="R2", judge="safety", condition=PolicyCondition.ZERO_CRITICAL),
            PolicyRule(id="r3", name="R3", judge="quality", condition=PolicyCondition.MIN_SCORE),
        ]
        ps = PolicySet(name="Test", rules=rules)
        safety_rules = ps.get_rules_for_judge("safety")
        assert len(safety_rules) == 2

    def test_empty_policy_set(self):
        ps = PolicySet(name="Empty")
        assert ps.rule_count == 0
        assert ps.critical_rules == []


# ============================================================================
# ComplianceScorecard Model Tests
# ============================================================================

class TestComplianceScorecard:

    def test_scorecard_properties(self):
        results = [
            ComplianceResult(rule_id="r1", rule_name="R1", passed=True, severity=PolicySeverity.HIGH, actual_value=0.9, threshold=0.8, condition=PolicyCondition.MIN_SCORE, judge="safety"),
            ComplianceResult(rule_id="r2", rule_name="R2", passed=False, severity=PolicySeverity.CRITICAL, actual_value=3.0, threshold=0.0, condition=PolicyCondition.ZERO_CRITICAL, judge="safety"),
            ComplianceResult(rule_id="r3", rule_name="R3", passed=False, severity=PolicySeverity.MEDIUM, actual_value=0.4, threshold=0.6, condition=PolicyCondition.MIN_SCORE, judge="quality"),
        ]
        sc = ComplianceScorecard(
            policy_set_name="Test",
            total_rules=3,
            passed_rules=1,
            failed_rules=2,
            results=results,
            overall_compliant=False,
            compliance_score=33.3,
            critical_violations=[results[1]],
        )
        assert sc.has_critical_violations is True
        assert sc.violation_count_by_severity == {"critical": 1, "medium": 1}
        assert len(sc.get_failed_results()) == 2
        assert len(sc.get_passed_results()) == 1

    def test_scorecard_to_summary_dict(self):
        results = [
            ComplianceResult(rule_id="r1", rule_name="R1", passed=True, severity=PolicySeverity.HIGH, actual_value=0.9, threshold=0.8, condition=PolicyCondition.MIN_SCORE, judge="safety"),
        ]
        sc = ComplianceScorecard(
            policy_set_name="Test",
            total_rules=1,
            passed_rules=1,
            failed_rules=0,
            results=results,
            overall_compliant=True,
            compliance_score=100.0,
        )
        d = sc.to_summary_dict()
        assert d["overall_compliant"] is True
        assert d["compliance_score"] == 100.0
        assert len(d["results"]) == 1


# ============================================================================
# PolicyLoader Tests
# ============================================================================

class TestPolicyLoader:

    def test_load_from_dict(self, sample_policy_dict):
        ps = PolicyLoader.load_from_dict(sample_policy_dict)
        assert ps.name == "Test Policy"
        assert ps.rule_count == 2
        assert ps.rules[0].id == "safety_check"

    def test_load_from_dict_missing_name(self):
        with pytest.raises(PolicyLoadError, match="name"):
            PolicyLoader.load_from_dict({"rules": []})

    def test_load_from_dict_missing_rules(self):
        with pytest.raises(PolicyLoadError, match="rules"):
            PolicyLoader.load_from_dict({"name": "Test"})

    def test_load_from_dict_invalid_condition(self):
        data = {
            "name": "Test",
            "rules": [
                {"id": "r1", "name": "R1", "judge": "safety", "condition": "invalid_cond", "severity": "high"}
            ],
        }
        with pytest.raises(PolicyLoadError, match="invalid condition"):
            PolicyLoader.load_from_dict(data)

    def test_load_from_dict_invalid_severity(self):
        data = {
            "name": "Test",
            "rules": [
                {"id": "r1", "name": "R1", "judge": "safety", "condition": "min_score", "severity": "super_bad"}
            ],
        }
        with pytest.raises(PolicyLoadError, match="invalid severity"):
            PolicyLoader.load_from_dict(data)

    def test_load_from_yaml_file(self, sample_yaml_file):
        ps = PolicyLoader.load_from_file(sample_yaml_file)
        assert ps.name == "Test Policy"
        assert ps.rule_count == 2

    def test_load_from_nonexistent_file(self, tmp_path):
        with pytest.raises(PolicyLoadError, match="not found"):
            PolicyLoader.load_from_file(tmp_path / "nonexistent.yaml")

    def test_load_from_non_yaml_file(self, tmp_path):
        txt_file = tmp_path / "policy.txt"
        txt_file.write_text("not yaml")
        with pytest.raises(PolicyLoadError, match="must be .yaml"):
            PolicyLoader.load_from_file(txt_file)

    def test_load_from_invalid_yaml(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("{{{{invalid yaml content")
        with pytest.raises(PolicyLoadError):
            PolicyLoader.load_from_file(bad_yaml)

    def test_load_from_directory(self, tmp_path, sample_policy_dict):
        # Create 2 YAML files
        for i in range(2):
            d = sample_policy_dict.copy()
            d["name"] = f"Policy {i}"
            with open(tmp_path / f"policy_{i}.yaml", "w") as f:
                yaml.dump(d, f)
        # Create a non-YAML file (should be ignored)
        (tmp_path / "readme.txt").write_text("ignore me")

        policies = PolicyLoader.load_from_directory(tmp_path)
        assert len(policies) == 2

    def test_load_from_empty_directory(self, tmp_path):
        policies = PolicyLoader.load_from_directory(tmp_path)
        assert len(policies) == 0

    def test_load_from_nonexistent_directory(self, tmp_path):
        with pytest.raises(PolicyLoadError, match="not found"):
            PolicyLoader.load_from_directory(tmp_path / "ghost")

    def test_load_built_in_general(self):
        ps = PolicyLoader.load_built_in("general")
        assert ps is not None
        assert ps.name == "General Chatbot Compliance"
        assert ps.rule_count >= 5

    def test_load_built_in_healthcare(self):
        ps = PolicyLoader.load_built_in("healthcare")
        assert ps is not None
        assert "HIPAA" in ps.description or "hipaa" in ps.description.lower()

    def test_load_built_in_finance(self):
        ps = PolicyLoader.load_built_in("finance")
        assert ps is not None
        assert ps.industry == "finance"

    def test_load_built_in_airline(self):
        ps = PolicyLoader.load_built_in("airline")
        assert ps is not None
        assert ps.industry == "airline"

    def test_load_built_in_nonexistent(self):
        ps = PolicyLoader.load_built_in("nonexistent_industry")
        assert ps is None

    def test_list_built_in(self):
        templates = PolicyLoader.list_built_in()
        assert "general" in templates
        assert "healthcare" in templates
        assert "finance" in templates
        assert "airline" in templates

    def test_validate_policy_set_valid(self, sample_policy_dict):
        ps = PolicyLoader.load_from_dict(sample_policy_dict)
        warnings = PolicyLoader.validate_policy_set(ps)
        assert len(warnings) == 0

    def test_validate_policy_set_empty_rules(self):
        ps = PolicySet(name="Empty")
        warnings = PolicyLoader.validate_policy_set(ps)
        assert any("no rules" in w.lower() for w in warnings)

    def test_validate_policy_set_duplicate_ids(self):
        ps = PolicySet(
            name="Dupes",
            rules=[
                PolicyRule(id="dup", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE),
                PolicyRule(id="dup", name="R2", judge="quality", condition=PolicyCondition.MIN_SCORE),
            ],
        )
        warnings = PolicyLoader.validate_policy_set(ps)
        assert any("duplicate" in w.lower() for w in warnings)

    def test_validate_policy_set_invalid_judge(self):
        ps = PolicySet(
            name="BadJudge",
            rules=[
                PolicyRule(id="r1", name="R1", judge="nonexistent_judge", condition=PolicyCondition.MIN_SCORE),
            ],
        )
        warnings = PolicyLoader.validate_policy_set(ps)
        assert any("unknown judge" in w.lower() for w in warnings)

    def test_validate_policy_set_bad_threshold(self):
        ps = PolicySet(
            name="BadThreshold",
            rules=[
                PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE, threshold=1.5),
            ],
        )
        warnings = PolicyLoader.validate_policy_set(ps)
        assert any("threshold" in w.lower() for w in warnings)


# ============================================================================
# PolicyEngine Tests — Condition Evaluation
# ============================================================================

class TestPolicyEngine:

    def test_min_score_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Safety Check", judge="safety", condition=PolicyCondition.MIN_SCORE, threshold=0.8),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.total_rules == 1
        assert scorecard.results[0].passed is True  # 0.95 >= 0.8

    def test_min_score_fail(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Grounding Check", judge="grounding", condition=PolicyCondition.MIN_SCORE, threshold=0.8),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is False  # 0.65 < 0.8

    def test_max_failure_rate_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Safety Failure Rate", judge="safety", condition=PolicyCondition.MAX_FAILURE_RATE, threshold=0.1),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is True  # 1 - 0.95 = 0.05 <= 0.1

    def test_max_failure_rate_fail(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Grounding Failure Rate", judge="grounding", condition=PolicyCondition.MAX_FAILURE_RATE, threshold=0.1),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is False  # 1 - 0.65 = 0.35 > 0.1

    def test_zero_critical_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="No Critical", judge="safety", condition=PolicyCondition.ZERO_CRITICAL, severity=PolicySeverity.CRITICAL),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is True  # 0 critical failures

    def test_zero_critical_fail(self, failing_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="No Critical", judge="safety", condition=PolicyCondition.ZERO_CRITICAL, severity=PolicySeverity.CRITICAL),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)
        assert scorecard.results[0].passed is False  # 5 critical failures

    def test_max_critical_count_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Max 2 Critical", judge="overall", condition=PolicyCondition.MAX_CRITICAL_COUNT, threshold=2),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is True  # 0 <= 2

    def test_max_critical_count_fail(self, failing_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Max 2 Critical", judge="overall", condition=PolicyCondition.MAX_CRITICAL_COUNT, threshold=2),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)
        assert scorecard.results[0].passed is False  # 5 > 2

    def test_min_pass_rate_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Pass Rate", judge="overall", condition=PolicyCondition.MIN_PASS_RATE, threshold=0.7),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is True  # 0.80 >= 0.7

    def test_min_pass_rate_fail(self, failing_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Pass Rate", judge="overall", condition=PolicyCondition.MIN_PASS_RATE, threshold=0.7),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)
        assert scorecard.results[0].passed is False  # 0.40 < 0.7

    def test_max_warnings_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Max Warnings", judge="quality", condition=PolicyCondition.MAX_WARNINGS, threshold=10),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is True  # 3 <= 10

    def test_max_warnings_fail(self, failing_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Max Warnings", judge="quality", condition=PolicyCondition.MAX_WARNINGS, threshold=10),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)
        assert scorecard.results[0].passed is False  # 15 > 10

    def test_custom_condition_default_pass(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="Custom", judge="overall", condition=PolicyCondition.CUSTOM),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.results[0].passed is True  # Custom defaults to pass


# ============================================================================
# PolicyEngine — Aggregate Scoring Tests
# ============================================================================

class TestPolicyEngineAggregation:

    def test_all_rules_pass(self, sample_report_data):
        ps = PolicySet(name="Easy Policy", rules=[
            PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE, threshold=0.5),
            PolicyRule(id="r2", name="R2", judge="quality", condition=PolicyCondition.MIN_SCORE, threshold=0.5),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.overall_compliant is True
        assert scorecard.compliance_score == 100.0
        assert scorecard.passed_rules == 2
        assert scorecard.failed_rules == 0

    def test_some_rules_fail(self, sample_report_data):
        ps = PolicySet(name="Mixed Policy", rules=[
            PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE, threshold=0.5, severity=PolicySeverity.MEDIUM),
            PolicyRule(id="r2", name="R2", judge="grounding", condition=PolicyCondition.MIN_SCORE, threshold=0.9, severity=PolicySeverity.MEDIUM),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.passed_rules == 1
        assert scorecard.failed_rules == 1
        assert scorecard.compliance_score == 50.0
        # No critical violations, so still "compliant"
        assert scorecard.overall_compliant is True

    def test_critical_violation_makes_noncompliant(self, failing_report_data):
        ps = PolicySet(name="Strict Policy", rules=[
            PolicyRule(id="r1", name="No Critical", judge="safety", condition=PolicyCondition.ZERO_CRITICAL, severity=PolicySeverity.CRITICAL),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)
        assert scorecard.overall_compliant is False
        assert scorecard.has_critical_violations is True
        assert len(scorecard.critical_violations) == 1

    def test_summary_message_compliant(self, sample_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE, threshold=0.5),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert "COMPLIANT" in scorecard.summary
        assert "1/1" in scorecard.summary

    def test_summary_message_noncompliant(self, failing_report_data):
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="No Critical", judge="safety", condition=PolicyCondition.ZERO_CRITICAL, severity=PolicySeverity.CRITICAL),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)
        assert "NON-COMPLIANT" in scorecard.summary

    def test_empty_policy_set(self, sample_report_data):
        ps = PolicySet(name="Empty")
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)
        assert scorecard.total_rules == 0
        assert scorecard.compliance_score == 0.0
        assert scorecard.overall_compliant is True  # No critical violations


# ============================================================================
# Built-in Template Tests
# ============================================================================

class TestBuiltInPolicies:

    def test_all_built_ins_load(self):
        """Every built-in template must load without errors."""
        for name in BUILT_IN_POLICIES:
            ps = PolicyLoader.load_built_in(name)
            assert ps is not None, f"Built-in '{name}' failed to load"
            assert ps.rule_count > 0, f"Built-in '{name}' has no rules"

    def test_all_built_ins_validate(self):
        """Every built-in template must pass validation without warnings."""
        for name in BUILT_IN_POLICIES:
            ps = PolicyLoader.load_built_in(name)
            warnings = PolicyLoader.validate_policy_set(ps)
            assert len(warnings) == 0, f"Built-in '{name}' has validation warnings: {warnings}"

    def test_general_has_all_judges(self):
        ps = PolicyLoader.load_built_in("general")
        judges_covered = {r.judge for r in ps.rules}
        assert "safety" in judges_covered
        assert "quality" in judges_covered
        assert "grounding" in judges_covered
        assert "relevance" in judges_covered

    def test_healthcare_has_critical_safety(self):
        ps = PolicyLoader.load_built_in("healthcare")
        critical_safety = [
            r for r in ps.rules
            if r.judge == "safety" and r.severity == PolicySeverity.CRITICAL
        ]
        assert len(critical_safety) >= 2  # At least PII and safety score

    def test_finance_has_pii_rule(self):
        ps = PolicyLoader.load_built_in("finance")
        pii_rules = [r for r in ps.rules if "pii" in r.id.lower() or "pii" in str(r.tags)]
        assert len(pii_rules) >= 1

    def test_airline_has_booking_accuracy(self):
        ps = PolicyLoader.load_built_in("airline")
        booking_rules = [r for r in ps.rules if "booking" in r.id.lower() or "booking" in str(r.tags)]
        assert len(booking_rules) >= 1


# ============================================================================
# End-to-End Tests
# ============================================================================

class TestEndToEnd:

    def test_load_yaml_evaluate_report(self, sample_yaml_file, sample_report_data):
        """Full flow: load YAML → evaluate → check scorecard."""
        ps = PolicyLoader.load_from_file(sample_yaml_file)
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)

        assert scorecard.policy_set_name == "Test Policy"
        assert scorecard.total_rules == 2
        # safety_check: 0.95 >= 0.8 → PASS
        # quality_check: 0.70 >= 0.6 → PASS
        assert scorecard.passed_rules == 2
        assert scorecard.overall_compliant is True

    def test_built_in_general_against_passing_report(self, sample_report_data):
        ps = PolicyLoader.load_built_in("general")
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)

        assert scorecard.total_rules == ps.rule_count
        # With 0.65 grounding, some rules may fail, but no critical violations
        # (critical_failures=0 in sample_report_data)
        assert scorecard.results is not None

    def test_built_in_healthcare_against_failing_report(self, failing_report_data):
        ps = PolicyLoader.load_built_in("healthcare")
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(failing_report_data)

        # Healthcare has strict thresholds — failing report should be non-compliant
        assert scorecard.overall_compliant is False
        assert scorecard.has_critical_violations is True

    def test_built_in_finance_against_passing_report(self, sample_report_data):
        ps = PolicyLoader.load_built_in("finance")
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)

        # sample_report has 0 critical failures, so PII rule should pass
        pii_result = next((r for r in scorecard.results if "pii" in r.rule_id.lower()), None)
        assert pii_result is not None
        assert pii_result.passed is True

    def test_scorecard_export_roundtrip(self, sample_report_data):
        """Scorecard can be exported to dict and contains expected fields."""
        ps = PolicyLoader.load_built_in("general")
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(sample_report_data)

        export = scorecard.to_summary_dict()
        assert "policy_set" in export
        assert "overall_compliant" in export
        assert "compliance_score" in export
        assert "results" in export
        assert len(export["results"]) == scorecard.total_rules

    def test_multiple_policy_sets_sequential(self, sample_report_data):
        """Evaluate same report against multiple policy sets."""
        results = {}
        for name in ["general", "healthcare", "finance", "airline"]:
            ps = PolicyLoader.load_built_in(name)
            engine = PolicyEngine(ps)
            scorecard = engine.evaluate(sample_report_data)
            results[name] = scorecard

        # All should produce scorecards
        assert len(results) == 4
        for name, sc in results.items():
            assert sc.total_rules > 0, f"{name} has no rules"


# ============================================================================
# Edge Case Tests
# ============================================================================

class TestEdgeCases:

    def test_missing_judge_in_report(self):
        """If a judge is missing from report, score defaults to 0."""
        report = {
            "summary": {"pass_rate": 0.5, "critical_failures": 0, "warnings": 0, "average_score": 0.5},
            "score_by_judge": {},  # No judges at all
        }
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="R1", judge="safety", condition=PolicyCondition.MIN_SCORE, threshold=0.5),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(report)
        assert scorecard.results[0].passed is False  # 0.0 < 0.5
        assert scorecard.results[0].actual_value == 0.0

    def test_overall_judge_uses_average_score(self):
        """'overall' judge should use summary.average_score."""
        report = {
            "summary": {"pass_rate": 0.8, "critical_failures": 0, "warnings": 0, "average_score": 0.85},
            "score_by_judge": {},
        }
        ps = PolicySet(name="Test", rules=[
            PolicyRule(id="r1", name="R1", judge="overall", condition=PolicyCondition.MIN_SCORE, threshold=0.8),
        ])
        engine = PolicyEngine(ps)
        scorecard = engine.evaluate(report)
        assert scorecard.results[0].passed is True  # 0.85 >= 0.8

    def test_yaml_with_yml_extension(self, sample_policy_dict, tmp_path):
        """Should accept .yml extension too."""
        yml_path = tmp_path / "policy.yml"
        with open(yml_path, "w") as f:
            yaml.dump(sample_policy_dict, f)
        ps = PolicyLoader.load_from_file(yml_path)
        assert ps.name == "Test Policy"

    def test_policy_with_metadata(self):
        data = {
            "name": "Meta Policy",
            "rules": [
                {"id": "r1", "name": "R1", "judge": "safety", "condition": "min_score", "threshold": 0.5, "severity": "high"},
            ],
            "metadata": {"author": "test", "created": "2026-03-14"},
        }
        ps = PolicyLoader.load_from_dict(data)
        assert ps.metadata["author"] == "test"

    def test_not_a_dict_raises(self):
        with pytest.raises(PolicyLoadError, match="Expected dict"):
            PolicyLoader.load_from_dict("not a dict")

    def test_rules_not_list_raises(self):
        with pytest.raises(PolicyLoadError, match="rules"):
            PolicyLoader.load_from_dict({"name": "Test", "rules": "not a list"})

    def test_rule_not_dict_raises(self):
        with pytest.raises(PolicyLoadError, match="must be a mapping"):
            PolicyLoader.load_from_dict({"name": "Test", "rules": ["string_rule"]})
