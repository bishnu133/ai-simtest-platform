"""
Tests for Compare Mode (P1) — Regression detection.

Tests cover:
- ComparisonModels (MetricDelta, JudgeComparison, etc.)
- ComparisonEngine (delta computation, verdict logic, failure pattern matching)
- ComparisonReportGenerator (HTML + JSON output)
- Edge cases (missing data, identical reports, extreme regressions)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

# Adjust imports to match your project structure
# These imports assume files are in src/core/
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_simtest_engine.core.comparison_models import (
    ComparisonConfig,
    ComparisonResult,
    ComparisonSummary,
    DeltaDirection,
    FailurePatternDelta,
    JudgeComparison,
    MetricDelta,
    RegressionVerdict,
)
from ai_simtest_engine.core.comparison_engine import ComparisonEngine
from ai_simtest_engine.exporters.comparison_report import ComparisonReportGenerator


# ─── Fixtures ───────────────────────────────────────────────

def make_summary(
    name="Test Run",
    pass_rate=0.85,
    average_score=0.80,
    critical_failures=0,
    total_conversations=10,
    total_turns=50,
    score_by_judge=None,
    failure_patterns=None,
):
    """Create a mock summary dict."""
    return {
        "simulation_name": name,
        "pass_rate": pass_rate,
        "average_score": average_score,
        "critical_failures": critical_failures,
        "total_conversations": total_conversations,
        "total_turns": total_turns,
        "score_by_judge": score_by_judge if score_by_judge is not None else {
            "grounding": 0.75,
            "safety": 1.0,
            "quality": 0.72,
            "relevance": 0.80,
        },
        "failure_patterns": failure_patterns or [],
        "timestamp": "2026-02-27T10:00:00",
    }


@pytest.fixture
def baseline():
    return make_summary(name="v1.0 Baseline", pass_rate=0.85, average_score=0.80)


@pytest.fixture
def current_better():
    return make_summary(
        name="v1.1 Current",
        pass_rate=0.92,
        average_score=0.87,
        score_by_judge={"grounding": 0.82, "safety": 1.0, "quality": 0.80, "relevance": 0.85},
    )


@pytest.fixture
def current_worse():
    return make_summary(
        name="v1.1 Regression",
        pass_rate=0.70,
        average_score=0.65,
        critical_failures=3,
        score_by_judge={"grounding": 0.60, "safety": 0.90, "quality": 0.55, "relevance": 0.60},
    )


@pytest.fixture
def engine():
    return ComparisonEngine(ComparisonConfig(regression_threshold=0.05))


@pytest.fixture
def strict_engine():
    return ComparisonEngine(ComparisonConfig(
        regression_threshold=0.01,
        fail_if_regression=True,
        critical_failure_tolerance=0,
    ))


# ─── MetricDelta Tests ──────────────────────────────────────

class TestMetricDelta:
    def test_compute_improvement(self):
        m = MetricDelta.compute("pass_rate", 0.80, 0.90, higher_is_better=True)
        assert m.direction == DeltaDirection.IMPROVED
        assert m.is_regression is False
        assert m.delta == pytest.approx(0.10, abs=0.001)

    def test_compute_regression(self):
        m = MetricDelta.compute("pass_rate", 0.90, 0.75, higher_is_better=True)
        assert m.direction == DeltaDirection.REGRESSED
        assert m.is_regression is True

    def test_compute_unchanged(self):
        m = MetricDelta.compute("score", 0.85, 0.85, higher_is_better=True)
        assert m.direction == DeltaDirection.UNCHANGED
        assert m.is_regression is False

    def test_compute_lower_is_better(self):
        # Critical failures: going from 5 to 2 is improvement
        m = MetricDelta.compute("critical_failures", 5, 2, higher_is_better=False)
        assert m.direction == DeltaDirection.IMPROVED
        assert m.is_regression is False

    def test_compute_lower_is_better_regression(self):
        # Critical failures: going from 0 to 3 is regression
        m = MetricDelta.compute("critical_failures", 0, 3, higher_is_better=False)
        assert m.direction == DeltaDirection.REGRESSED
        assert m.is_regression is True

    def test_compute_from_zero_baseline(self):
        m = MetricDelta.compute("score", 0.0, 0.5, higher_is_better=True)
        assert m.direction == DeltaDirection.IMPROVED
        assert m.delta_percent == 100.0

    def test_delta_percent_calculation(self):
        m = MetricDelta.compute("score", 0.80, 0.84, higher_is_better=True)
        assert m.delta_percent == pytest.approx(5.0, abs=0.1)


# ─── ComparisonEngine Tests ─────────────────────────────────

class TestComparisonEngine:
    def test_compare_improvement(self, engine, baseline, current_better):
        result = engine.compare_dicts(baseline, current_better)
        assert result.summary.verdict in (RegressionVerdict.IMPROVED, RegressionVerdict.PASS)
        assert result.summary.pass_rate.direction == DeltaDirection.IMPROVED
        assert result.summary.total_regressions == 0
        assert result.exit_code == 0

    def test_compare_regression(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        assert result.summary.verdict == RegressionVerdict.FAIL
        assert result.summary.total_regressions > 0
        assert len(result.summary.verdict_reasons) > 0

    def test_compare_identical(self, engine, baseline):
        result = engine.compare_dicts(baseline, baseline)
        assert result.summary.verdict == RegressionVerdict.PASS
        assert result.summary.total_regressions == 0
        assert result.summary.pass_rate.direction == DeltaDirection.UNCHANGED

    def test_judge_comparison(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        judges = result.summary.judge_comparisons
        assert len(judges) == 4

        grounding = next(j for j in judges if j.judge_name == "grounding")
        assert grounding.direction == DeltaDirection.REGRESSED
        assert grounding.baseline_score == 0.75
        assert grounding.current_score == 0.60

    def test_judge_new_judge_in_current(self, engine):
        b = make_summary(score_by_judge={"grounding": 0.8})
        c = make_summary(score_by_judge={"grounding": 0.8, "tone": 0.9})
        result = engine.compare_dicts(b, c)
        judges = result.summary.judge_comparisons
        assert any(j.judge_name == "tone" for j in judges)

    def test_exit_code_with_fail_if_regression(self, strict_engine, baseline, current_worse):
        result = strict_engine.compare_dicts(baseline, current_worse)
        assert result.exit_code == 1

    def test_exit_code_no_regression(self, strict_engine, baseline, current_better):
        result = strict_engine.compare_dicts(baseline, current_better)
        assert result.exit_code == 0

    def test_pass_rate_floor(self):
        engine = ComparisonEngine(ComparisonConfig(pass_rate_floor=0.80))
        b = make_summary(pass_rate=0.85)
        c = make_summary(pass_rate=0.75)
        result = engine.compare_dicts(b, c)
        assert any("floor" in r.lower() for r in result.summary.verdict_reasons)

    def test_names_extracted(self, engine, baseline, current_better):
        result = engine.compare_dicts(baseline, current_better)
        assert result.summary.baseline_name == "v1.0 Baseline"
        assert result.summary.current_name == "v1.1 Current"


# ─── Failure Pattern Tests ───────────────────────────────────

class TestFailurePatterns:
    def test_new_failure_detected(self, engine):
        b = make_summary(failure_patterns=[])
        c = make_summary(failure_patterns=[
            {"pattern_name": "Hallucination", "frequency": 3, "severity": "high"}
        ])
        result = engine.compare_dicts(b, c)
        assert len(result.summary.new_failures) == 1
        assert result.summary.new_failures[0].pattern_name == "Hallucination"
        assert result.summary.new_failures[0].status == "new"

    def test_resolved_failure(self, engine):
        b = make_summary(failure_patterns=[
            {"pattern_name": "PII Leak", "frequency": 2, "severity": "critical"}
        ])
        c = make_summary(failure_patterns=[])
        result = engine.compare_dicts(b, c)
        assert len(result.summary.resolved_failures) == 1
        assert result.summary.resolved_failures[0].pattern_name == "PII Leak"

    def test_worsened_failure(self, engine):
        b = make_summary(failure_patterns=[
            {"pattern_name": "Off-topic", "frequency": 2, "severity": "medium"}
        ])
        c = make_summary(failure_patterns=[
            {"pattern_name": "Off-topic", "frequency": 5, "severity": "medium"}
        ])
        result = engine.compare_dicts(b, c)
        assert len(result.summary.worsened_failures) == 1
        assert result.summary.worsened_failures[0].delta == 3

    def test_improved_failure(self, engine):
        b = make_summary(failure_patterns=[
            {"pattern_name": "Slow response", "frequency": 8, "severity": "low"}
        ])
        c = make_summary(failure_patterns=[
            {"pattern_name": "Slow response", "frequency": 2, "severity": "low"}
        ])
        result = engine.compare_dicts(b, c)
        assert len(result.summary.improved_failures) == 1
        assert result.summary.improved_failures[0].delta == -6

    def test_persistent_failure(self, engine):
        b = make_summary(failure_patterns=[
            {"pattern_name": "Vague answer", "frequency": 4, "severity": "medium"}
        ])
        c = make_summary(failure_patterns=[
            {"pattern_name": "Vague answer", "frequency": 4, "severity": "medium"}
        ])
        result = engine.compare_dicts(b, c)
        assert len(result.summary.persistent_failures) == 1

    def test_multiple_pattern_changes(self, engine):
        b = make_summary(failure_patterns=[
            {"pattern_name": "PII Leak", "frequency": 3, "severity": "critical"},
            {"pattern_name": "Off-topic", "frequency": 5, "severity": "medium"},
        ])
        c = make_summary(failure_patterns=[
            {"pattern_name": "Off-topic", "frequency": 2, "severity": "medium"},
            {"pattern_name": "Hallucination", "frequency": 4, "severity": "high"},
        ])
        result = engine.compare_dicts(b, c)
        s = result.summary
        assert len(s.resolved_failures) == 1   # PII Leak gone
        assert len(s.new_failures) == 1         # Hallucination new
        assert len(s.improved_failures) == 1    # Off-topic 5 → 2


# ─── File I/O Tests ──────────────────────────────────────────

class TestFileIO:
    def test_compare_from_files(self, engine):
        with tempfile.TemporaryDirectory() as tmpdir:
            b_path = Path(tmpdir) / "baseline.json"
            c_path = Path(tmpdir) / "current.json"

            b_path.write_text(json.dumps(make_summary(name="Baseline", pass_rate=0.80)))
            c_path.write_text(json.dumps(make_summary(name="Current", pass_rate=0.90)))

            result = engine.compare(b_path, c_path)
            assert result.summary.pass_rate.direction == DeltaDirection.IMPROVED

    def test_compare_wrapped_format(self, engine):
        """Test loading reports with {summary: {...}, score_by_judge: {...}} format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            b_path = Path(tmpdir) / "baseline.json"
            c_path = Path(tmpdir) / "current.json"

            # Wrapped format (full report)
            b_path.write_text(json.dumps({
                "summary": {"simulation_name": "B", "pass_rate": 0.80, "average_score": 0.75,
                            "critical_failures": 0, "total_conversations": 10, "total_turns": 50},
                "score_by_judge": {"grounding": 0.7, "safety": 1.0},
                "failure_patterns": [],
            }))
            c_path.write_text(json.dumps({
                "summary": {"simulation_name": "C", "pass_rate": 0.90, "average_score": 0.85,
                            "critical_failures": 0, "total_conversations": 10, "total_turns": 60},
                "score_by_judge": {"grounding": 0.8, "safety": 1.0},
                "failure_patterns": [],
            }))

            result = engine.compare(b_path, c_path)
            assert result.summary.pass_rate.direction == DeltaDirection.IMPROVED

    def test_file_not_found(self, engine):
        with pytest.raises(FileNotFoundError):
            engine.compare("/nonexistent/baseline.json", "/nonexistent/current.json")


# ─── Report Generator Tests ─────────────────────────────────

class TestComparisonReport:
    def test_html_report_generated(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        gen = ComparisonReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate(result, Path(tmpdir) / "comparison.html")
            assert path.exists()
            content = path.read_text()
            assert "AI SimTest" in content
            assert "Comparison Report" in content
            assert "REGRESSION" in content.upper() or "FAIL" in content.upper()

    def test_html_report_improvement(self, engine, baseline, current_better):
        result = engine.compare_dicts(baseline, current_better)
        gen = ComparisonReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate(result, Path(tmpdir) / "comparison.html")
            content = path.read_text()
            assert "IMPROVED" in content.upper() or "PASS" in content.upper()

    def test_json_report_generated(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        gen = ComparisonReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate_json(result, Path(tmpdir) / "comparison.json")
            assert path.exists()
            data = json.loads(path.read_text())
            assert "summary" in data
            assert "verdict" in data["summary"]

    def test_html_contains_judge_chart(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        gen = ComparisonReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate(result, Path(tmpdir) / "comparison.html")
            content = path.read_text()
            assert "judgeChart" in content
            assert "Chart(" in content

    def test_html_contains_failure_patterns(self, engine):
        b = make_summary(failure_patterns=[
            {"pattern_name": "Leak", "frequency": 2, "severity": "critical"}
        ])
        c = make_summary(failure_patterns=[
            {"pattern_name": "Hallucination", "frequency": 3, "severity": "high"}
        ])
        result = engine.compare_dicts(b, c)
        gen = ComparisonReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate(result, Path(tmpdir) / "comparison.html")
            content = path.read_text()
            assert "Leak" in content
            assert "Hallucination" in content
            assert "RESOLVED" in content.upper()
            assert "NEW" in content.upper()

    def test_html_cicd_gate_section(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        gen = ComparisonReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate(result, Path(tmpdir) / "comparison.html")
            content = path.read_text()
            assert "CI/CD Gate" in content
            assert "Exit code" in content


# ─── Verdict Logic Tests ─────────────────────────────────────

class TestVerdictLogic:
    def test_verdict_pass_no_changes(self, engine, baseline):
        result = engine.compare_dicts(baseline, baseline)
        assert result.summary.verdict == RegressionVerdict.PASS

    def test_verdict_improved(self, engine, baseline, current_better):
        result = engine.compare_dicts(baseline, current_better)
        assert result.summary.verdict == RegressionVerdict.IMPROVED

    def test_verdict_fail_on_critical_regression(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        assert result.summary.verdict == RegressionVerdict.FAIL

    def test_verdict_warning_minor_regression(self):
        engine = ComparisonEngine(ComparisonConfig(regression_threshold=0.05))
        b = make_summary(pass_rate=0.85, average_score=0.80)
        # Small regression in one judge only, pass rate unchanged
        c = make_summary(
            pass_rate=0.85,
            average_score=0.80,
            score_by_judge={"grounding": 0.70, "safety": 1.0, "quality": 0.72, "relevance": 0.80},
        )
        result = engine.compare_dicts(b, c)
        # Should be WARNING (minor regression in grounding only)
        assert result.summary.verdict in (RegressionVerdict.WARNING, RegressionVerdict.PASS)

    def test_critical_failure_tolerance(self):
        engine = ComparisonEngine(ComparisonConfig(critical_failure_tolerance=2))
        b = make_summary(critical_failures=0)
        c = make_summary(critical_failures=2)
        result = engine.compare_dicts(b, c)
        # 2 new critical failures, tolerance is 2 → should not fail just for this
        crit_reasons = [r for r in result.summary.verdict_reasons if "critical failure" in r.lower()]
        assert len(crit_reasons) == 0  # Within tolerance


# ─── Edge Cases ──────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_score_by_judge(self, engine):
        b = make_summary(score_by_judge={})
        c = make_summary(score_by_judge={})
        result = engine.compare_dicts(b, c)
        assert result.summary.judge_comparisons == []

    def test_missing_fields_graceful(self, engine):
        b = {"pass_rate": 0.80}
        c = {"pass_rate": 0.90}
        result = engine.compare_dicts(b, c)
        assert result.summary.pass_rate.direction == DeltaDirection.IMPROVED

    def test_zero_baseline_pass_rate(self, engine):
        b = make_summary(pass_rate=0.0)
        c = make_summary(pass_rate=0.5)
        result = engine.compare_dicts(b, c)
        assert result.summary.pass_rate.direction == DeltaDirection.IMPROVED

    def test_perfect_to_perfect(self, engine):
        b = make_summary(pass_rate=1.0, average_score=1.0)
        c = make_summary(pass_rate=1.0, average_score=1.0)
        result = engine.compare_dicts(b, c)
        assert result.summary.verdict == RegressionVerdict.PASS

    def test_comparison_result_serializable(self, engine, baseline, current_worse):
        result = engine.compare_dicts(baseline, current_worse)
        # Should serialize to JSON without errors
        json_str = json.dumps(result.model_dump(), default=str)
        assert len(json_str) > 100
        parsed = json.loads(json_str)
        assert parsed["summary"]["verdict"] == "fail"
