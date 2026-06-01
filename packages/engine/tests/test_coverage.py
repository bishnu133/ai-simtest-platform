"""
Test suite for Coverage Metrics module (P2 #11).

Tests cover:
- CoverageGrade letter grading
- PersonaTypeCoverage analysis
- TopicCoverage analysis
- ScenarioCoverage analysis
- JudgeCoverage analysis
- Overall composite scoring with dynamic weights
- Gap and recommendation generation
- Edge cases (empty data, single persona, etc.)
- Serialization roundtrip (to_dict / from_dict)
- Integration with mock simulation data
"""

try:
    import pytest
except ImportError:
    pass  # Running standalone without pytest

from ai_simtest_engine.coverage import (
    CoverageAnalyzer,
    CoverageConfig,
    CoverageGrade,
    CoverageReport,
    PersonaTypeCoverage,
    TopicCoverage,
    ScenarioCoverage,
    JudgeCoverage,
    SCENARIO_METADATA,
)


# ━━━ Mock Data Helpers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MockPersona:
    """Lightweight mock persona."""
    def __init__(self, name: str, persona_type: str, topics: list[str] | None = None):
        self.name = name
        self.persona_type = MockEnum(persona_type)
        self.topics = topics or []
        self.id = f"persona_{name.lower().replace(' ', '_')}"


class MockEnum:
    """Mock enum with .value property."""
    def __init__(self, value: str):
        self.value = value


class MockTurn:
    def __init__(self, speaker: str, message: str):
        self.speaker = speaker
        self.message = message


class MockConversation:
    def __init__(self, conv_id: str, persona_id: str, turns: list[MockTurn]):
        self.id = conv_id
        self.persona_id = persona_id
        self.turns = turns


class MockJudgment:
    def __init__(self, judge_name: str, score: float, passed: bool = True):
        self.judge_name = judge_name
        self.score = score
        self.passed = passed


class MockJudgedTurn:
    def __init__(self, turn: MockTurn, judgments: list[MockJudgment]):
        self.turn = turn
        self.judgments = judgments
        self.overall_label = "PASS" if all(j.passed for j in judgments) else "FAIL"
        self.overall_score = sum(j.score for j in judgments) / len(judgments) if judgments else 0


class MockJudgedConversation:
    def __init__(self, conversation: MockConversation, judged_turns: list[MockJudgedTurn]):
        self.conversation = conversation
        self.judged_turns = judged_turns
        self.overall_score = (
            sum(jt.overall_score for jt in judged_turns) / len(judged_turns)
            if judged_turns else 0
        )
        self.failure_modes = []


def make_judged_turn(message: str, judge_scores: dict[str, float]) -> MockJudgedTurn:
    """Helper to create a judged turn with multiple judges."""
    turn = MockTurn("bot", message)
    judgments = [
        MockJudgment(name, score, passed=(score >= 0.7))
        for name, score in judge_scores.items()
    ]
    return MockJudgedTurn(turn, judgments)


def make_conversation(
    conv_id: str,
    persona_id: str,
    messages: list[str],
    judge_scores: dict[str, float] | None = None,
) -> MockJudgedConversation:
    """Helper to create a mock judged conversation."""
    if judge_scores is None:
        judge_scores = {"grounding": 0.9, "safety": 0.8, "quality": 0.7, "relevance": 0.75}

    turns = []
    judged_turns = []
    for i, msg in enumerate(messages):
        if i % 2 == 0:
            turns.append(MockTurn("user", msg))
        else:
            t = MockTurn("bot", msg)
            turns.append(t)
            judged_turns.append(make_judged_turn(msg, judge_scores))

    conv = MockConversation(conv_id, persona_id, turns)
    return MockJudgedConversation(conv, judged_turns)


# ━━━ Standard Test Fixtures ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def standard_personas(n: int = 20) -> list[MockPersona]:
    """Generate standard 70/20/10 distribution."""
    personas = []
    # 14 standard, 4 edge_case, 2 adversarial (for 20)
    std_count = int(n * 0.7)
    edge_count = int(n * 0.2)
    adv_count = n - std_count - edge_count

    for i in range(std_count):
        personas.append(MockPersona(f"Standard User {i}", "standard"))
    for i in range(edge_count):
        personas.append(MockPersona(f"Edge Case {i}", "edge_case"))
    for i in range(adv_count):
        personas.append(MockPersona(f"Adversarial {i}", "adversarial"))

    return personas


def standard_conversations(
    personas: list[MockPersona],
    topic_messages: list[str] | None = None,
) -> list[MockJudgedConversation]:
    """Generate mock conversations for each persona."""
    if topic_messages is None:
        topic_messages = [
            "I need help with my billing issue",
            "Sure, I can help with billing. What's the problem?",
            "I want a refund for my order",
            "I'll process your refund request.",
        ]

    convs = []
    for p in personas:
        convs.append(make_conversation(
            conv_id=f"conv_{p.id}",
            persona_id=p.id,
            messages=topic_messages,
        ))
    return convs


# ━━━ Test: CoverageGrade ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCoverageGrade:

    def test_grade_A(self):
        assert CoverageGrade.from_score(0.95) == CoverageGrade.A
        assert CoverageGrade.from_score(0.90) == CoverageGrade.A

    def test_grade_B(self):
        assert CoverageGrade.from_score(0.80) == CoverageGrade.B
        assert CoverageGrade.from_score(0.75) == CoverageGrade.B

    def test_grade_C(self):
        assert CoverageGrade.from_score(0.65) == CoverageGrade.C
        assert CoverageGrade.from_score(0.60) == CoverageGrade.C

    def test_grade_D(self):
        assert CoverageGrade.from_score(0.50) == CoverageGrade.D
        assert CoverageGrade.from_score(0.40) == CoverageGrade.D

    def test_grade_F(self):
        assert CoverageGrade.from_score(0.30) == CoverageGrade.F
        assert CoverageGrade.from_score(0.0) == CoverageGrade.F

    def test_grade_boundary_values(self):
        assert CoverageGrade.from_score(0.899) == CoverageGrade.B
        assert CoverageGrade.from_score(0.749) == CoverageGrade.C
        assert CoverageGrade.from_score(0.599) == CoverageGrade.D
        assert CoverageGrade.from_score(0.399) == CoverageGrade.F


# ━━━ Test: Persona-Type Coverage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPersonaTypeCoverage:

    def test_perfect_distribution(self):
        personas = standard_personas(20)
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, convs, CoverageConfig())

        assert result.total == 20
        assert "standard" in result.type_counts
        assert "edge_case" in result.type_counts
        assert "adversarial" in result.type_counts
        assert result.missing_types == []
        assert result.score > 0.8

    def test_missing_adversarial(self):
        personas = [MockPersona(f"Std {i}", "standard") for i in range(8)]
        personas += [MockPersona(f"Edge {i}", "edge_case") for i in range(2)]
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, convs, CoverageConfig())

        assert "adversarial" in result.missing_types
        assert result.score < 0.8

    def test_single_type_only(self):
        personas = [MockPersona(f"Std {i}", "standard") for i in range(10)]
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, convs, CoverageConfig())

        assert "edge_case" in result.missing_types
        assert "adversarial" in result.missing_types
        assert result.score < 0.5

    def test_empty_personas(self):
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types([], [], CoverageConfig())

        assert result.total == 0
        assert result.score == 0.0
        assert len(result.missing_types) == 3

    def test_custom_expected_distribution(self):
        config = CoverageConfig(
            expected_persona_distribution={"standard": 0.5, "edge_case": 0.5}
        )
        personas = [MockPersona(f"Std {i}", "standard") for i in range(5)]
        personas += [MockPersona(f"Edge {i}", "edge_case") for i in range(5)]
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, convs, config)

        assert result.missing_types == []
        assert result.score > 0.9

    def test_deviation_calculation(self):
        personas = standard_personas(20)
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, convs, CoverageConfig())

        # Deviations should exist for all expected types
        assert "standard" in result.deviation
        assert "edge_case" in result.deviation
        assert "adversarial" in result.deviation


# ━━━ Test: Topic Coverage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestTopicCoverage:

    def test_all_topics_covered(self):
        config = CoverageConfig(defined_topics=["billing", "refund"])
        personas = [MockPersona("User", "standard")]
        convs = [make_conversation(
            "c1", "persona_user",
            ["Help with billing", "Sure, billing help here",
             "I need a refund", "Processing refund now"],
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_topics(convs, config)

        assert result.score == 1.0
        assert result.covered_topics == ["billing", "refund"]
        assert result.uncovered_topics == []

    def test_partial_topic_coverage(self):
        config = CoverageConfig(defined_topics=["billing", "shipping", "returns"])
        convs = [make_conversation(
            "c1", "p1",
            ["Billing question", "Here's your billing info"],
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_topics(convs, config)

        assert "billing" in result.covered_topics
        assert "shipping" in result.uncovered_topics
        assert "returns" in result.uncovered_topics
        assert abs(result.score - 1 / 3) < 0.01

    def test_no_topics_defined(self):
        config = CoverageConfig(defined_topics=[])
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_topics([], config)

        assert result.score == 1.0  # Nothing to miss

    def test_no_topics_covered(self):
        config = CoverageConfig(defined_topics=["quantum", "physics"])
        convs = [make_conversation("c1", "p1", ["Hello", "Hi there"])]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_topics(convs, config)

        assert result.score == 0.0
        assert len(result.uncovered_topics) == 2

    def test_topic_mention_counts(self):
        config = CoverageConfig(defined_topics=["billing"])
        convs = [make_conversation(
            "c1", "p1",
            ["billing question about billing", "Sure, billing info here"],
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_topics(convs, config)

        assert result.topic_mention_counts["billing"] >= 2

    def test_case_insensitive_matching(self):
        config = CoverageConfig(defined_topics=["Billing"])
        convs = [make_conversation(
            "c1", "p1", ["help with BILLING", "Sure thing"],
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_topics(convs, config)

        assert "Billing" in result.covered_topics


# ━━━ Test: Scenario Coverage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestScenarioCoverage:

    def test_all_scenarios(self):
        config = CoverageConfig(
            scenario_ids=list(SCENARIO_METADATA.keys()),
            stress_enabled=True,
        )
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        assert result.score > 0.9
        assert result.missing_categories == []
        assert result.stress_included is True

    def test_single_scenario(self):
        config = CoverageConfig(scenario_ids=["prompt_injection"])
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        assert "safety" in result.categories_tested
        assert "hard" in result.difficulties_tested
        assert result.score < 0.5  # Only 1 category of 5

    def test_no_scenarios(self):
        config = CoverageConfig(scenario_ids=[], stress_enabled=False)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        assert result.score == 0.0
        assert len(result.missing_categories) == 5

    def test_stress_only(self):
        config = CoverageConfig(scenario_ids=[], stress_enabled=True)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        assert "memory" in result.categories_tested
        assert result.stress_included is True
        assert result.score > 0.0

    def test_category_dedup(self):
        # Two quality scenarios should only count quality once
        config = CoverageConfig(
            scenario_ids=["clarification_required", "multi_intent", "correction_loop"]
        )
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        assert result.categories_tested.count("quality") == 1

    def test_difficulty_spread(self):
        config = CoverageConfig(
            scenario_ids=["clarification_required", "goal_shift", "prompt_injection"]
        )
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        assert "easy" in result.difficulties_tested
        assert "medium" in result.difficulties_tested
        assert "hard" in result.difficulties_tested

    def test_unknown_scenario_ignored(self):
        config = CoverageConfig(scenario_ids=["nonexistent_scenario"])
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_scenarios(config)

        # Unknown scenarios don't contribute to coverage
        assert result.categories_tested == []


# ━━━ Test: Judge Coverage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestJudgeCoverage:

    def test_all_judges_active(self):
        convs = [make_conversation(
            "c1", "p1",
            ["Q", "A"],
            {"grounding": 0.9, "safety": 0.8, "quality": 0.7, "relevance": 0.75},
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_judges(convs, CoverageConfig())

        assert set(result.active_judges) == {"grounding", "safety", "quality", "relevance"}
        assert result.missing_judges == []
        assert result.score == 1.0

    def test_missing_judge(self):
        convs = [make_conversation(
            "c1", "p1",
            ["Q", "A"],
            {"grounding": 0.9, "safety": 0.8},  # Missing quality + relevance
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_judges(convs, CoverageConfig())

        assert "quality" in result.missing_judges
        assert "relevance" in result.missing_judges
        assert result.score == 0.5

    def test_suspicious_judge_all_pass(self):
        """Judge with 100% score on 3+ turns is suspicious."""
        convs = []
        for i in range(3):
            convs.append(make_conversation(
                f"c{i}", f"p{i}",
                ["Q", "A"],
                {"grounding": 1.0, "safety": 0.8, "quality": 0.7, "relevance": 0.6},
            ))
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_judges(convs, CoverageConfig())

        assert "grounding" in result.suspicious_judges

    def test_no_conversations(self):
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_judges([], CoverageConfig())

        assert result.score == 0.0
        assert len(result.missing_judges) == 4

    def test_verdict_counts(self):
        convs = [make_conversation(
            "c1", "p1",
            ["Q1", "A1", "Q2", "A2"],
            {"grounding": 0.9, "safety": 0.8},
        )]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_judges(convs, CoverageConfig())

        assert result.verdict_counts["grounding"] == 2
        assert result.verdict_counts["safety"] == 2


# ━━━ Test: Overall Composite ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestOverallCoverage:

    def test_full_coverage_high_score(self):
        personas = standard_personas(20)
        convs = standard_conversations(personas, [
            "billing question", "billing answer",
            "refund request", "refund processed",
        ])
        config = CoverageConfig(
            defined_topics=["billing", "refund"],
            scenario_ids=list(SCENARIO_METADATA.keys()),
            stress_enabled=True,
        )
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert report.overall_coverage > 0.8
        assert report.grade in (CoverageGrade.A, CoverageGrade.B)

    def test_minimal_run_low_score(self):
        personas = [MockPersona("Solo", "standard")]
        convs = [make_conversation("c1", "persona_solo", ["Hi", "Hello"])]
        config = CoverageConfig()

        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert report.overall_coverage < 0.6
        assert report.grade in (CoverageGrade.D, CoverageGrade.F)

    def test_dynamic_weights_no_topics(self):
        """When no topics defined, topic weight should be 0."""
        config = CoverageConfig(defined_topics=[])
        personas = standard_personas(10)
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert report.dimension_weights["topic"] == 0.0

    def test_dynamic_weights_with_topics(self):
        """When topics defined, topic weight should be >0."""
        config = CoverageConfig(defined_topics=["billing"])
        personas = standard_personas(10)
        convs = standard_conversations(personas, [
            "billing issue", "billing help"
        ])
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert report.dimension_weights["topic"] > 0.0

    def test_gaps_generated(self):
        personas = [MockPersona("Only Std", "standard")]
        convs = [make_conversation("c1", "persona_only_std", ["Hi", "Hello"])]
        config = CoverageConfig(
            defined_topics=["billing"],
            scenario_ids=["prompt_injection"],
        )
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert len(report.gaps) > 0
        gap_text = " ".join(report.gaps)
        assert "adversarial" in gap_text or "edge_case" in gap_text

    def test_recommendations_generated(self):
        personas = [MockPersona("Solo", "standard")]
        convs = [make_conversation("c1", "persona_solo", ["Hi", "Hello"])]
        config = CoverageConfig()

        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert len(report.recommendations) > 0

    def test_dimension_scores_populated(self):
        personas = standard_personas(10)
        convs = standard_conversations(personas)
        config = CoverageConfig()

        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert "persona_type" in report.dimension_scores
        assert "topic" in report.dimension_scores
        assert "scenario" in report.dimension_scores
        assert "judge" in report.dimension_scores


# ━━━ Test: Serialization ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSerialization:

    def test_to_dict_roundtrip(self):
        personas = standard_personas(10)
        convs = standard_conversations(personas)
        config = CoverageConfig(
            defined_topics=["billing"],
            scenario_ids=["goal_shift"],
            stress_enabled=True,
        )
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        data = report.to_dict()
        restored = CoverageReport.from_dict(data)

        assert abs(restored.overall_coverage - report.overall_coverage) < 0.001
        assert restored.grade == report.grade
        assert restored.gaps == report.gaps
        assert restored.total_conversations == report.total_conversations

    def test_to_dict_has_all_dimensions(self):
        report = CoverageReport()
        data = report.to_dict()

        assert "persona_type" in data
        assert "topic" in data
        assert "scenario" in data
        assert "judge" in data
        assert "overall_coverage" in data
        assert "grade" in data
        assert "gaps" in data

    def test_from_dict_empty(self):
        restored = CoverageReport.from_dict({})
        assert restored.overall_coverage == 0.0
        assert restored.grade == CoverageGrade.F


# ━━━ Test: Edge Cases ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEdgeCases:

    def test_empty_everything(self):
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze([], [], CoverageConfig())

        assert report.overall_coverage >= 0.0
        assert report.grade is not None
        assert report.total_conversations == 0
        assert report.total_personas == 0

    def test_single_persona_single_conversation(self):
        personas = [MockPersona("Solo", "standard")]
        convs = [make_conversation("c1", "persona_solo", ["Q", "A"])]
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, CoverageConfig())

        assert 0.0 <= report.overall_coverage <= 1.0
        assert report.total_conversations == 1

    def test_dict_personas_fallback(self):
        """Ensure analyzer works with dict personas too."""
        personas = [{"persona_type": "standard"}, {"persona_type": "edge_case"}]
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, [], CoverageConfig())

        assert result.type_counts.get("standard", 0) == 1
        assert result.type_counts.get("edge_case", 0) == 1

    def test_unknown_persona_type(self):
        personas = [MockPersona("Mystery", "unknown_type")]
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        result = analyzer._analyze_persona_types(personas, convs, CoverageConfig())

        assert "unknown_type" in result.type_counts

    def test_large_persona_count(self):
        personas = standard_personas(100)
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, CoverageConfig())

        assert report.total_personas == 100
        assert report.persona_type.score > 0.7


# ━━━ Test: Integration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIntegration:

    def test_full_pipeline(self):
        """Simulate a realistic test run with all features enabled."""
        personas = standard_personas(20)
        messages = [
            "I need help with my billing account",
            "I can help with billing. What's the issue?",
            "I want to return this item for a refund",
            "I'll process your refund. Anything about shipping?",
            "Yes, shipping to Portland Oregon",
            "Shipping to Portland is available.",
        ]
        convs = standard_conversations(personas, messages)

        config = CoverageConfig(
            defined_topics=["billing", "refund", "shipping"],
            scenario_ids=["goal_shift", "prompt_injection", "emotional_escalation"],
            stress_enabled=True,
        )

        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        # Verify all dimensions computed
        assert report.persona_type.score > 0
        assert report.topic.score > 0
        assert report.scenario.score > 0
        assert report.judge.score > 0

        # Verify overall is reasonable
        assert 0.0 <= report.overall_coverage <= 1.0
        assert report.grade is not None

        # Verify serialization works
        data = report.to_dict()
        assert isinstance(data, dict)
        assert data["grade"] in ("A", "B", "C", "D", "F")

    def test_coverage_improves_with_more_features(self):
        """Adding scenarios and stress should improve coverage."""
        personas = standard_personas(10)
        convs = standard_conversations(personas)
        analyzer = CoverageAnalyzer()

        # Bare minimum run
        report_basic = analyzer.analyze(personas, convs, CoverageConfig())

        # With scenarios + stress
        report_full = analyzer.analyze(personas, convs, CoverageConfig(
            scenario_ids=["goal_shift", "prompt_injection"],
            stress_enabled=True,
        ))

        assert report_full.overall_coverage > report_basic.overall_coverage

    def test_scenario_metadata_consistency(self):
        """Verify all 8 built-in scenarios have metadata."""
        expected_ids = [
            "clarification_required", "goal_shift", "emotional_escalation",
            "prompt_injection", "out_of_scope", "multi_intent",
            "correction_loop", "context_retention",
        ]
        for sid in expected_ids:
            assert sid in SCENARIO_METADATA, f"Missing metadata for {sid}"
            assert "category" in SCENARIO_METADATA[sid]
            assert "difficulty" in SCENARIO_METADATA[sid]
