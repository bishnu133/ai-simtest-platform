"""
Test suite for Judge Calibration Suite (P2 #12).

Tests:
- Golden example model serialization
- GoldenDatasetManager (load, filter, validate, save)
- CalibrationRunner (verdict correctness logic)
- CalibrationAnalyzer (accuracy, confusion, agreement, recommendations)
- JudgeVersionTracker (register, compare, persist)
- Integration (full pipeline: load → run → analyze)
"""

try:
    import pytest
except ImportError:
    pass

import json
import tempfile
from pathlib import Path

from ai_simtest_engine.calibration import (
    GoldenDatasetManager,
    CalibrationRunner,
    CalibrationAnalyzer,
    JudgeVersionTracker,
    get_built_in_examples,
    GoldenExample,
    ExampleVerdict,
    ExpectedVerdict,
    ExampleCategory,
    CalibrationGrade,
    CalibrationReport,
    JudgeAccuracy,
)


# ━━━ Test: Golden Example Model ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestGoldenExample:

    def test_roundtrip_serialization(self):
        ex = GoldenExample(
            id="test_001", target_judge="safety",
            category=ExampleCategory.SAFETY_PII,
            description="Test PII",
            user_message="Hello", bot_response="Your SSN is 123-45-6789",
            expected_verdict=ExpectedVerdict.FAIL,
            expected_score_min=0.0, expected_score_max=0.3,
            tags=["pii"], difficulty="standard",
        )
        data = ex.to_dict()
        restored = GoldenExample.from_dict(data)
        assert restored.id == ex.id
        assert restored.target_judge == ex.target_judge
        assert restored.expected_verdict == ex.expected_verdict
        assert restored.expected_score_min == ex.expected_score_min

    def test_default_values(self):
        ex = GoldenExample(
            id="t", target_judge="quality",
            category=ExampleCategory.QUALITY_HIGH,
            description="d", user_message="q", bot_response="a",
        )
        assert ex.expected_verdict == ExpectedVerdict.PASS
        assert ex.expected_score_min == 0.0
        assert ex.expected_score_max == 1.0
        assert ex.tags == []


# ━━━ Test: Built-in Examples ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBuiltInExamples:

    def test_count(self):
        examples = get_built_in_examples()
        assert len(examples) == 42

    def test_all_judges_covered(self):
        examples = get_built_in_examples()
        judges = set(e.target_judge for e in examples)
        assert judges == {"grounding", "safety", "quality", "relevance"}

    def test_grounding_has_docs(self):
        examples = get_built_in_examples()
        for e in examples:
            if e.target_judge == "grounding":
                assert e.documentation, f"{e.id} missing documentation"

    def test_unique_ids(self):
        examples = get_built_in_examples()
        ids = [e.id for e in examples]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"

    def test_score_ranges_valid(self):
        examples = get_built_in_examples()
        for e in examples:
            assert e.expected_score_min <= e.expected_score_max, f"{e.id} invalid range"

    def test_has_pass_and_fail_examples(self):
        examples = get_built_in_examples()
        pass_count = sum(1 for e in examples if e.expected_verdict == ExpectedVerdict.PASS)
        fail_count = sum(1 for e in examples if e.expected_verdict == ExpectedVerdict.FAIL)
        assert pass_count > 0, "No PASS examples"
        assert fail_count > 0, "No FAIL examples"


# ━━━ Test: GoldenDatasetManager ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestGoldenDatasetManager:

    def test_load_built_in(self):
        mgr = GoldenDatasetManager()
        count = mgr.load_built_in()
        assert count == 42
        assert mgr.count == 42

    def test_filter_by_judge(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        safety = mgr.filter_by_judge("safety")
        assert len(safety) == 12
        assert all(e.target_judge == "safety" for e in safety)

    def test_filter_by_category(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        pii = mgr.filter_by_category(ExampleCategory.SAFETY_PII)
        assert len(pii) > 0
        assert all(e.category == ExampleCategory.SAFETY_PII for e in pii)

    def test_filter_by_tags(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        toxic = mgr.filter_by_tags(["toxic"])
        assert len(toxic) > 0

    def test_get_judge_names(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        names = mgr.get_judge_names()
        assert names == ["grounding", "quality", "relevance", "safety"]

    def test_get_stats(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        stats = mgr.get_stats()
        assert stats["total"] == 42
        assert "grounding" in stats["by_judge"]
        assert "pass" in stats["by_expected_verdict"]

    def test_validate_built_in(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        issues = mgr.validate()
        assert issues == [], f"Built-in examples have issues: {issues}"

    def test_save_and_load(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            mgr.save_to_file(f.name)
            mgr2 = GoldenDatasetManager()
            loaded = mgr2.load_from_file(f.name)
            assert loaded == 42
            assert mgr2.count == 42

    def test_add_and_remove(self):
        mgr = GoldenDatasetManager()
        ex = GoldenExample(
            id="custom_001", target_judge="quality",
            category=ExampleCategory.QUALITY_HIGH,
            description="Custom", user_message="Q", bot_response="A",
        )
        mgr.add_example(ex)
        assert mgr.count == 1
        removed = mgr.remove_by_id("custom_001")
        assert removed is True
        assert mgr.count == 0

    def test_clear(self):
        mgr = GoldenDatasetManager()
        mgr.load_built_in()
        mgr.clear()
        assert mgr.count == 0


# ━━━ Test: CalibrationRunner ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCalibrationRunner:

    def _make_example(self, verdict=ExpectedVerdict.PASS, score_min=0.7, score_max=1.0):
        return GoldenExample(
            id="test", target_judge="quality",
            category=ExampleCategory.QUALITY_HIGH,
            description="Test", user_message="Q", bot_response="A",
            expected_verdict=verdict,
            expected_score_min=score_min, expected_score_max=score_max,
        )

    def test_correct_pass(self):
        runner = CalibrationRunner()
        v = runner.evaluate_example_with_result(
            self._make_example(ExpectedVerdict.PASS, 0.7, 1.0),
            actual_score=0.85, actual_passed=True,
        )
        assert v.verdict_correct is True
        assert v.score_in_range is True
        assert v.overall_correct is True

    def test_correct_fail(self):
        runner = CalibrationRunner()
        v = runner.evaluate_example_with_result(
            self._make_example(ExpectedVerdict.FAIL, 0.0, 0.3),
            actual_score=0.1, actual_passed=False,
        )
        assert v.verdict_correct is True
        assert v.score_in_range is True
        assert v.overall_correct is True

    def test_false_positive(self):
        runner = CalibrationRunner()
        v = runner.evaluate_example_with_result(
            self._make_example(ExpectedVerdict.FAIL, 0.0, 0.3),
            actual_score=0.9, actual_passed=True,
        )
        assert v.verdict_correct is False
        assert v.score_in_range is False
        assert v.overall_correct is False

    def test_false_negative(self):
        runner = CalibrationRunner()
        v = runner.evaluate_example_with_result(
            self._make_example(ExpectedVerdict.PASS, 0.7, 1.0),
            actual_score=0.2, actual_passed=False,
        )
        assert v.verdict_correct is False
        assert v.score_in_range is False
        assert v.overall_correct is False

    def test_score_out_of_range_but_verdict_correct(self):
        runner = CalibrationRunner()
        v = runner.evaluate_example_with_result(
            self._make_example(ExpectedVerdict.PASS, 0.7, 0.9),
            actual_score=0.95, actual_passed=True,
        )
        assert v.verdict_correct is True
        assert v.score_in_range is False
        assert v.overall_correct is False

    def test_warning_always_correct_verdict(self):
        runner = CalibrationRunner()
        v = runner.evaluate_example_with_result(
            self._make_example(ExpectedVerdict.WARNING, 0.4, 0.6),
            actual_score=0.5, actual_passed=True,  # Either pass or fail is OK for WARNING
        )
        assert v.verdict_correct is True

    def test_run_batch(self):
        runner = CalibrationRunner()
        examples = [
            GoldenExample(
                id="b1", target_judge="safety",
                category=ExampleCategory.SAFETY_CLEAN,
                description="clean", user_message="Q", bot_response="A",
                expected_verdict=ExpectedVerdict.PASS,
                expected_score_min=0.8, expected_score_max=1.0,
            ),
            GoldenExample(
                id="b2", target_judge="safety",
                category=ExampleCategory.SAFETY_PII,
                description="pii", user_message="Q", bot_response="SSN: 123",
                expected_verdict=ExpectedVerdict.FAIL,
                expected_score_min=0.0, expected_score_max=0.3,
            ),
        ]
        results = {
            "b1": (0.9, True, "clean"),
            "b2": (0.1, False, "PII detected"),
        }
        verdicts = runner.run_batch(examples, results)
        assert len(verdicts) == 2
        assert all(v.overall_correct for v in verdicts)


# ━━━ Test: CalibrationAnalyzer ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCalibrationAnalyzer:

    def _make_verdicts(self) -> list[ExampleVerdict]:
        """Create sample verdicts for testing."""
        return [
            ExampleVerdict(
                example_id="g1", target_judge="grounding",
                expected_verdict=ExpectedVerdict.PASS,
                expected_score_min=0.7, expected_score_max=1.0,
                actual_score=0.85, actual_passed=True,
                score_in_range=True, verdict_correct=True, overall_correct=True,
            ),
            ExampleVerdict(
                example_id="g2", target_judge="grounding",
                expected_verdict=ExpectedVerdict.FAIL,
                expected_score_min=0.0, expected_score_max=0.4,
                actual_score=0.1, actual_passed=False,
                score_in_range=True, verdict_correct=True, overall_correct=True,
            ),
            ExampleVerdict(
                example_id="s1", target_judge="safety",
                expected_verdict=ExpectedVerdict.PASS,
                expected_score_min=0.8, expected_score_max=1.0,
                actual_score=0.95, actual_passed=True,
                score_in_range=True, verdict_correct=True, overall_correct=True,
            ),
            ExampleVerdict(
                example_id="s2", target_judge="safety",
                expected_verdict=ExpectedVerdict.FAIL,
                expected_score_min=0.0, expected_score_max=0.3,
                actual_score=0.8, actual_passed=True,  # FALSE POSITIVE
                score_in_range=False, verdict_correct=False, overall_correct=False,
            ),
        ]

    def test_overall_accuracy(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        assert report.total_examples == 4
        assert report.total_correct == 3
        assert report.overall_accuracy == 0.75

    def test_per_judge_accuracy(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        assert "grounding" in report.judge_accuracy
        assert "safety" in report.judge_accuracy
        assert report.judge_accuracy["grounding"].overall_accuracy == 1.0
        assert report.judge_accuracy["safety"].overall_accuracy == 0.5

    def test_confusion_matrix(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        safety = report.judge_accuracy["safety"]
        assert safety.true_positives == 1
        assert safety.false_positives == 1

    def test_strongest_weakest(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        assert report.strongest_judge == "grounding"
        assert report.weakest_judge == "safety"

    def test_grade_assignment(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        assert report.overall_grade == CalibrationGrade.GOOD  # 75%

    def test_empty_verdicts(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze([])
        assert report.total_examples == 0
        assert report.overall_accuracy == 0.0

    def test_recommendations_generated(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        assert len(report.recommendations) > 0

    def test_agreements_computed(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        assert len(report.agreements) > 0

    def test_serialization(self):
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(self._make_verdicts())
        data = report.to_dict()
        assert "overall_accuracy" in data
        assert "judges" in data
        assert "agreements" in data


# ━━━ Test: CalibrationGrade ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCalibrationGrade:

    def test_excellent(self):
        assert CalibrationGrade.from_accuracy(0.95) == CalibrationGrade.EXCELLENT

    def test_good(self):
        assert CalibrationGrade.from_accuracy(0.80) == CalibrationGrade.GOOD

    def test_needs_tuning(self):
        assert CalibrationGrade.from_accuracy(0.65) == CalibrationGrade.NEEDS_TUNING

    def test_poor(self):
        assert CalibrationGrade.from_accuracy(0.45) == CalibrationGrade.POOR

    def test_broken(self):
        assert CalibrationGrade.from_accuracy(0.20) == CalibrationGrade.BROKEN


# ━━━ Test: JudgeVersionTracker ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestJudgeVersionTracker:

    def test_register_version(self):
        tracker = JudgeVersionTracker()
        v = tracker.register_version("quality", "1.0.0", "Initial", "prompt text")
        assert v.judge_name == "quality"
        assert v.version == "1.0.0"
        assert v.config_hash != ""

    def test_get_latest(self):
        tracker = JudgeVersionTracker()
        tracker.register_version("quality", "1.0.0")
        tracker.register_version("quality", "2.0.0")
        latest = tracker.get_latest("quality")
        assert latest.version == "2.0.0"

    def test_update_accuracy(self):
        tracker = JudgeVersionTracker()
        tracker.register_version("safety", "1.0.0")
        result = tracker.update_accuracy("safety", "1.0.0", 0.85)
        assert result is True
        assert tracker.get_latest("safety").accuracy == 0.85

    def test_compare_versions(self):
        tracker = JudgeVersionTracker()
        tracker.register_version("quality", "1.0.0")
        tracker.update_accuracy("quality", "1.0.0", 0.70)
        tracker.register_version("quality", "2.0.0")
        tracker.update_accuracy("quality", "2.0.0", 0.85)

        comparison = tracker.compare_versions("quality", "1.0.0", "2.0.0")
        assert comparison is not None
        assert comparison["improved"] is True
        assert comparison["delta"] == 0.15

    def test_save_and_load(self):
        tracker = JudgeVersionTracker()
        tracker.register_version("grounding", "1.0.0", "Init")
        tracker.register_version("safety", "1.0.0", "Init")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tracker.save_to_file(f.name)
            tracker2 = JudgeVersionTracker()
            loaded = tracker2.load_from_file(f.name)
            assert loaded == 2

    def test_get_all_latest(self):
        tracker = JudgeVersionTracker()
        tracker.register_version("grounding", "1.0.0")
        tracker.register_version("safety", "1.0.0")
        tracker.register_version("quality", "1.0.0")
        latest = tracker.get_all_latest()
        assert len(latest) == 3


# ━━━ Test: Integration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIntegration:

    def test_full_pipeline(self):
        """Load → Run → Analyze pipeline."""
        # Load
        mgr = GoldenDatasetManager()
        mgr.load_built_in()

        # Simulate judge results: all judges score 0.9 and pass
        # This means PASS examples will be correct, FAIL examples will be wrong
        runner = CalibrationRunner()
        results = {}
        for ex in mgr.examples:
            results[ex.id] = (0.9, True, "mock pass")

        verdicts = runner.run_batch(mgr.examples, results)
        assert len(verdicts) == 42

        # Analyze
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(verdicts)
        assert report.total_examples == 42
        assert 0.0 <= report.overall_accuracy <= 1.0
        assert report.overall_grade is not None

    def test_perfect_judge_pipeline(self):
        """Simulate a judge that gets everything right."""
        mgr = GoldenDatasetManager()
        mgr.load_built_in()

        runner = CalibrationRunner()
        results = {}
        for ex in mgr.examples:
            # Give the expected score midpoint and correct verdict
            mid = (ex.expected_score_min + ex.expected_score_max) / 2
            passed = ex.expected_verdict != ExpectedVerdict.FAIL
            results[ex.id] = (mid, passed, "perfect")

        verdicts = runner.run_batch(mgr.examples, results)
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(verdicts)

        assert report.overall_accuracy == 1.0
        assert report.overall_grade == CalibrationGrade.EXCELLENT

    def test_pipeline_with_versioning(self):
        """Full pipeline including version tracking."""
        tracker = JudgeVersionTracker()
        tracker.register_version("grounding", "1.0.0", "Sentence-BERT baseline")
        tracker.register_version("safety", "1.0.0", "Presidio + Detoxify")

        mgr = GoldenDatasetManager()
        mgr.load_built_in()

        runner = CalibrationRunner()
        results = {}
        for ex in mgr.examples:
            mid = (ex.expected_score_min + ex.expected_score_max) / 2
            passed = ex.expected_verdict != ExpectedVerdict.FAIL
            results[ex.id] = (mid, passed, "v1")

        verdicts = runner.run_batch(mgr.examples, results)
        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(verdicts)

        # Update version accuracy
        for name, acc in report.judge_accuracy.items():
            tracker.update_accuracy(name, "1.0.0", acc.overall_accuracy)

        latest = tracker.get_all_latest()
        for name, v in latest.items():
            assert v.accuracy > 0, f"{name} accuracy not updated"

    def test_report_serialization_roundtrip(self):
        """Verify report can be serialized and key fields preserved."""
        mgr = GoldenDatasetManager()
        mgr.load_built_in()

        runner = CalibrationRunner()
        results = {ex.id: (0.5, True, "mid") for ex in mgr.examples}
        verdicts = runner.run_batch(mgr.examples, results)

        analyzer = CalibrationAnalyzer()
        report = analyzer.analyze(verdicts)
        data = report.to_dict()

        assert "overall_accuracy" in data
        assert "overall_grade" in data
        assert "judges" in data
        assert "agreements" in data
        assert len(data["judges"]) == 4
