"""
CLI Integration Tests for Coverage Metrics (P2 #11).

Tests cover:
- CoverageConfig construction from CLI args
- Coverage report generation from simulation data
- simtest coverage info command (listable)
- --min-coverage threshold enforcement
- Coverage report serialization in summary.json
"""

try:
    import pytest
except ImportError:
    pass

from ai_simtest_engine.coverage import (
    CoverageAnalyzer,
    CoverageConfig,
    CoverageGrade,
    CoverageReport,
)


# ━━━ Helper: Simulates CLI → CoverageConfig mapping ━━━━━━━━━━━━━━━

def build_coverage_config_from_cli(
    topics: list[str] | None = None,
    scenario_ids: list[str] | None = None,
    stress_enabled: bool = False,
    min_coverage: float | None = None,
) -> CoverageConfig:
    """
    Simulates CLI → CoverageConfig mapping.
    This mirrors what cli.py will do after integration.
    """
    return CoverageConfig(
        defined_topics=topics or [],
        scenario_ids=scenario_ids or [],
        stress_enabled=stress_enabled,
    )


def check_coverage_gate(
    coverage_score: float,
    min_coverage: float | None,
) -> tuple[bool, str]:
    """
    Simulates the --min-coverage CI/CD gate.
    Returns (passed, message).
    """
    if min_coverage is None:
        return True, "No minimum coverage threshold set"

    if coverage_score >= min_coverage:
        return True, f"Coverage {coverage_score:.0%} meets threshold {min_coverage:.0%}"
    else:
        return False, f"Coverage {coverage_score:.0%} below threshold {min_coverage:.0%}"


# ━━━ Mock helpers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MockEnum:
    def __init__(self, value):
        self.value = value

class MockPersona:
    def __init__(self, name, ptype):
        self.name = name
        self.persona_type = MockEnum(ptype)
        self.id = f"p_{name}"

class MockTurn:
    def __init__(self, speaker, message):
        self.speaker = speaker
        self.message = message

class MockConversation:
    def __init__(self, cid, pid, turns):
        self.id = cid
        self.persona_id = pid
        self.turns = turns

class MockJudgment:
    def __init__(self, name, score):
        self.judge_name = name
        self.score = score

class MockJudgedTurn:
    def __init__(self, turn, judgments):
        self.turn = turn
        self.judgments = judgments

class MockJudgedConversation:
    def __init__(self, conv, judged_turns):
        self.conversation = conv
        self.judged_turns = judged_turns
        self.overall_score = 0.7
        self.failure_modes = []


def make_simple_data(n_personas=5):
    """Create minimal test data."""
    personas = []
    convs = []
    types = ["standard"] * 3 + ["edge_case"] * 1 + ["adversarial"] * 1

    for i in range(n_personas):
        p = MockPersona(f"P{i}", types[i % len(types)])
        personas.append(p)

        turns = [MockTurn("user", "billing help"), MockTurn("bot", "billing answer")]
        jt = MockJudgedTurn(
            turns[1],
            [MockJudgment("grounding", 0.9), MockJudgment("safety", 0.8),
             MockJudgment("quality", 0.7), MockJudgment("relevance", 0.75)],
        )
        conv = MockConversation(f"c{i}", p.id, turns)
        convs.append(MockJudgedConversation(conv, [jt]))

    return personas, convs


# ━━━ Config Construction from CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCLIConfigConstruction:

    def test_default_config(self):
        config = build_coverage_config_from_cli()
        assert config.defined_topics == []
        assert config.scenario_ids == []
        assert config.stress_enabled is False

    def test_with_topics(self):
        config = build_coverage_config_from_cli(topics=["billing", "refunds"])
        assert config.defined_topics == ["billing", "refunds"]

    def test_with_scenarios(self):
        config = build_coverage_config_from_cli(scenario_ids=["goal_shift", "prompt_injection"])
        assert config.scenario_ids == ["goal_shift", "prompt_injection"]

    def test_with_stress(self):
        config = build_coverage_config_from_cli(stress_enabled=True)
        assert config.stress_enabled is True

    def test_combined(self):
        config = build_coverage_config_from_cli(
            topics=["billing"],
            scenario_ids=["goal_shift"],
            stress_enabled=True,
        )
        assert config.defined_topics == ["billing"]
        assert config.scenario_ids == ["goal_shift"]
        assert config.stress_enabled is True


# ━━━ Coverage Gate (--min-coverage) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCoverageGate:

    def test_no_threshold(self):
        passed, msg = check_coverage_gate(0.3, None)
        assert passed is True

    def test_meets_threshold(self):
        passed, msg = check_coverage_gate(0.85, 0.80)
        assert passed is True
        assert "meets" in msg

    def test_below_threshold(self):
        passed, msg = check_coverage_gate(0.65, 0.80)
        assert passed is False
        assert "below" in msg

    def test_exact_threshold(self):
        passed, msg = check_coverage_gate(0.80, 0.80)
        assert passed is True

    def test_zero_coverage(self):
        passed, msg = check_coverage_gate(0.0, 0.50)
        assert passed is False


# ━━━ Report from Simulation Data ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCoverageFromSimulation:

    def test_basic_analysis(self):
        personas, convs = make_simple_data(5)
        config = build_coverage_config_from_cli(topics=["billing"])
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert report.overall_coverage > 0
        assert report.grade is not None
        assert report.total_personas == 5
        assert report.total_conversations == 5

    def test_coverage_in_summary_dict(self):
        personas, convs = make_simple_data(5)
        config = build_coverage_config_from_cli()
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        data = report.to_dict()
        assert "overall_coverage" in data
        assert "grade" in data
        assert "dimension_scores" in data
        assert "gaps" in data

    def test_grade_reflects_score(self):
        personas, convs = make_simple_data(20)
        config = build_coverage_config_from_cli(
            topics=["billing"],
            scenario_ids=["goal_shift", "prompt_injection"],
            stress_enabled=True,
        )
        analyzer = CoverageAnalyzer()
        report = analyzer.analyze(personas, convs, config)

        assert report.grade == CoverageGrade.from_score(report.overall_coverage)


# ━━━ Simtest Coverage Info ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSimtestCoverageInfo:

    def test_dimensions_listable(self):
        """Coverage dimensions should be enumerable for the info command."""
        dimensions = ["persona_type", "topic", "scenario", "judge"]
        report = CoverageReport()
        data = report.to_dict()
        for dim in dimensions:
            assert dim in data, f"Missing dimension: {dim}"

    def test_grades_listable(self):
        """All grade values should be accessible."""
        grades = [g.value for g in CoverageGrade]
        assert "A" in grades
        assert "B" in grades
        assert "C" in grades
        assert "D" in grades
        assert "F" in grades
