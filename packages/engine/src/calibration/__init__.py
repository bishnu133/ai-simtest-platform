"""
Judge Calibration Suite — P2 #12

Validates judge accuracy against golden labeled examples.

Place under: src/calibration/

Usage:
    from src.calibration import GoldenDatasetManager, CalibrationRunner, CalibrationAnalyzer

    # Load golden examples
    dataset = GoldenDatasetManager()
    dataset.load_built_in()  # 42 built-in examples

    # Run examples through judges (mock mode for testing)
    runner = CalibrationRunner()
    verdicts = runner.run_batch(dataset.examples, judge_results)

    # Analyze results
    analyzer = CalibrationAnalyzer()
    report = analyzer.analyze(verdicts)
    print(report.overall_accuracy)  # 0.0-1.0
    print(report.overall_grade)     # excellent/good/needs_tuning/poor/broken
"""

from src.calibration.models import (
    GoldenExample,
    ExampleVerdict,
    ExpectedVerdict,
    ExampleCategory,
    CalibrationGrade,
    CalibrationReport,
    JudgeAccuracy,
    JudgePairAgreement,
    JudgeVersion,
)
from src.calibration.golden_set import GoldenDatasetManager
from src.calibration.runner import CalibrationRunner
from src.calibration.analyzer import CalibrationAnalyzer
from src.calibration.versioning import JudgeVersionTracker
from src.calibration.built_in_examples import get_built_in_examples

__all__ = [
    # Models
    "GoldenExample",
    "ExampleVerdict",
    "ExpectedVerdict",
    "ExampleCategory",
    "CalibrationGrade",
    "CalibrationReport",
    "JudgeAccuracy",
    "JudgePairAgreement",
    "JudgeVersion",
    # Managers
    "GoldenDatasetManager",
    "CalibrationRunner",
    "CalibrationAnalyzer",
    "JudgeVersionTracker",
    # Helpers
    "get_built_in_examples",
]
