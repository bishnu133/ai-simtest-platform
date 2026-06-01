"""
Compare Mode Data Models

Models for regression detection and comparison between simulation runs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RegressionVerdict(str, Enum):
    """Overall regression verdict."""
    PASS = "pass"           # No regressions detected
    WARNING = "warning"     # Minor regressions detected
    FAIL = "fail"           # Critical regressions detected
    IMPROVED = "improved"   # Overall improvement


class DeltaDirection(str, Enum):
    """Direction of a metric change."""
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"


class MetricDelta(BaseModel):
    """A single metric comparison between baseline and current."""
    name: str
    baseline_value: float
    current_value: float
    delta: float = Field(description="current - baseline")
    delta_percent: float = Field(description="Percentage change")
    direction: DeltaDirection
    is_regression: bool = False

    @classmethod
    def compute(cls, name: str, baseline: float, current: float, higher_is_better: bool = True) -> "MetricDelta":
        delta = current - baseline
        delta_pct = (delta / baseline * 100) if baseline != 0 else (100.0 if delta > 0 else 0.0)

        if abs(delta) < 0.001:
            direction = DeltaDirection.UNCHANGED
            is_regression = False
        elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
            direction = DeltaDirection.IMPROVED
            is_regression = False
        else:
            direction = DeltaDirection.REGRESSED
            is_regression = True

        return cls(
            name=name,
            baseline_value=round(baseline, 4),
            current_value=round(current, 4),
            delta=round(delta, 4),
            delta_percent=round(delta_pct, 2),
            direction=direction,
            is_regression=is_regression,
        )


class JudgeComparison(BaseModel):
    """Per-judge score comparison."""
    judge_name: str
    baseline_score: float
    current_score: float
    delta: float
    delta_percent: float
    direction: DeltaDirection
    is_regression: bool = False


class FailurePatternDelta(BaseModel):
    """Change in a failure pattern between runs."""
    pattern_name: str
    status: str = Field(description="new | resolved | persistent | worsened | improved")
    baseline_frequency: int = 0
    current_frequency: int = 0
    severity: str = "medium"
    delta: int = 0


class ComparisonSummary(BaseModel):
    """High-level comparison summary."""
    baseline_name: str
    current_name: str
    baseline_date: Optional[str] = None
    current_date: Optional[str] = None
    comparison_date: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Core metrics
    pass_rate: MetricDelta
    average_score: MetricDelta
    critical_failures: MetricDelta
    total_conversations: MetricDelta
    total_turns: MetricDelta

    # Judge-level
    judge_comparisons: list[JudgeComparison] = []

    # Failure patterns
    new_failures: list[FailurePatternDelta] = []
    resolved_failures: list[FailurePatternDelta] = []
    persistent_failures: list[FailurePatternDelta] = []
    worsened_failures: list[FailurePatternDelta] = []
    improved_failures: list[FailurePatternDelta] = []

    # Verdict
    verdict: RegressionVerdict = RegressionVerdict.PASS
    verdict_reasons: list[str] = []
    total_regressions: int = 0
    total_improvements: int = 0


class ComparisonConfig(BaseModel):
    """Configuration for comparison analysis."""
    regression_threshold: float = Field(default=0.05, description="Minimum delta % to flag as regression")
    critical_failure_tolerance: int = Field(default=0, description="Max new critical failures before FAIL verdict")
    pass_rate_floor: float = Field(default=0.0, description="If current pass rate below this, always FAIL")
    fail_if_regression: bool = Field(default=False, description="Return exit code 1 on regression")


class ComparisonResult(BaseModel):
    """Complete comparison result between two simulation runs."""
    summary: ComparisonSummary
    config: ComparisonConfig
    baseline_report: dict[str, Any] = Field(description="Raw baseline summary data")
    current_report: dict[str, Any] = Field(description="Raw current summary data")
    exit_code: int = Field(default=0, description="0 = pass, 1 = regression detected")
