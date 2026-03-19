"""
Calibration Analyzer — computes calibration metrics from verdicts.

Takes raw ExampleVerdict objects from CalibrationRunner and produces:
  - Per-judge accuracy (verdict accuracy, score accuracy, overall)
  - Confusion matrix per judge (TP, TN, FP, FN)
  - Inter-judge agreement metrics
  - Score drift analysis
  - Calibration grade and recommendations
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from src.calibration.models import (
    ExampleVerdict,
    ExpectedVerdict,
    JudgeAccuracy,
    JudgePairAgreement,
    CalibrationReport,
    CalibrationGrade,
)


class CalibrationAnalyzer:
    """Analyzes calibration verdicts to produce accuracy and agreement metrics."""

    def analyze(
        self,
        verdicts: list[ExampleVerdict],
    ) -> CalibrationReport:
        """
        Full calibration analysis from a set of verdicts.

        Args:
            verdicts: List of ExampleVerdict objects from CalibrationRunner.

        Returns:
            CalibrationReport with per-judge and cross-judge metrics.
        """
        report = CalibrationReport()
        report.total_examples = len(verdicts)

        if not verdicts:
            return report

        # Group verdicts by judge
        by_judge: dict[str, list[ExampleVerdict]] = defaultdict(list)
        for v in verdicts:
            by_judge[v.target_judge].append(v)

        # Per-judge accuracy
        for judge_name, judge_verdicts in by_judge.items():
            accuracy = self._compute_judge_accuracy(judge_name, judge_verdicts)
            report.judge_accuracy[judge_name] = accuracy

        # Cross-judge agreement
        report.agreements = self._compute_agreements(verdicts)

        # Overall metrics
        report.total_correct = sum(
            acc.overall_correct for acc in report.judge_accuracy.values()
        )
        report.overall_accuracy = (
            report.total_correct / report.total_examples
            if report.total_examples > 0 else 0.0
        )
        report.overall_grade = CalibrationGrade.from_accuracy(report.overall_accuracy)

        # Best / worst judge
        if report.judge_accuracy:
            sorted_judges = sorted(
                report.judge_accuracy.items(),
                key=lambda x: x[1].overall_accuracy,
            )
            report.weakest_judge = sorted_judges[0][0]
            report.strongest_judge = sorted_judges[-1][0]

        # Recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    def _compute_judge_accuracy(
        self,
        judge_name: str,
        verdicts: list[ExampleVerdict],
    ) -> JudgeAccuracy:
        """Compute accuracy metrics for a single judge."""
        acc = JudgeAccuracy(judge_name=judge_name)
        acc.total_examples = len(verdicts)
        acc.verdicts = verdicts

        if not verdicts:
            return acc

        # Count correctness
        acc.correct_verdicts = sum(1 for v in verdicts if v.verdict_correct)
        acc.correct_scores = sum(1 for v in verdicts if v.score_in_range)
        acc.overall_correct = sum(1 for v in verdicts if v.overall_correct)

        # Accuracy percentages
        n = len(verdicts)
        acc.verdict_accuracy = acc.correct_verdicts / n
        acc.score_accuracy = acc.correct_scores / n
        acc.overall_accuracy = acc.overall_correct / n

        # Confusion matrix
        for v in verdicts:
            expected_pass = v.expected_verdict == ExpectedVerdict.PASS
            actual_pass = v.actual_passed

            if expected_pass and actual_pass:
                acc.true_positives += 1
            elif not expected_pass and not actual_pass:
                acc.true_negatives += 1
            elif not expected_pass and actual_pass:
                acc.false_positives += 1
            elif expected_pass and not actual_pass:
                acc.false_negatives += 1

        # Score drift: average deviation from expected score midpoint
        deviations = []
        for v in verdicts:
            midpoint = (v.expected_score_min + v.expected_score_max) / 2
            deviations.append(v.actual_score - midpoint)
        acc.avg_score_deviation = sum(deviations) / len(deviations) if deviations else 0.0

        acc.grade = CalibrationGrade.from_accuracy(acc.overall_accuracy)

        return acc

    def _compute_agreements(
        self,
        verdicts: list[ExampleVerdict],
    ) -> list[JudgePairAgreement]:
        """
        Compute inter-judge agreement.

        Since each golden example targets ONE judge, we can't directly
        compare judges on the same example. Instead, we look for examples
        where BOTH judges WOULD agree on pass/fail (based on the expected
        verdict patterns).

        We compute agreement based on accuracy alignment: if both judges
        are accurate on their respective examples, they "agree" on the
        correctness framework.
        """
        # Group by judge
        by_judge: dict[str, list[ExampleVerdict]] = defaultdict(list)
        for v in verdicts:
            by_judge[v.target_judge].append(v)

        judge_names = sorted(by_judge.keys())
        agreements = []

        for judge_a, judge_b in combinations(judge_names, 2):
            verdicts_a = by_judge[judge_a]
            verdicts_b = by_judge[judge_b]

            # Agreement metric: do both judges have similar accuracy patterns?
            # We use the rate at which each judge's verdicts are correct
            correct_a = sum(1 for v in verdicts_a if v.overall_correct)
            correct_b = sum(1 for v in verdicts_b if v.overall_correct)
            total = len(verdicts_a) + len(verdicts_b)
            shared = min(len(verdicts_a), len(verdicts_b))

            if total == 0:
                continue

            # Alignment score: how similar are their accuracy rates?
            rate_a = correct_a / len(verdicts_a) if verdicts_a else 0
            rate_b = correct_b / len(verdicts_b) if verdicts_b else 0
            alignment = 1.0 - abs(rate_a - rate_b)

            pair = JudgePairAgreement(
                judge_a=judge_a,
                judge_b=judge_b,
                shared_examples=shared,
                agreements=int(alignment * shared),
                disagreements=shared - int(alignment * shared),
                agreement_rate=alignment,
            )

            # Track directional disagreements
            fp_a = sum(1 for v in verdicts_a if not v.verdict_correct and v.actual_passed)
            fp_b = sum(1 for v in verdicts_b if not v.verdict_correct and v.actual_passed)
            pair.a_pass_b_fail = fp_a  # Judge A tends to over-pass
            pair.a_fail_b_pass = fp_b  # Judge B tends to over-pass

            agreements.append(pair)

        return agreements

    def _generate_recommendations(self, report: CalibrationReport) -> list[str]:
        """Generate actionable calibration recommendations."""
        recs = []

        if report.overall_grade in (CalibrationGrade.BROKEN, CalibrationGrade.POOR):
            recs.append(
                f"Overall calibration is {report.overall_grade.value}. "
                f"Judge accuracy is {report.overall_accuracy:.0%}. "
                f"Consider reviewing judge thresholds and configurations."
            )

        for name, acc in report.judge_accuracy.items():
            if acc.false_positives > acc.total_examples * 0.3:
                recs.append(
                    f"Judge '{name}' has high false positive rate "
                    f"({acc.false_positives}/{acc.total_examples}). "
                    f"It's too lenient — tighten pass thresholds."
                )

            if acc.false_negatives > acc.total_examples * 0.3:
                recs.append(
                    f"Judge '{name}' has high false negative rate "
                    f"({acc.false_negatives}/{acc.total_examples}). "
                    f"It's too strict — loosen pass thresholds."
                )

            if abs(acc.avg_score_deviation) > 0.2:
                direction = "high" if acc.avg_score_deviation > 0 else "low"
                recs.append(
                    f"Judge '{name}' scores consistently {direction} "
                    f"(avg deviation: {acc.avg_score_deviation:+.2f}). "
                    f"Consider recalibrating score thresholds."
                )

            if acc.grade == CalibrationGrade.BROKEN:
                recs.append(
                    f"Judge '{name}' is broken (accuracy: {acc.overall_accuracy:.0%}). "
                    f"Check if it's properly initialized and configured."
                )

        if not recs:
            recs.append(
                f"All judges are well-calibrated (overall: {report.overall_accuracy:.0%}). "
                f"No action needed."
            )

        return recs
