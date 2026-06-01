"""
Coverage data models — configuration, dimension reports, and composite report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_simtest_engine.coverage.constants import CoverageGrade, ALL_SCENARIO_CATEGORIES


# ━━━ Config ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class CoverageConfig:
    """Configuration for what SHOULD have been covered in this test run."""

    # Topics defined via --topics (empty = skip topic coverage scoring)
    defined_topics: list[str] = field(default_factory=list)

    # Scenario IDs requested via --scenarios (empty = skip)
    scenario_ids: list[str] = field(default_factory=list)

    # Whether --stress-memory was enabled
    stress_enabled: bool = False

    # Expected persona distribution (default 70/20/10)
    expected_persona_distribution: dict[str, float] = field(
        default_factory=lambda: {
            "standard": 0.70,
            "edge_case": 0.20,
            "adversarial": 0.10,
        }
    )

    # Tolerance for persona distribution deviation (±15%)
    distribution_tolerance: float = 0.15

    # Minimum conversations per persona type to count as "covered"
    min_conversations_per_type: int = 1

    # Expected judges (default all 4)
    expected_judges: list[str] = field(
        default_factory=lambda: ["grounding", "safety", "quality", "relevance"]
    )


# ━━━ Dimension Reports ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class PersonaTypeCoverage:
    """Analysis of persona-type distribution vs. expected 70/20/10."""

    type_counts: dict[str, int] = field(default_factory=dict)
    type_percentages: dict[str, float] = field(default_factory=dict)
    expected_percentages: dict[str, float] = field(default_factory=dict)
    deviation: dict[str, float] = field(default_factory=dict)
    missing_types: list[str] = field(default_factory=list)
    score: float = 0.0
    total: int = 0


@dataclass
class TopicCoverage:
    """Analysis of which --topics were actually discussed in conversations."""

    defined_topics: list[str] = field(default_factory=list)
    covered_topics: list[str] = field(default_factory=list)
    uncovered_topics: list[str] = field(default_factory=list)
    topic_mention_counts: dict[str, int] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class ScenarioCoverage:
    """Analysis of scenario category/difficulty breadth."""

    categories_tested: list[str] = field(default_factory=list)
    all_categories: list[str] = field(
        default_factory=lambda: list(ALL_SCENARIO_CATEGORIES)
    )
    missing_categories: list[str] = field(default_factory=list)
    requested_scenarios: list[str] = field(default_factory=list)
    difficulties_tested: list[str] = field(default_factory=list)
    stress_included: bool = False
    score: float = 0.0


@dataclass
class JudgeCoverage:
    """Analysis of judge evaluation completeness."""

    active_judges: list[str] = field(default_factory=list)
    expected_judges: list[str] = field(default_factory=list)
    missing_judges: list[str] = field(default_factory=list)
    suspicious_judges: list[str] = field(default_factory=list)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    score: float = 0.0


# ━━━ Composite Report ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class CoverageReport:
    """Complete coverage analysis across all 4 dimensions."""

    # Per-dimension reports
    persona_type: PersonaTypeCoverage = field(default_factory=PersonaTypeCoverage)
    topic: TopicCoverage = field(default_factory=TopicCoverage)
    scenario: ScenarioCoverage = field(default_factory=ScenarioCoverage)
    judge: JudgeCoverage = field(default_factory=JudgeCoverage)

    # Composite
    overall_coverage: float = 0.0
    grade: CoverageGrade = CoverageGrade.F

    # Actionable output
    gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    # Metadata
    dimension_weights: dict[str, float] = field(default_factory=dict)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    total_conversations: int = 0
    total_personas: int = 0

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "overall_coverage": round(self.overall_coverage, 4),
            "grade": self.grade.value,
            "total_conversations": self.total_conversations,
            "total_personas": self.total_personas,
            "dimension_scores": {
                k: round(v, 4) for k, v in self.dimension_scores.items()
            },
            "dimension_weights": {
                k: round(v, 2) for k, v in self.dimension_weights.items()
            },
            "gaps": self.gaps,
            "recommendations": self.recommendations,
            "persona_type": {
                "score": round(self.persona_type.score, 4),
                "type_counts": self.persona_type.type_counts,
                "type_percentages": {
                    k: round(v, 4)
                    for k, v in self.persona_type.type_percentages.items()
                },
                "expected_percentages": self.persona_type.expected_percentages,
                "deviation": {
                    k: round(v, 4) for k, v in self.persona_type.deviation.items()
                },
                "missing_types": self.persona_type.missing_types,
            },
            "topic": {
                "score": round(self.topic.score, 4),
                "defined": self.topic.defined_topics,
                "covered": self.topic.covered_topics,
                "uncovered": self.topic.uncovered_topics,
                "mention_counts": self.topic.topic_mention_counts,
            },
            "scenario": {
                "score": round(self.scenario.score, 4),
                "categories_tested": self.scenario.categories_tested,
                "missing_categories": self.scenario.missing_categories,
                "requested_scenarios": self.scenario.requested_scenarios,
                "difficulties_tested": self.scenario.difficulties_tested,
                "stress_included": self.scenario.stress_included,
            },
            "judge": {
                "score": round(self.judge.score, 4),
                "active_judges": self.judge.active_judges,
                "missing_judges": self.judge.missing_judges,
                "suspicious_judges": self.judge.suspicious_judges,
                "verdict_counts": self.judge.verdict_counts,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> CoverageReport:
        """Deserialize from dictionary."""
        report = cls()
        report.overall_coverage = data.get("overall_coverage", 0.0)
        report.grade = CoverageGrade(data.get("grade", "F"))
        report.total_conversations = data.get("total_conversations", 0)
        report.total_personas = data.get("total_personas", 0)
        report.dimension_scores = data.get("dimension_scores", {})
        report.dimension_weights = data.get("dimension_weights", {})
        report.gaps = data.get("gaps", [])
        report.recommendations = data.get("recommendations", [])

        pt = data.get("persona_type", {})
        report.persona_type = PersonaTypeCoverage(
            score=pt.get("score", 0.0),
            type_counts=pt.get("type_counts", {}),
            type_percentages=pt.get("type_percentages", {}),
            expected_percentages=pt.get("expected_percentages", {}),
            deviation=pt.get("deviation", {}),
            missing_types=pt.get("missing_types", []),
        )

        tc = data.get("topic", {})
        report.topic = TopicCoverage(
            score=tc.get("score", 0.0),
            defined_topics=tc.get("defined", []),
            covered_topics=tc.get("covered", []),
            uncovered_topics=tc.get("uncovered", []),
            topic_mention_counts=tc.get("mention_counts", {}),
        )

        sc = data.get("scenario", {})
        report.scenario = ScenarioCoverage(
            score=sc.get("score", 0.0),
            categories_tested=sc.get("categories_tested", []),
            missing_categories=sc.get("missing_categories", []),
            requested_scenarios=sc.get("requested_scenarios", []),
            difficulties_tested=sc.get("difficulties_tested", []),
            stress_included=sc.get("stress_included", False),
        )

        jc = data.get("judge", {})
        report.judge = JudgeCoverage(
            score=jc.get("score", 0.0),
            active_judges=jc.get("active_judges", []),
            missing_judges=jc.get("missing_judges", []),
            suspicious_judges=jc.get("suspicious_judges", []),
            verdict_counts=jc.get("verdict_counts", {}),
        )

        return report
