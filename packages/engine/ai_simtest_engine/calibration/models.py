"""
Calibration data models — golden examples, results, accuracy metrics.

Design principles:
  - Golden examples are judge-specific: each example targets ONE judge
  - Expected verdicts are explicit: score range + pass/fail + severity
  - Results are per-judge AND cross-judge (agreement metrics)
  - Everything is serializable for JSON export and versioning
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


# ━━━ Enums ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ExpectedVerdict(str, enum.Enum):
    """What the judge SHOULD say about this example."""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


class ExampleCategory(str, enum.Enum):
    """Category of the golden example for filtering."""
    GROUNDING_SUPPORTED = "grounding_supported"
    GROUNDING_HALLUCINATED = "grounding_hallucinated"
    SAFETY_CLEAN = "safety_clean"
    SAFETY_PII = "safety_pii"
    SAFETY_TOXIC = "safety_toxic"
    SAFETY_PROMPT_LEAK = "safety_prompt_leak"
    QUALITY_HIGH = "quality_high"
    QUALITY_LOW = "quality_low"
    QUALITY_MODERATE = "quality_moderate"
    RELEVANCE_ON_TOPIC = "relevance_on_topic"
    RELEVANCE_OFF_TOPIC = "relevance_off_topic"
    RELEVANCE_PARTIAL = "relevance_partial"


class CalibrationGrade(str, enum.Enum):
    """Overall calibration health grade."""
    EXCELLENT = "excellent"   # >= 90% accuracy
    GOOD = "good"             # >= 75%
    NEEDS_TUNING = "needs_tuning"  # >= 60%
    POOR = "poor"             # >= 40%
    BROKEN = "broken"         # < 40%

    @classmethod
    def from_accuracy(cls, accuracy: float) -> CalibrationGrade:
        if accuracy >= 0.90:
            return cls.EXCELLENT
        elif accuracy >= 0.75:
            return cls.GOOD
        elif accuracy >= 0.60:
            return cls.NEEDS_TUNING
        elif accuracy >= 0.40:
            return cls.POOR
        else:
            return cls.BROKEN


# ━━━ Golden Example ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class GoldenExample:
    """
    A single hand-labeled example for judge calibration.

    Each example contains the INPUT a judge would see (user message,
    bot response, documentation context) plus the EXPECTED output
    (should it pass or fail? what score range?).
    """

    # Identity
    id: str                                    # Unique ID, e.g. "grounding_001"
    target_judge: str                          # "grounding", "safety", "quality", "relevance"
    category: ExampleCategory                  # For filtering and reporting
    description: str                           # Human-readable what this tests

    # Judge input (what the judge will evaluate)
    user_message: str                          # The user's message / question
    bot_response: str                          # The bot's response to evaluate
    documentation: str = ""                    # Context docs (for grounding judge)

    # Expected output
    expected_verdict: ExpectedVerdict = ExpectedVerdict.PASS
    expected_score_min: float = 0.0            # Score should be >= this
    expected_score_max: float = 1.0            # Score should be <= this
    expected_issues: list[str] = field(default_factory=list)  # Issues that should be flagged

    # Metadata
    tags: list[str] = field(default_factory=list)
    difficulty: str = "standard"               # "easy", "standard", "tricky"
    added_date: str = ""                       # ISO date when this example was added

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_judge": self.target_judge,
            "category": self.category.value,
            "description": self.description,
            "user_message": self.user_message,
            "bot_response": self.bot_response,
            "documentation": self.documentation,
            "expected_verdict": self.expected_verdict.value,
            "expected_score_min": self.expected_score_min,
            "expected_score_max": self.expected_score_max,
            "expected_issues": self.expected_issues,
            "tags": self.tags,
            "difficulty": self.difficulty,
            "added_date": self.added_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GoldenExample:
        return cls(
            id=data["id"],
            target_judge=data["target_judge"],
            category=ExampleCategory(data["category"]),
            description=data["description"],
            user_message=data["user_message"],
            bot_response=data["bot_response"],
            documentation=data.get("documentation", ""),
            expected_verdict=ExpectedVerdict(data.get("expected_verdict", "pass")),
            expected_score_min=data.get("expected_score_min", 0.0),
            expected_score_max=data.get("expected_score_max", 1.0),
            expected_issues=data.get("expected_issues", []),
            tags=data.get("tags", []),
            difficulty=data.get("difficulty", "standard"),
            added_date=data.get("added_date", ""),
        )


# ━━━ Example Verdict (actual judge output vs expected) ━━━━━━━━━━━━━

@dataclass
class ExampleVerdict:
    """Result of running one golden example through its target judge."""

    example_id: str
    target_judge: str

    # Expected
    expected_verdict: ExpectedVerdict
    expected_score_min: float
    expected_score_max: float

    # Actual from judge
    actual_score: float = 0.0
    actual_passed: bool = False
    actual_message: str = ""

    # Verdict on the verdict
    score_in_range: bool = False        # Was actual score within expected range?
    verdict_correct: bool = False       # Did pass/fail match expected?
    overall_correct: bool = False       # Both score_in_range AND verdict_correct?

    def to_dict(self) -> dict:
        return {
            "example_id": self.example_id,
            "target_judge": self.target_judge,
            "expected_verdict": self.expected_verdict.value,
            "expected_score_range": [self.expected_score_min, self.expected_score_max],
            "actual_score": round(self.actual_score, 4),
            "actual_passed": self.actual_passed,
            "actual_message": self.actual_message,
            "score_in_range": self.score_in_range,
            "verdict_correct": self.verdict_correct,
            "overall_correct": self.overall_correct,
        }


# ━━━ Per-Judge Accuracy ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class JudgeAccuracy:
    """Accuracy metrics for a single judge."""

    judge_name: str
    total_examples: int = 0
    correct_verdicts: int = 0
    correct_scores: int = 0
    overall_correct: int = 0

    # Accuracy percentages
    verdict_accuracy: float = 0.0      # % verdicts correct
    score_accuracy: float = 0.0        # % scores in expected range
    overall_accuracy: float = 0.0      # % both correct

    # Confusion counts
    false_positives: int = 0           # Judge said PASS but expected FAIL
    false_negatives: int = 0           # Judge said FAIL but expected PASS
    true_positives: int = 0            # Judge correctly PASS
    true_negatives: int = 0            # Judge correctly FAIL

    # Score drift (average deviation from expected midpoint)
    avg_score_deviation: float = 0.0

    # Per-example details
    verdicts: list[ExampleVerdict] = field(default_factory=list)

    # Calibration health
    grade: CalibrationGrade = CalibrationGrade.BROKEN

    def to_dict(self) -> dict:
        return {
            "judge_name": self.judge_name,
            "total_examples": self.total_examples,
            "verdict_accuracy": round(self.verdict_accuracy, 4),
            "score_accuracy": round(self.score_accuracy, 4),
            "overall_accuracy": round(self.overall_accuracy, 4),
            "grade": self.grade.value,
            "confusion_matrix": {
                "true_positives": self.true_positives,
                "true_negatives": self.true_negatives,
                "false_positives": self.false_positives,
                "false_negatives": self.false_negatives,
            },
            "avg_score_deviation": round(self.avg_score_deviation, 4),
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


# ━━━ Judge Agreement ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class JudgePairAgreement:
    """Agreement metrics between two judges on shared examples."""

    judge_a: str
    judge_b: str
    shared_examples: int = 0
    agreements: int = 0
    disagreements: int = 0
    agreement_rate: float = 0.0

    # Specific disagreement patterns
    a_pass_b_fail: int = 0             # Judge A says pass, Judge B says fail
    a_fail_b_pass: int = 0             # Judge A says fail, Judge B says pass

    def to_dict(self) -> dict:
        return {
            "judges": [self.judge_a, self.judge_b],
            "shared_examples": self.shared_examples,
            "agreement_rate": round(self.agreement_rate, 4),
            "disagreement_patterns": {
                f"{self.judge_a}_pass_{self.judge_b}_fail": self.a_pass_b_fail,
                f"{self.judge_a}_fail_{self.judge_b}_pass": self.a_fail_b_pass,
            },
        }


# ━━━ Judge Version ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class JudgeVersion:
    """Tracks a specific version of a judge's configuration/prompt."""

    judge_name: str
    version: str                       # Semantic version, e.g. "1.0.0"
    description: str = ""              # What changed in this version
    config_hash: str = ""              # Hash of the judge config/prompt
    timestamp: str = ""                # ISO datetime
    accuracy: float = 0.0             # Accuracy from last calibration

    def to_dict(self) -> dict:
        return {
            "judge_name": self.judge_name,
            "version": self.version,
            "description": self.description,
            "config_hash": self.config_hash,
            "timestamp": self.timestamp,
            "accuracy": round(self.accuracy, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> JudgeVersion:
        return cls(
            judge_name=data["judge_name"],
            version=data["version"],
            description=data.get("description", ""),
            config_hash=data.get("config_hash", ""),
            timestamp=data.get("timestamp", ""),
            accuracy=data.get("accuracy", 0.0),
        )


# ━━━ Calibration Report ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class CalibrationReport:
    """Complete calibration results across all judges."""

    # Per-judge accuracy
    judge_accuracy: dict[str, JudgeAccuracy] = field(default_factory=dict)

    # Cross-judge agreement
    agreements: list[JudgePairAgreement] = field(default_factory=list)

    # Overall
    overall_accuracy: float = 0.0
    overall_grade: CalibrationGrade = CalibrationGrade.BROKEN
    total_examples: int = 0
    total_correct: int = 0

    # Worst performers
    weakest_judge: str = ""
    strongest_judge: str = ""

    # Recommendations
    recommendations: list[str] = field(default_factory=list)

    # Versioning
    judge_versions: dict[str, JudgeVersion] = field(default_factory=dict)

    # Metadata
    timestamp: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "overall_accuracy": round(self.overall_accuracy, 4),
            "overall_grade": self.overall_grade.value,
            "total_examples": self.total_examples,
            "total_correct": self.total_correct,
            "weakest_judge": self.weakest_judge,
            "strongest_judge": self.strongest_judge,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp,
            "duration_seconds": round(self.duration_seconds, 2),
            "judges": {
                name: acc.to_dict()
                for name, acc in self.judge_accuracy.items()
            },
            "agreements": [a.to_dict() for a in self.agreements],
            "judge_versions": {
                name: v.to_dict()
                for name, v in self.judge_versions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> CalibrationReport:
        report = cls()
        report.overall_accuracy = data.get("overall_accuracy", 0.0)
        report.overall_grade = CalibrationGrade(data.get("overall_grade", "broken"))
        report.total_examples = data.get("total_examples", 0)
        report.total_correct = data.get("total_correct", 0)
        report.weakest_judge = data.get("weakest_judge", "")
        report.strongest_judge = data.get("strongest_judge", "")
        report.recommendations = data.get("recommendations", [])
        report.timestamp = data.get("timestamp", "")
        report.duration_seconds = data.get("duration_seconds", 0.0)
        return report
