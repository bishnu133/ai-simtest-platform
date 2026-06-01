"""
Tests for Phase 10 features: Iterative Refinement, Regression Suite, Comparison Mode.
Run with: PYTHONPATH=. pytest tests/test_phase10.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.models import (
    Conversation,
    FailurePattern,
    JudgedConversation,
    JudgedTurn,
    JudgmentLabel,
    JudgmentResult,
    Persona,
    PersonaType,
    ReportSummary,
    Severity,
    SimulationReport,
    Turn,
)


# ============================================================
# Fixtures: Build realistic test data
# ============================================================

def _make_judgment(judge: str, passed: bool, score: float) -> JudgmentResult:
    return JudgmentResult(
        judge_name=judge,
        passed=passed,
        score=score,
        message=f"{judge}: {'pass' if passed else 'fail'}",
    )


def _make_judged_conv(
    persona_name: str,
    persona_type: str,
    tone: str,
    topics: list[str],
    overall_score: float,
    labels: list[str],
    failure_modes: list[str] | None = None,
) -> JudgedConversation:
    persona = Persona(
        name=persona_name,
        role="tester",
        goals=["test the bot"],
        tone=tone,
        persona_type=PersonaType(persona_type),
        topics=topics,
    )
    conv = Conversation(
        persona_id=persona.id,
        turns=[
            Turn(speaker="user", message="Hello, I need help"),
            Turn(speaker="bot", message="How can I help?"),
            Turn(speaker="user", message="I want a refund"),
            Turn(speaker="bot", message="I can help with that"),
        ],
    )
    judged_turns = []
    for label_str in labels:
        label = JudgmentLabel(label_str)
        passed = label == JudgmentLabel.PASS
        score = 0.85 if passed else (0.55 if label == JudgmentLabel.WARNING else 0.3)
        judged_turns.append(JudgedTurn(
            turn=Turn(speaker="bot", message="bot response"),
            judgments=[
                _make_judgment("grounding", passed, score),
                _make_judgment("safety", True, 1.0),
                _make_judgment("quality", passed, score),
                _make_judgment("relevance", passed, score),
            ],
            overall_label=label,
            overall_score=score,
            issues=[] if passed else [f"{persona_name} issue"],
        ))

    return JudgedConversation(
        conversation=conv,
        persona=persona,
        judged_turns=judged_turns,
        overall_score=overall_score,
        failure_modes=failure_modes or [],
    )


@pytest.fixture
def sample_report() -> SimulationReport:
    """A sample report with a mix of passes, warnings, and failures."""
    conversations = [
        # High-risk: adversarial, frustrated tone, low score
        _make_judged_conv("Angry Attacker", "adversarial", "angry", ["security", "jailbreak"], 0.2, ["FAIL", "FAIL"], ["prompt_leak", "off_topic"]),
        _make_judged_conv("Sneaky Injector", "adversarial", "neutral", ["security"], 0.3, ["FAIL", "WARNING"], ["prompt_leak"]),
        # Edge case failures
        _make_judged_conv("Confused Novice", "edge_case", "confused", ["refunds", "billing"], 0.4, ["FAIL", "PASS"], ["unhelpful_response"]),
        _make_judged_conv("Non-English Speaker", "edge_case", "polite", ["general"], 0.5, ["WARNING", "WARNING"], ["unclear_response"]),
        # Standard passes
        _make_judged_conv("Happy Customer", "standard", "friendly", ["products"], 0.9, ["PASS", "PASS"]),
        _make_judged_conv("Tech Expert", "standard", "neutral", ["technical"], 0.85, ["PASS", "PASS"]),
        _make_judged_conv("Casual Browser", "standard", "casual", ["products", "shipping"], 0.8, ["PASS", "WARNING"]),
    ]

    return SimulationReport(
        summary=ReportSummary(
            simulation_id="sim_test001",
            simulation_name="Test Run v1",
            total_personas=7,
            total_conversations=7,
            total_turns=28,
            pass_rate=0.57,
            average_score=0.56,
            critical_failures=3,
            warnings=4,
            execution_time_seconds=12.5,
        ),
        failure_patterns=[
            FailurePattern(pattern_name="prompt_leak", description="Bot leaked system prompt", frequency=2, severity=Severity.CRITICAL),
            FailurePattern(pattern_name="unhelpful_response", description="Response not helpful", frequency=1, severity=Severity.MEDIUM),
        ],
        score_by_judge={"grounding": 0.65, "safety": 0.90, "quality": 0.55, "relevance": 0.60},
        score_by_persona_type={"standard": 0.85, "edge_case": 0.45, "adversarial": 0.25},
        recommendations=["Strengthen prompt injection defenses", "Improve responses for confused users"],
        judged_conversations=conversations,
    )


@pytest.fixture
def sample_report_improved() -> SimulationReport:
    """An improved version of the report (for comparison testing)."""
    conversations = [
        _make_judged_conv("Angry Attacker", "adversarial", "angry", ["security"], 0.5, ["WARNING", "PASS"], ["off_topic"]),
        _make_judged_conv("Sneaky Injector", "adversarial", "neutral", ["security"], 0.6, ["PASS", "PASS"]),
        _make_judged_conv("Confused Novice", "edge_case", "confused", ["refunds"], 0.7, ["PASS", "PASS"]),
        _make_judged_conv("Non-English Speaker", "edge_case", "polite", ["general"], 0.65, ["PASS", "WARNING"]),
        _make_judged_conv("Happy Customer", "standard", "friendly", ["products"], 0.92, ["PASS", "PASS"]),
        _make_judged_conv("Tech Expert", "standard", "neutral", ["technical"], 0.88, ["PASS", "PASS"]),
        _make_judged_conv("Casual Browser", "standard", "casual", ["products"], 0.85, ["PASS", "PASS"]),
    ]

    return SimulationReport(
        summary=ReportSummary(
            simulation_id="sim_test002",
            simulation_name="Test Run v2",
            total_personas=7,
            total_conversations=7,
            total_turns=28,
            pass_rate=0.78,
            average_score=0.73,
            critical_failures=0,
            warnings=2,
            execution_time_seconds=11.2,
        ),
        failure_patterns=[
            FailurePattern(pattern_name="off_topic", description="Bot went off topic", frequency=1, severity=Severity.LOW),
        ],
        score_by_judge={"grounding": 0.80, "safety": 0.95, "quality": 0.72, "relevance": 0.75},
        score_by_persona_type={"standard": 0.88, "edge_case": 0.68, "adversarial": 0.55},
        recommendations=["Monitor adversarial edge cases"],
        judged_conversations=conversations,
    )


# ============================================================
# Tests: Iterative Persona Refinement
# ============================================================

class TestPersonaRefiner:
    """Tests for src/generators/persona_refiner.py"""

    def test_analyze_failures_finds_high_risk(self, sample_report):
        from src.generators.persona_refiner import PersonaRefiner
        refiner = PersonaRefiner()
        analysis = refiner.analyze_failures(sample_report)

        assert "high_risk_personas" in analysis
        assert len(analysis["high_risk_personas"]) > 0
        # Angry Attacker and Sneaky Injector should be high risk (score < 0.5)
        high_risk_names = [ps["persona"].name for ps in analysis["high_risk_personas"]]
        assert "Angry Attacker" in high_risk_names

    def test_analyze_failures_by_type(self, sample_report):
        from src.generators.persona_refiner import PersonaRefiner
        refiner = PersonaRefiner()
        analysis = refiner.analyze_failures(sample_report)

        by_type = analysis["failure_by_type"]
        assert "adversarial" in by_type
        assert "standard" in by_type
        # Adversarial should have highest failure rate
        assert by_type["adversarial"] > by_type["standard"]

    def test_analyze_failures_by_topic(self, sample_report):
        from src.generators.persona_refiner import PersonaRefiner
        refiner = PersonaRefiner()
        analysis = refiner.analyze_failures(sample_report)

        by_topic = analysis["failure_by_topic"]
        assert "security" in by_topic
        assert by_topic["security"] >= 1

    def test_analyze_failures_strategy(self, sample_report):
        from src.generators.persona_refiner import PersonaRefiner
        refiner = PersonaRefiner()
        analysis = refiner.analyze_failures(sample_report)

        strategy = analysis["refinement_strategy"]
        assert len(strategy) > 0
        # Should mention adversarial or high failure rate
        strategy_text = " ".join(strategy).lower()
        assert "adversarial" in strategy_text or "failure" in strategy_text

    def test_analyze_empty_report(self):
        from src.generators.persona_refiner import PersonaRefiner
        refiner = PersonaRefiner()

        empty_report = SimulationReport(
            summary=ReportSummary(
                simulation_id="empty", simulation_name="Empty",
                total_personas=0, total_conversations=0, total_turns=0,
                pass_rate=1.0, average_score=1.0,
                critical_failures=0, warnings=0, execution_time_seconds=0,
            ),
        )
        analysis = refiner.analyze_failures(empty_report)
        assert analysis["high_risk_personas"] == []

    def test_build_refinement_prompt(self, sample_report):
        from src.generators.persona_refiner import PersonaRefiner
        refiner = PersonaRefiner()
        analysis = refiner.analyze_failures(sample_report)

        prompt = refiner.build_refinement_prompt(
            analysis=analysis,
            num_personas=5,
            original_bot_description="Customer support bot",
            original_documentation="Handles refunds and billing",
        )

        assert "5" in prompt  # num_personas
        assert "Angry Attacker" in prompt or "high-risk" in prompt.lower()
        assert "JSON" in prompt


# ============================================================
# Tests: Regression Suite
# ============================================================

class TestRegressionSuiteManager:
    """Tests for src/regression/suite_manager.py"""

    def test_create_suite_from_report(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report, name="Test Suite")

        assert suite.name == "Test Suite"
        assert suite.total_cases > 0
        # Should include failing conversations but not all-passing ones
        assert suite.total_cases < len(sample_report.judged_conversations)

    def test_suite_includes_failures(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report)

        fail_cases = [tc for tc in suite.test_cases if tc.original_label == "FAIL"]
        assert len(fail_cases) > 0

    def test_suite_includes_warnings(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report, include_warnings=True)

        warning_cases = [tc for tc in suite.test_cases if tc.original_label == "WARNING"]
        # Should have some warnings
        assert suite.total_cases >= 2  # at least the FAIL cases

    def test_suite_excludes_warnings_when_disabled(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite_with = mgr.create_from_report(sample_report, include_warnings=True)
        suite_without = mgr.create_from_report(sample_report, include_warnings=False)

        # Without warnings should have fewer or equal cases
        assert suite_without.total_cases <= suite_with.total_cases

    def test_suite_has_user_messages(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report)

        for tc in suite.test_cases:
            assert len(tc.user_messages) > 0
            assert all(isinstance(m, str) for m in tc.user_messages)

    def test_suite_has_tags(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report)

        # At least some cases should have tags (from failed judge names)
        cases_with_tags = [tc for tc in suite.test_cases if tc.tags]
        assert len(cases_with_tags) > 0

    def test_save_and_load_suite(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report, name="Persist Test")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = mgr.save_suite(suite, Path(tmpdir) / "suite.json")
            assert path.exists()

            loaded = mgr.load_suite(path)
            assert loaded.name == "Persist Test"
            assert loaded.total_cases == suite.total_cases
            assert len(loaded.test_cases) == len(suite.test_cases)

    def test_suite_max_cases(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report, max_cases=2)

        assert suite.total_cases <= 2

    def test_suite_serialization_roundtrip(self, sample_report):
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(sample_report)

        # Serialize to dict and back
        data = suite.model_dump()
        json_str = json.dumps(data, default=str)
        parsed = json.loads(json_str)

        from src.regression.suite_manager import RegressionSuite
        restored = RegressionSuite(**parsed)
        assert restored.total_cases == suite.total_cases


# ============================================================
# Tests: Comparison Engine
# ============================================================

class TestComparisonEngine:
    """Tests for src/comparison/engine.py"""

    def _export_summary(self, report: SimulationReport, path: Path):
        """Helper to export a summary.json from a report."""
        from src.exporters.dataset_exporter import DatasetExporter
        exporter = DatasetExporter()
        exporter.export_summary_json(report, path)

    def test_compare_detects_improvement(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            assert report.overall_verdict == "improved"
            assert report.before_name == "Test Run v1"
            assert report.after_name == "Test Run v2"

    def test_compare_pass_rate_delta(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            pass_rate_delta = next(d for d in report.summary_deltas if d.metric == "Pass Rate")
            assert pass_rate_delta.delta > 0
            assert pass_rate_delta.status == "improved"

    def test_compare_judge_deltas(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            # All judges should show improvement
            for jd in report.judge_deltas:
                assert jd.delta >= 0, f"Judge {jd.judge_name} regressed"

    def test_compare_failure_patterns(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            # prompt_leak was resolved, off_topic is new or persistent
            assert len(report.resolved_failures) >= 1

    def test_compare_persona_types(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            # Adversarial should show most improvement
            adv_delta = next(
                (d for d in report.persona_type_deltas if d.persona_type == "adversarial"),
                None,
            )
            assert adv_delta is not None
            assert adv_delta.delta > 0

    def test_compare_same_report(self, sample_report):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "same.json"
            self._export_summary(sample_report, path)

            engine = ComparisonEngine()
            report = engine.compare_files(path, path)

            assert report.overall_verdict == "unchanged"

    def test_compare_critical_failures_delta(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            crit_delta = next(d for d in report.summary_deltas if d.metric == "Critical Failures")
            # Fewer critical failures = improved
            assert crit_delta.status == "improved"
            assert crit_delta.delta < 0

    def test_format_console(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)
            output = engine.format_console(report)

            assert "COMPARISON" in output
            assert "IMPROVED" in output or "improved" in output.lower()
            assert "Pass Rate" in output

    def test_save_comparison(self, sample_report, sample_report_improved):
        from src.comparison.engine import ComparisonEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.json"
            after_path = Path(tmpdir) / "after.json"
            self._export_summary(sample_report, before_path)
            self._export_summary(sample_report_improved, after_path)

            engine = ComparisonEngine()
            report = engine.compare_files(before_path, after_path)

            out_path = Path(tmpdir) / "comparison.json"
            engine.save_comparison(report, out_path)

            assert out_path.exists()
            with open(out_path) as f:
                data = json.load(f)
            assert data["overall_verdict"] == "improved"


# ============================================================
# Tests: Regression Suite Models
# ============================================================

class TestRegressionModels:
    """Tests for the regression suite Pydantic models."""

    def test_regression_test_case(self):
        from src.regression.suite_manager import RegressionTestCase
        tc = RegressionTestCase(
            id="tc_001",
            source_conversation_id="conv_001",
            source_persona_name="Angry User",
            source_persona_type="adversarial",
            user_messages=["Hello", "I want a refund"],
            original_bot_responses=["Hi", "Sure"],
            original_score=0.3,
            original_label="FAIL",
            failure_modes=["prompt_leak"],
            tags=["safety"],
        )
        assert tc.id == "tc_001"
        assert len(tc.user_messages) == 2
        assert tc.original_label == "FAIL"

    def test_regression_suite(self):
        from src.regression.suite_manager import RegressionSuite, RegressionTestCase
        suite = RegressionSuite(
            id="suite_001",
            name="Test Suite",
            test_cases=[
                RegressionTestCase(
                    id="tc_001",
                    source_conversation_id="conv_001",
                    source_persona_name="User",
                    source_persona_type="standard",
                    user_messages=["Hi"],
                    original_bot_responses=["Hello"],
                    original_score=0.3,
                    original_label="FAIL",
                    failure_modes=[],
                ),
            ],
        )
        assert suite.total_cases == 1

    def test_replay_result(self):
        from src.regression.suite_manager import ReplayResult
        result = ReplayResult(
            test_case_id="tc_001",
            persona_name="User",
            original_label="FAIL",
            new_label="PASS",
            original_score=0.3,
            new_score=0.85,
            status="fixed",
            original_failures=["bad response"],
            new_failures=[],
            new_bot_responses=["Good response"],
        )
        assert result.status == "fixed"
        assert result.new_score > result.original_score

    def test_replay_summary(self):
        from src.regression.suite_manager import ReplaySummary
        summary = ReplaySummary(
            suite_name="Test",
            total_cases=4,
            fixed=2,
            regressed=1,
            still_failing=1,
        )
        assert summary.fixed == 2
        assert summary.regressed == 1


# ============================================================
# Tests: Comparison Models
# ============================================================

class TestComparisonModels:
    """Tests for the comparison Pydantic models."""

    def test_metric_delta(self):
        from src.comparison.engine import MetricDelta
        d = MetricDelta(
            metric="Pass Rate",
            before=0.57,
            after=0.78,
            delta=0.21,
            pct_change=36.8,
            status="improved",
        )
        assert d.status == "improved"
        assert d.delta > 0

    def test_comparison_report(self):
        from src.comparison.engine import ComparisonReport, MetricDelta
        report = ComparisonReport(
            before_name="v1",
            after_name="v2",
            summary_deltas=[
                MetricDelta(metric="Pass Rate", before=0.5, after=0.8, delta=0.3, pct_change=60.0, status="improved"),
            ],
            overall_verdict="improved",
            verdict_reason="Pass rate improved by 30%",
        )
        assert report.overall_verdict == "improved"
        assert len(report.summary_deltas) == 1
