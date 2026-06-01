"""
Comparison Engine — Core regression detection logic.

Loads two simulation summary JSON files (baseline vs current),
computes deltas across all metrics, detects regressions, and
produces a ComparisonResult with verdict.

Usage:
    engine = ComparisonEngine(config)
    result = engine.compare(baseline_path, current_path)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .comparison_models import (
    ComparisonConfig,
    ComparisonResult,
    ComparisonSummary,
    DeltaDirection,
    FailurePatternDelta,
    JudgeComparison,
    MetricDelta,
    RegressionVerdict,
)


class ComparisonEngine:
    """Compares two simulation summary reports and detects regressions."""

    def __init__(self, config: ComparisonConfig | None = None):
        self.config = config or ComparisonConfig()

    def compare(self, baseline_path: str | Path, current_path: str | Path) -> ComparisonResult:
        """
        Compare two simulation summary JSON files.

        Args:
            baseline_path: Path to the baseline summary.json
            current_path: Path to the current summary.json

        Returns:
            ComparisonResult with all deltas and verdict
        """
        baseline = self._load_report(baseline_path)
        current = self._load_report(current_path)

        summary = self._build_summary(baseline, current)
        exit_code = 1 if (self.config.fail_if_regression and summary.verdict == RegressionVerdict.FAIL) else 0

        return ComparisonResult(
            summary=summary,
            config=self.config,
            baseline_report=baseline,
            current_report=current,
            exit_code=exit_code,
        )

    def compare_dicts(self, baseline: dict, current: dict) -> ComparisonResult:
        """Compare two summary dicts directly (for testing / API use)."""
        summary = self._build_summary(baseline, current)
        exit_code = 1 if (self.config.fail_if_regression and summary.verdict == RegressionVerdict.FAIL) else 0

        return ComparisonResult(
            summary=summary,
            config=self.config,
            baseline_report=baseline,
            current_report=current,
            exit_code=exit_code,
        )

    # ─── Internal ───────────────────────────────────────────────

    def _load_report(self, path: str | Path) -> dict[str, Any]:
        """Load and validate a summary JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Report not found: {path}")

        with open(path) as f:
            data = json.load(f)

        # Support both flat summary and wrapped {summary: {...}} formats
        if "summary" in data and isinstance(data["summary"], dict):
            # Full report format — extract summary
            summary = data["summary"]
            # Preserve score_by_judge if present at top level
            if "score_by_judge" in data:
                summary["score_by_judge"] = data["score_by_judge"]
            if "failure_patterns" in data:
                summary["failure_patterns"] = data["failure_patterns"]
            return summary

        return data

    def _build_summary(self, baseline: dict, current: dict) -> ComparisonSummary:
        """Build the full comparison summary from two report dicts."""

        # Extract names
        baseline_name = baseline.get("simulation_name") or baseline.get("name") or "Baseline"
        current_name = current.get("simulation_name") or current.get("name") or "Current"

        # Core metrics
        pass_rate = MetricDelta.compute(
            "pass_rate",
            self._get_float(baseline, "pass_rate"),
            self._get_float(current, "pass_rate"),
            higher_is_better=True,
        )

        avg_score = MetricDelta.compute(
            "average_score",
            self._get_float(baseline, "average_score"),
            self._get_float(current, "average_score"),
            higher_is_better=True,
        )

        critical_failures = MetricDelta.compute(
            "critical_failures",
            self._get_float(baseline, "critical_failures"),
            self._get_float(current, "critical_failures"),
            higher_is_better=False,  # Lower is better
        )

        total_conversations = MetricDelta.compute(
            "total_conversations",
            self._get_float(baseline, "total_conversations", "total_personas"),
            self._get_float(current, "total_conversations", "total_personas"),
            higher_is_better=True,
        )

        total_turns = MetricDelta.compute(
            "total_turns",
            self._get_float(baseline, "total_turns"),
            self._get_float(current, "total_turns"),
            higher_is_better=True,
        )

        # Judge-level comparisons
        judge_comparisons = self._compare_judges(baseline, current)

        # Failure pattern analysis
        new_failures, resolved_failures, persistent_failures, worsened_failures, improved_failures = \
            self._compare_failure_patterns(baseline, current)

        # Compute verdict
        verdict, reasons, regressions, improvements = self._compute_verdict(
            pass_rate, avg_score, critical_failures,
            judge_comparisons, new_failures, worsened_failures,
            self._get_float(current, "pass_rate"),
        )

        return ComparisonSummary(
            baseline_name=baseline_name,
            current_name=current_name,
            baseline_date=baseline.get("timestamp") or baseline.get("date"),
            current_date=current.get("timestamp") or current.get("date"),
            pass_rate=pass_rate,
            average_score=avg_score,
            critical_failures=critical_failures,
            total_conversations=total_conversations,
            total_turns=total_turns,
            judge_comparisons=judge_comparisons,
            new_failures=new_failures,
            resolved_failures=resolved_failures,
            persistent_failures=persistent_failures,
            worsened_failures=worsened_failures,
            improved_failures=improved_failures,
            verdict=verdict,
            verdict_reasons=reasons,
            total_regressions=regressions,
            total_improvements=improvements,
        )

    def _compare_judges(self, baseline: dict, current: dict) -> list[JudgeComparison]:
        """Compare per-judge scores between runs."""
        baseline_judges = baseline.get("score_by_judge", {})
        current_judges = current.get("score_by_judge", {})

        all_judges = set(list(baseline_judges.keys()) + list(current_judges.keys()))
        comparisons = []

        for judge in sorted(all_judges):
            b_score = float(baseline_judges.get(judge, 0))
            c_score = float(current_judges.get(judge, 0))
            delta = c_score - b_score
            delta_pct = (delta / b_score * 100) if b_score != 0 else 0

            if abs(delta) < 0.001:
                direction = DeltaDirection.UNCHANGED
            elif delta > 0:
                direction = DeltaDirection.IMPROVED
            else:
                direction = DeltaDirection.REGRESSED

            comparisons.append(JudgeComparison(
                judge_name=judge,
                baseline_score=round(b_score, 4),
                current_score=round(c_score, 4),
                delta=round(delta, 4),
                delta_percent=round(delta_pct, 2),
                direction=direction,
                is_regression=(direction == DeltaDirection.REGRESSED
                               and abs(delta_pct) >= self.config.regression_threshold * 100),
            ))

        return comparisons

    def _compare_failure_patterns(self, baseline: dict, current: dict) -> tuple:
        """Compare failure patterns between runs.

        Returns: (new, resolved, persistent, worsened, improved)
        """
        baseline_patterns = {
            p.get("pattern_name", p.get("name", "unknown")): p
            for p in baseline.get("failure_patterns", [])
        }
        current_patterns = {
            p.get("pattern_name", p.get("name", "unknown")): p
            for p in current.get("failure_patterns", [])
        }

        baseline_names = set(baseline_patterns.keys())
        current_names = set(current_patterns.keys())

        new_failures = []
        resolved_failures = []
        persistent_failures = []
        worsened_failures = []
        improved_failures = []

        # New failures (in current but not baseline)
        for name in current_names - baseline_names:
            p = current_patterns[name]
            new_failures.append(FailurePatternDelta(
                pattern_name=name,
                status="new",
                current_frequency=p.get("frequency", 1),
                severity=p.get("severity", "medium"),
                delta=p.get("frequency", 1),
            ))

        # Resolved failures (in baseline but not current)
        for name in baseline_names - current_names:
            p = baseline_patterns[name]
            resolved_failures.append(FailurePatternDelta(
                pattern_name=name,
                status="resolved",
                baseline_frequency=p.get("frequency", 1),
                severity=p.get("severity", "medium"),
                delta=-p.get("frequency", 1),
            ))

        # Persistent (in both)
        for name in baseline_names & current_names:
            bp = baseline_patterns[name]
            cp = current_patterns[name]
            b_freq = bp.get("frequency", 1)
            c_freq = cp.get("frequency", 1)
            delta = c_freq - b_freq

            if delta > 0:
                worsened_failures.append(FailurePatternDelta(
                    pattern_name=name,
                    status="worsened",
                    baseline_frequency=b_freq,
                    current_frequency=c_freq,
                    severity=cp.get("severity", "medium"),
                    delta=delta,
                ))
            elif delta < 0:
                improved_failures.append(FailurePatternDelta(
                    pattern_name=name,
                    status="improved",
                    baseline_frequency=b_freq,
                    current_frequency=c_freq,
                    severity=cp.get("severity", "medium"),
                    delta=delta,
                ))
            else:
                persistent_failures.append(FailurePatternDelta(
                    pattern_name=name,
                    status="persistent",
                    baseline_frequency=b_freq,
                    current_frequency=c_freq,
                    severity=cp.get("severity", "medium"),
                    delta=0,
                ))

        return new_failures, resolved_failures, persistent_failures, worsened_failures, improved_failures

    def _compute_verdict(
        self,
        pass_rate: MetricDelta,
        avg_score: MetricDelta,
        critical_failures: MetricDelta,
        judge_comparisons: list[JudgeComparison],
        new_failures: list[FailurePatternDelta],
        worsened_failures: list[FailurePatternDelta],
        current_pass_rate: float,
    ) -> tuple[RegressionVerdict, list[str], int, int]:
        """Compute overall verdict from all deltas."""

        reasons = []
        regressions = 0
        improvements = 0
        threshold = self.config.regression_threshold

        # Pass rate check
        if pass_rate.is_regression and abs(pass_rate.delta_percent) >= threshold * 100:
            reasons.append(f"Pass rate dropped {abs(pass_rate.delta_percent):.1f}% "
                           f"({pass_rate.baseline_value:.1%} → {pass_rate.current_value:.1%})")
            regressions += 1
        elif pass_rate.direction == DeltaDirection.IMPROVED:
            improvements += 1

        # Pass rate floor
        if self.config.pass_rate_floor > 0 and current_pass_rate < self.config.pass_rate_floor:
            reasons.append(f"Pass rate {current_pass_rate:.1%} is below floor {self.config.pass_rate_floor:.1%}")
            regressions += 1

        # Critical failures
        if critical_failures.is_regression:
            new_critical = int(critical_failures.delta)
            if new_critical > self.config.critical_failure_tolerance:
                reasons.append(f"{new_critical} new critical failure(s) detected")
                regressions += 1
        elif critical_failures.direction == DeltaDirection.IMPROVED:
            improvements += 1

        # Average score
        if avg_score.is_regression and abs(avg_score.delta_percent) >= threshold * 100:
            reasons.append(f"Average score dropped {abs(avg_score.delta_percent):.1f}%")
            regressions += 1
        elif avg_score.direction == DeltaDirection.IMPROVED:
            improvements += 1

        # Judge regressions
        regressed_judges = [j for j in judge_comparisons if j.is_regression]
        for j in regressed_judges:
            reasons.append(f"{j.judge_name} judge regressed {abs(j.delta_percent):.1f}%")
            regressions += 1

        improved_judges = [j for j in judge_comparisons if j.direction == DeltaDirection.IMPROVED]
        improvements += len(improved_judges)

        # New failure patterns
        critical_new = [f for f in new_failures if f.severity in ("critical", "high")]
        if critical_new:
            reasons.append(f"{len(critical_new)} new critical/high failure pattern(s)")
            regressions += len(critical_new)

        # Worsened patterns
        if worsened_failures:
            reasons.append(f"{len(worsened_failures)} failure pattern(s) worsened")
            regressions += len(worsened_failures)

        # Determine verdict
        if regressions == 0 and improvements > 0:
            verdict = RegressionVerdict.IMPROVED
        elif regressions == 0:
            verdict = RegressionVerdict.PASS
        elif any("critical" in r.lower() for r in reasons) or \
             any("pass rate" in r.lower() and "dropped" in r.lower() for r in reasons):
            verdict = RegressionVerdict.FAIL
        else:
            verdict = RegressionVerdict.WARNING

        return verdict, reasons, regressions, improvements

    # ─── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _get_float(data: dict, *keys: str) -> float:
        """Get a float value trying multiple keys."""
        for key in keys:
            if key in data:
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    continue
        return 0.0
