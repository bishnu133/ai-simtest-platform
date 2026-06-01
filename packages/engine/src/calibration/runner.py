"""
Calibration Runner — executes golden examples against judges.

Two modes:
  1. Real mode: Uses actual judge instances (async, requires model loading)
  2. Mock mode: Takes pre-computed judge results (for testing and offline analysis)

The runner itself only COLLECTS results. Analysis is done by CalibrationAnalyzer.
"""

from __future__ import annotations

from src.calibration.models import (
    GoldenExample,
    ExampleVerdict,
    ExpectedVerdict,
)


class CalibrationRunner:
    """
    Runs golden examples through judges and collects raw verdicts.

    Does NOT do analysis — just collects ExampleVerdict objects.
    Analysis is handled by CalibrationAnalyzer.
    """

    def evaluate_example_with_result(
        self,
        example: GoldenExample,
        actual_score: float,
        actual_passed: bool,
        actual_message: str = "",
    ) -> ExampleVerdict:
        """
        Evaluate a single golden example given a judge's actual output.

        This is the core comparison: did the judge's output match
        the expected verdict and score range?

        Args:
            example: The golden example with expected values.
            actual_score: The score the judge actually gave (0.0-1.0).
            actual_passed: Whether the judge said "pass".
            actual_message: The judge's message/explanation.

        Returns:
            ExampleVerdict with correctness assessment.
        """
        # Check if actual score is within expected range
        score_in_range = (
            example.expected_score_min <= actual_score <= example.expected_score_max
        )

        # Check if pass/fail verdict matches expected
        if example.expected_verdict == ExpectedVerdict.PASS:
            verdict_correct = actual_passed is True
        elif example.expected_verdict == ExpectedVerdict.FAIL:
            verdict_correct = actual_passed is False
        else:
            # WARNING: either pass or fail is acceptable depending on threshold
            verdict_correct = True  # We don't strictly enforce warning

        overall_correct = score_in_range and verdict_correct

        return ExampleVerdict(
            example_id=example.id,
            target_judge=example.target_judge,
            expected_verdict=example.expected_verdict,
            expected_score_min=example.expected_score_min,
            expected_score_max=example.expected_score_max,
            actual_score=actual_score,
            actual_passed=actual_passed,
            actual_message=actual_message,
            score_in_range=score_in_range,
            verdict_correct=verdict_correct,
            overall_correct=overall_correct,
        )

    def run_batch(
        self,
        examples: list[GoldenExample],
        judge_results: dict[str, tuple[float, bool, str]],
    ) -> list[ExampleVerdict]:
        """
        Run a batch of examples given pre-computed judge results.

        Args:
            examples: List of golden examples.
            judge_results: Dict mapping example_id → (score, passed, message).

        Returns:
            List of ExampleVerdict objects.
        """
        verdicts = []
        for example in examples:
            result = judge_results.get(example.id)
            if result is None:
                continue
            score, passed, message = result
            verdict = self.evaluate_example_with_result(
                example=example,
                actual_score=score,
                actual_passed=passed,
                actual_message=message,
            )
            verdicts.append(verdict)
        return verdicts
