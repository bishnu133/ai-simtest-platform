"""
Tests for Multi-Model Comparison — Phase 4 (N-Way Comparison Engine).

Covers all review points:
- #1: Statistical confidence + evidence_basis, gate restrictions
- #2: Coverage parity (ok/degraded/invalid), strict mode blocking
- #3: Cost efficiency (value-based, not just price)
- #4: ConversationKey as primary comparison unit
- #5: Tied ranks, "no clear winner"
- #6: Forensic confidence on root causes
- #7: Evidence transparency (num_cases_used, coverage_basis)
- #8: Slice-based analysis (scenario, persona_type)
- #9: Confidence-aware recommendations
- #10: Pairwise diff explanations
- #11: Trial-ready interfaces
- #12-15: Product differentiators
"""

from __future__ import annotations

import pytest

from src.multi_compare.models import (
    ComparisonMode,
    CostEfficiency,
    Dimension,
    GateVerdict,
    INVERTED_DIMENSIONS,
    ModelRunResult,
    ModelRunStatus,
    RootCauseBucket,
    StatisticalVerdictLabel,
    BUILT_IN_PROFILES,
)
from src.multi_compare.analysis import (
    AnalysisConfig,
    AnalysisMode,
    ConfidenceLevel,
    CostAnalyzer,
    CoverageParityChecker,
    DiffExplainer,
    DimensionExtractor,
    EvidenceBasis,
    EvidenceInfo,
    ExtractedDimension,
    ForensicAnalyzer,
    NWayComparisonEngine,
    ParityStatus,
    RecommendationGenerator,
    SliceAnalyzer,
    StatisticalAnalyzer,
    _safe_div,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

def _make_result(
    model_id: str,
    pass_rate: float = 0.8,
    score_by_judge: dict | None = None,
    total_conversations: int = 10,
    passed_conversations: int = 8,
    critical_failures: int = 0,
    total_cost: float = 5.0,
    status: ModelRunStatus = ModelRunStatus.SUCCESS,
) -> ModelRunResult:
    if score_by_judge is None:
        score_by_judge = {
            "grounding": 0.75, "safety": 0.95,
            "quality": 0.72, "relevance": 0.80,
        }
    return ModelRunResult(
        model_id=model_id,
        model_name=model_id.replace("-", " ").title(),
        status=status,
        summary_dict={
            "pass_rate": pass_rate,
            "average_score": sum(score_by_judge.values()) / len(score_by_judge),
            "total_conversations": total_conversations,
            "passed_conversations": passed_conversations,
            "failed_conversations": total_conversations - passed_conversations,
            "score_by_judge": score_by_judge,
            "critical_failures": critical_failures,
        },
        cost_report_dict={"total_estimated_cost": total_cost},
        execution_time_seconds=30.0,
    )


# ═══════════════════════════════════════════════════════════════
# SECTION 1: DimensionExtractor (Review #4, #7)
# ═══════════════════════════════════════════════════════════════


class TestDimensionExtractor:
    def test_extracts_all_dimensions(self):
        result = _make_result("model-a")
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=10)

        dim_names = {d.dimension for d in dims}
        assert Dimension.OVERALL_PASS_RATE in dim_names
        assert Dimension.GROUNDING in dim_names
        assert Dimension.SAFETY in dim_names
        assert Dimension.QUALITY in dim_names
        assert Dimension.RELEVANCE in dim_names
        assert Dimension.CRITICAL_FAILURE_RATE in dim_names
        assert Dimension.COST_EFFICIENCY in dim_names

    def test_evidence_high_confidence(self):
        """Review #7: Evidence transparency with high sample."""
        result = _make_result("model-a", total_conversations=15)
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=15)
        for d in dims:
            assert d.evidence.confidence == ConfidenceLevel.HIGH
            assert d.evidence.num_cases_used == 15

    def test_evidence_medium_confidence(self):
        result = _make_result("model-a", total_conversations=7, passed_conversations=5)
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=10)
        for d in dims:
            assert d.evidence.confidence == ConfidenceLevel.MEDIUM

    def test_evidence_low_confidence(self):
        result = _make_result("model-a", total_conversations=3, passed_conversations=2)
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=10)
        for d in dims:
            assert d.evidence.confidence == ConfidenceLevel.LOW
            assert d.evidence.num_cases_missing == 7

    def test_cost_efficiency_score(self):
        result = _make_result("model-a", total_conversations=10, total_cost=5.0)
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=10)
        cost_dim = next(d for d in dims if d.dimension == Dimension.COST_EFFICIENCY)
        assert cost_dim.score == 0.5  # $5 / 10 conversations

    def test_zero_cost_model(self):
        result = _make_result("model-a", total_cost=0.0)
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=10)
        cost_dim = next(d for d in dims if d.dimension == Dimension.COST_EFFICIENCY)
        assert cost_dim.score == 0.0  # Free model

    def test_critical_failure_rate(self):
        result = _make_result("model-a", critical_failures=2, total_conversations=10)
        extractor = DimensionExtractor()
        dims = extractor.extract(result, total_personas=10)
        cfr = next(d for d in dims if d.dimension == Dimension.CRITICAL_FAILURE_RATE)
        assert cfr.score == 0.2


# ═══════════════════════════════════════════════════════════════
# SECTION 2: StatisticalAnalyzer (Review #1, #5)
# ═══════════════════════════════════════════════════════════════


class TestStatisticalAnalyzer:
    def setup_method(self):
        self.analyzer = StatisticalAnalyzer(significance_threshold=0.03)

    def test_clear_winner_high_confidence(self):
        v = self.analyzer.compare_dimension(
            champion_score=0.70, challenger_score=0.85,
            is_inverted=False, sample_size=20, confidence=ConfidenceLevel.HIGH,
        )
        assert v.verdict == StatisticalVerdictLabel.CLEAR_WINNER
        assert v.practical_significance == "large"

    def test_likely_winner_high_confidence(self):
        v = self.analyzer.compare_dimension(
            champion_score=0.80, challenger_score=0.84,
            is_inverted=False, sample_size=15, confidence=ConfidenceLevel.HIGH,
        )
        assert v.verdict == StatisticalVerdictLabel.LIKELY_WINNER

    def test_no_clear_winner_negligible_delta(self):
        """Review #5: Negligible difference → no winner."""
        v = self.analyzer.compare_dimension(
            champion_score=0.80, challenger_score=0.81,
            is_inverted=False, sample_size=20, confidence=ConfidenceLevel.HIGH,
        )
        assert v.verdict == StatisticalVerdictLabel.NO_CLEAR_WINNER
        assert v.practical_significance == "negligible"

    def test_insufficient_data_low_confidence(self):
        """Review #1: Low confidence blocks strong verdicts."""
        v = self.analyzer.compare_dimension(
            champion_score=0.80, challenger_score=0.84,
            is_inverted=False, sample_size=3, confidence=ConfidenceLevel.LOW,
        )
        assert v.verdict == StatisticalVerdictLabel.INSUFFICIENT_DATA

    def test_leaning_low_confidence_large_delta(self):
        """Low confidence with huge delta → leaning, not clear."""
        v = self.analyzer.compare_dimension(
            champion_score=0.50, challenger_score=0.90,
            is_inverted=False, sample_size=3, confidence=ConfidenceLevel.LOW,
        )
        assert v.verdict == StatisticalVerdictLabel.LEANING

    def test_inverted_dimension(self):
        """Lower is better for inverted dimensions."""
        v = self.analyzer.compare_dimension(
            champion_score=0.20, challenger_score=0.05,
            is_inverted=True, sample_size=15, confidence=ConfidenceLevel.HIGH,
        )
        # Challenger is better (lower score on inverted dim)
        assert v.effect_size > 0  # positive = challenger wins

    def test_medium_confidence_likely_winner(self):
        v = self.analyzer.compare_dimension(
            champion_score=0.70, challenger_score=0.80,
            is_inverted=False, sample_size=8, confidence=ConfidenceLevel.MEDIUM,
        )
        assert v.verdict == StatisticalVerdictLabel.LIKELY_WINNER

    def test_trial_ready_interface(self):
        """Review #11: Trial-ready placeholder."""
        v = self.analyzer.compare_dimension_trial_ready(
            champion_values=[0.7, 0.8, 0.75, 0.9, 0.85, 0.72, 0.88, 0.76, 0.82, 0.79],
            challenger_values=[0.85, 0.9, 0.88, 0.92, 0.87, 0.91, 0.86, 0.89, 0.93, 0.84],
            is_inverted=False,
        )
        assert v.verdict in (
            StatisticalVerdictLabel.CLEAR_WINNER,
            StatisticalVerdictLabel.LIKELY_WINNER,
        )
        assert v.sample_size == 10

    def test_trial_ready_empty_data(self):
        v = self.analyzer.compare_dimension_trial_ready([], [], False)
        assert v.verdict == StatisticalVerdictLabel.INSUFFICIENT_DATA


# ═══════════════════════════════════════════════════════════════
# SECTION 3: CoverageParityChecker (Review #2)
# ═══════════════════════════════════════════════════════════════


class TestCoverageParityChecker:
    def test_ok_parity(self):
        checker = CoverageParityChecker()
        results = {
            "a": _make_result("a", total_conversations=10),
            "b": _make_result("b", total_conversations=10),
        }
        status, reports = checker.check(results, total_personas=10)
        assert status == ParityStatus.OK
        assert all(r.parity_warning is None for r in reports)

    def test_degraded_parity(self):
        checker = CoverageParityChecker(degraded_threshold=0.85)
        results = {
            "a": _make_result("a", total_conversations=10),
            "b": _make_result("b", total_conversations=7, passed_conversations=5),
        }
        status, reports = checker.check(results, total_personas=10)
        assert status == ParityStatus.DEGRADED
        b_report = next(r for r in reports if r.model_id == "b")
        assert b_report.parity_warning is not None
        assert "degraded" in b_report.parity_warning.lower() or "below" in b_report.parity_warning.lower()

    def test_invalid_parity(self):
        checker = CoverageParityChecker(invalid_threshold=0.50)
        results = {
            "a": _make_result("a", total_conversations=10),
            "b": _make_result("b", total_conversations=3, passed_conversations=2),
        }
        status, reports = checker.check(results, total_personas=10)
        assert status == ParityStatus.INVALID

    def test_failed_model_excluded(self):
        checker = CoverageParityChecker()
        results = {
            "a": _make_result("a"),
            "b": ModelRunResult(model_id="b", model_name="B", status=ModelRunStatus.FAILED),
        }
        status, reports = checker.check(results, total_personas=10)
        assert len(reports) == 1  # Only usable model included


# ═══════════════════════════════════════════════════════════════
# SECTION 4: CostAnalyzer (Review #3)
# ═══════════════════════════════════════════════════════════════


class TestCostAnalyzer:
    def test_cost_per_successful_conversation(self):
        """Review #3: Value-based cost, not just raw total."""
        result = _make_result("a", total_cost=10.0, total_conversations=10, passed_conversations=8)
        analyzer = CostAnalyzer()
        eff, gov = analyzer.analyze(result)
        assert eff.cost_per_conversation == 1.0
        assert eff.cost_per_successful_conversation == 1.25  # $10 / 8

    def test_quality_per_dollar(self):
        result = _make_result("a", total_cost=5.0)
        analyzer = CostAnalyzer()
        eff, _ = analyzer.analyze(result)
        assert eff.quality_per_dollar is not None
        assert eff.quality_per_dollar > 0

    def test_zero_cost_model(self):
        result = _make_result("a", total_cost=0.0)
        analyzer = CostAnalyzer()
        eff, _ = analyzer.analyze(result)
        assert eff.total_cost == 0.0
        assert eff.quality_per_dollar is None  # Can't divide by zero

    def test_zero_successful_conversations(self):
        result = _make_result("a", total_cost=5.0, passed_conversations=0)
        analyzer = CostAnalyzer()
        eff, _ = analyzer.analyze(result)
        assert eff.cost_per_successful_conversation is None  # safe_div returns None

    def test_budget_governance(self):
        result = _make_result("a", total_cost=12.0)
        result.cost_report_dict["budget_limit"] = 10.0
        analyzer = CostAnalyzer()
        _, gov = analyzer.analyze(result)
        assert gov.budget_exceeded is True
        assert gov.budget_utilization == 1.2


# ═══════════════════════════════════════════════════════════════
# SECTION 5: ForensicAnalyzer (Review #6)
# ═══════════════════════════════════════════════════════════════


class TestForensicAnalyzer:
    def test_maps_low_grounding_to_hallucination(self):
        result = _make_result("a", score_by_judge={
            "grounding": 0.30, "safety": 0.95, "quality": 0.80, "relevance": 0.80,
        })
        analyzer = ForensicAnalyzer()
        forensics = analyzer.analyze_failures(result)
        assert len(forensics) >= 1
        grounding_forensic = next(f for f in forensics if "grounding" in f.affected_dimensions)
        assert grounding_forensic.root_causes[0].bucket == RootCauseBucket.HALLUCINATION
        assert grounding_forensic.root_causes[0].severity == "medium"

    def test_critical_severity(self):
        result = _make_result("a", score_by_judge={
            "grounding": 0.10, "safety": 0.95, "quality": 0.80, "relevance": 0.80,
        })
        analyzer = ForensicAnalyzer()
        forensics = analyzer.analyze_failures(result)
        assert forensics[0].root_causes[0].severity == "critical"

    def test_regression_vs_champion(self):
        challenger = _make_result("b", score_by_judge={
            "grounding": 0.40, "safety": 0.95, "quality": 0.80, "relevance": 0.80,
        })
        champion = _make_result("a", score_by_judge={
            "grounding": 0.80, "safety": 0.95, "quality": 0.80, "relevance": 0.80,
        })
        analyzer = ForensicAnalyzer()
        forensics = analyzer.analyze_failures(challenger, champion)
        assert forensics[0].regression_vs_champion is True

    def test_no_failures(self):
        result = _make_result("a", score_by_judge={
            "grounding": 0.90, "safety": 0.95, "quality": 0.80, "relevance": 0.85,
        })
        analyzer = ForensicAnalyzer()
        forensics = analyzer.analyze_failures(result)
        assert len(forensics) == 0


# ═══════════════════════════════════════════════════════════════
# SECTION 6: SliceAnalyzer (Review #8, #12)
# ═══════════════════════════════════════════════════════════════


class TestSliceAnalyzer:
    def test_analyze_by_scenario(self):
        results = {
            "a": _make_result("a", pass_rate=0.9),
            "b": _make_result("b", pass_rate=0.7),
        }
        assignments = {"p1": "clarification", "p2": "prompt_injection", "p3": "clarification"}
        analyzer = SliceAnalyzer()
        slices = analyzer.analyze_by_scenario(results, assignments)
        assert len(slices) == 2
        assert all(s.slice_dimension == "scenario" for s in slices)
        # Model A should win both (higher pass_rate at summary level)
        assert all(s.winner_model_id == "a" for s in slices)

    def test_analyze_by_persona_type(self):
        results = {"a": _make_result("a"), "b": _make_result("b")}
        analyzer = SliceAnalyzer()
        slices = analyzer.analyze_by_persona_type(results)
        assert len(slices) == 3  # standard, edge_case, adversarial
        assert {s.slice_value for s in slices} == {"standard", "edge_case", "adversarial"}

    def test_empty_scenario_assignments(self):
        results = {"a": _make_result("a")}
        analyzer = SliceAnalyzer()
        slices = analyzer.analyze_by_scenario(results, {})
        assert len(slices) == 0


# ═══════════════════════════════════════════════════════════════
# SECTION 7: DiffExplainer (Review #10)
# ═══════════════════════════════════════════════════════════════


class TestDiffExplainer:
    def test_generates_wins_and_losses(self):
        champion = {
            Dimension.GROUNDING: ExtractedDimension(Dimension.GROUNDING, 0.80, EvidenceInfo()),
            Dimension.QUALITY: ExtractedDimension(Dimension.QUALITY, 0.90, EvidenceInfo()),
        }
        challenger = {
            Dimension.GROUNDING: ExtractedDimension(Dimension.GROUNDING, 0.90, EvidenceInfo()),
            Dimension.QUALITY: ExtractedDimension(Dimension.QUALITY, 0.75, EvidenceInfo()),
        }
        explainer = DiffExplainer()
        diff = explainer.explain(champion, challenger, "champ", "chall")
        assert len(diff.wins) == 1
        assert len(diff.losses) == 1
        assert "chall vs champ" in diff.summary

    def test_tied_dimensions(self):
        champion = {
            Dimension.SAFETY: ExtractedDimension(Dimension.SAFETY, 0.95, EvidenceInfo()),
        }
        challenger = {
            Dimension.SAFETY: ExtractedDimension(Dimension.SAFETY, 0.96, EvidenceInfo()),
        }
        explainer = DiffExplainer()
        diff = explainer.explain(champion, challenger, "c", "x")
        assert len(diff.neutral) == 1  # delta < 0.015

    def test_empty_dimensions(self):
        explainer = DiffExplainer()
        diff = explainer.explain({}, {}, "c", "x")
        assert diff.summary == "x vs c: ."


# ═══════════════════════════════════════════════════════════════
# SECTION 8: RecommendationGenerator (Review #9)
# ═══════════════════════════════════════════════════════════════


class TestRecommendationGenerator:
    def test_deployment_recommendation(self):
        from src.multi_compare.models import ModelRanking
        rankings = [ModelRanking(
            model_id="a", model_name="Model A",
            overall_rank=1, overall_score=0.85, deployment_ready=True,
        )]
        gen = RecommendationGenerator()
        recs = gen.generate(rankings, ParityStatus.OK, AnalysisMode.EXPLORATORY)
        assert any("recommended" in r.lower() for r in recs)

    def test_blocked_in_strict_invalid_parity(self):
        """Review #15: Strict mode blocks on invalid parity."""
        from src.multi_compare.models import ModelRanking
        rankings = [ModelRanking(
            model_id="a", model_name="Model A",
            overall_rank=1, overall_score=0.85, deployment_ready=True,
        )]
        gen = RecommendationGenerator()
        recs = gen.generate(rankings, ParityStatus.INVALID, AnalysisMode.STRICT)
        assert any("blocked" in r.lower() for r in recs)

    def test_close_models_tiebreaker_suggestion(self):
        from src.multi_compare.models import ModelRanking
        rankings = [
            ModelRanking(model_id="a", model_name="A", overall_rank=1, overall_score=0.82),
            ModelRanking(model_id="b", model_name="B", overall_rank=2, overall_score=0.80),
        ]
        gen = RecommendationGenerator()
        recs = gen.generate(rankings, ParityStatus.OK, AnalysisMode.EXPLORATORY)
        assert any("close" in r.lower() or "tiebreaker" in r.lower() for r in recs)

    def test_deployment_blockers(self):
        from src.multi_compare.models import ModelRanking
        rankings = [ModelRanking(
            model_id="a", model_name="A",
            overall_rank=1, overall_score=0.85,
            deployment_ready=False,
            deployment_blockers=["Safety score too low"],
        )]
        gen = RecommendationGenerator()
        recs = gen.generate(rankings, ParityStatus.OK, AnalysisMode.EXPLORATORY)
        assert any("blocker" in r.lower() for r in recs)


# ═══════════════════════════════════════════════════════════════
# SECTION 9: NWayComparisonEngine (Integration)
# ═══════════════════════════════════════════════════════════════


class TestNWayComparisonEngine:
    def test_two_model_comparison(self):
        results = {
            "gpt4": _make_result("gpt4", pass_rate=0.90, total_cost=8.0),
            "claude": _make_result("claude", pass_rate=0.85, total_cost=5.0),
        }
        engine = NWayComparisonEngine(AnalysisConfig(champion_id="gpt4"))
        report = engine.analyze(results, total_personas=10, champion_id="gpt4")

        assert len(report.models_compared) == 2
        assert report.comparison_matrix.rankings
        assert report.comparison_matrix.overall_winner is not None
        assert len(report.cost_analysis) == 2
        assert len(report.recommendations) > 0

    def test_three_model_comparison(self):
        results = {
            "gpt4": _make_result("gpt4", pass_rate=0.90, total_cost=8.0),
            "claude": _make_result("claude", pass_rate=0.85, total_cost=5.0),
            "llama": _make_result("llama", pass_rate=0.75, total_cost=0.0),
        }
        engine = NWayComparisonEngine(AnalysisConfig(champion_id="gpt4"))
        report = engine.analyze(results, total_personas=10, champion_id="gpt4")

        assert len(report.comparison_matrix.rankings) == 3
        # GPT-4 should rank highest (best pass rate)
        top = report.comparison_matrix.rankings[0]
        assert top.model_id == "gpt4"

    def test_diff_explanations_generated(self):
        results = {
            "champ": _make_result("champ", pass_rate=0.90, score_by_judge={
                "grounding": 0.85, "safety": 0.95, "quality": 0.80, "relevance": 0.85,
            }),
            "chall": _make_result("chall", pass_rate=0.70, score_by_judge={
                "grounding": 0.60, "safety": 0.98, "quality": 0.90, "relevance": 0.75,
            }),
        }
        engine = NWayComparisonEngine(AnalysisConfig(champion_id="champ"))
        report = engine.analyze(results, total_personas=10, champion_id="champ")

        assert len(report.comparison_matrix.diff_explanations) == 1
        diff = report.comparison_matrix.diff_explanations[0]
        assert diff.vs_champion_id == "champ"
        assert diff.challenger_id == "chall"

    def test_slice_analysis_with_scenarios(self):
        results = {
            "a": _make_result("a", pass_rate=0.90),
            "b": _make_result("b", pass_rate=0.70),
        }
        engine = NWayComparisonEngine()
        report = engine.analyze(
            results, total_personas=10,
            scenario_assignments={"p1": "clarification", "p2": "goal_shift"},
        )
        slices = report.comparison_matrix.slice_analysis
        scenario_slices = [s for s in slices if s.slice_dimension == "scenario"]
        persona_slices = [s for s in slices if s.slice_dimension == "persona_type"]
        assert len(scenario_slices) == 2
        assert len(persona_slices) == 3

    def test_strict_mode_blocks_winner_on_invalid_parity(self):
        """Review #2 + #15: Strict mode + invalid parity → no winner."""
        results = {
            "a": _make_result("a", total_conversations=10),
            "b": _make_result("b", total_conversations=3, passed_conversations=2),
        }
        config = AnalysisConfig(
            mode=AnalysisMode.STRICT,
            parity_invalid_threshold=0.50,
        )
        engine = NWayComparisonEngine(config)
        report = engine.analyze(results, total_personas=10)
        assert report.comparison_matrix.overall_winner is None

    def test_exploratory_mode_allows_winner_on_degraded(self):
        results = {
            "a": _make_result("a", total_conversations=10),
            "b": _make_result("b", total_conversations=7, passed_conversations=5),
        }
        config = AnalysisConfig(mode=AnalysisMode.EXPLORATORY)
        engine = NWayComparisonEngine(config)
        report = engine.analyze(results, total_personas=10)
        assert report.comparison_matrix.overall_winner is not None

    def test_tied_overall_ranks(self):
        """Review #5: Models with same score get same rank."""
        results = {
            "a": _make_result("a", pass_rate=0.80, score_by_judge={
                "grounding": 0.80, "safety": 0.95, "quality": 0.75, "relevance": 0.80,
            }),
            "b": _make_result("b", pass_rate=0.80, score_by_judge={
                "grounding": 0.80, "safety": 0.95, "quality": 0.75, "relevance": 0.80,
            }),
        }
        engine = NWayComparisonEngine()
        report = engine.analyze(results, total_personas=10)
        ranks = [r.overall_rank for r in report.comparison_matrix.rankings]
        assert ranks[0] == ranks[1]  # Tied

    def test_gate_fails_on_safety_floor(self):
        results = {
            "a": _make_result("a", score_by_judge={
                "grounding": 0.80, "safety": 0.50, "quality": 0.80, "relevance": 0.80,
            }),
            "b": _make_result("b"),
        }
        engine = NWayComparisonEngine()
        report = engine.analyze(results, total_personas=10)
        assert report.gate_result.verdict == GateVerdict.FAIL
        assert any("safety" in c.name for c in report.gate_result.checks)

    def test_fewer_than_2_usable_returns_empty_report(self):
        results = {
            "a": _make_result("a"),
            "b": ModelRunResult(model_id="b", model_name="B", status=ModelRunStatus.FAILED),
        }
        engine = NWayComparisonEngine()
        report = engine.analyze(results, total_personas=10)
        assert report.gate_result.verdict == GateVerdict.FAIL
        assert any("Fewer than 2" in r for r in report.recommendations)

    def test_cost_ranks_assigned(self):
        results = {
            "expensive": _make_result("expensive", total_cost=20.0),
            "cheap": _make_result("cheap", total_cost=2.0),
            "mid": _make_result("mid", total_cost=8.0),
        }
        engine = NWayComparisonEngine()
        report = engine.analyze(results, total_personas=10)
        cost_ranks = {c.model_id: c.cost_rank for c in report.cost_analysis}
        assert cost_ranks["cheap"] == 1
        assert cost_ranks["mid"] == 2
        assert cost_ranks["expensive"] == 3

    def test_deployment_blockers_detected(self):
        results = {
            "unsafe": _make_result("unsafe", score_by_judge={
                "grounding": 0.80, "safety": 0.60, "quality": 0.80, "relevance": 0.80,
            }),
            "safe": _make_result("safe"),
        }
        engine = NWayComparisonEngine()
        report = engine.analyze(results, total_personas=10)
        unsafe_ranking = next(r for r in report.comparison_matrix.rankings if r.model_id == "unsafe")
        assert unsafe_ranking.deployment_ready is False
        assert len(unsafe_ranking.deployment_blockers) > 0

    def test_profile_applied(self):
        results = {
            "a": _make_result("a", pass_rate=0.90, total_cost=20.0),
            "b": _make_result("b", pass_rate=0.70, total_cost=2.0),
        }
        # Cost-optimized profile should favor cheaper model
        engine = NWayComparisonEngine(AnalysisConfig(decision_profile_name="cost_optimized"))
        report = engine.analyze(
            results, total_personas=10,
            decision_profile=BUILT_IN_PROFILES["cost_optimized"],
        )
        assert report.comparison_matrix.profile_applied == "cost_optimized"

    def test_forensics_generated_for_failures(self):
        results = {
            "good": _make_result("good"),
            "bad": _make_result("bad", score_by_judge={
                "grounding": 0.30, "safety": 0.95, "quality": 0.80, "relevance": 0.80,
            }),
        }
        engine = NWayComparisonEngine(AnalysisConfig(champion_id="good"))
        report = engine.analyze(results, total_personas=10, champion_id="good")
        assert len(report.forensics) >= 1
        bad_forensic = next(f for f in report.forensics if f.model_id == "bad")
        assert bad_forensic.root_causes[0].bucket == RootCauseBucket.HALLUCINATION


# ═══════════════════════════════════════════════════════════════
# SECTION 10: Utility
# ═══════════════════════════════════════════════════════════════


class TestSafeDiv:
    def test_normal_division(self):
        assert _safe_div(10.0, 4) == 2.5

    def test_zero_denominator(self):
        assert _safe_div(10.0, 0) is None

    def test_none_denominator(self):
        assert _safe_div(10.0, None) is None

    def test_zero_numerator(self):
        assert _safe_div(0, 5) == 0.0
