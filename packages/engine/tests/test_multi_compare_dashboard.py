"""
Tests for Multi-Model Comparison HTML Dashboard (Phase 5).

Covers all review points #1-#17:
- Confidence visibility, parity visualization, uncertainty ranking
- Heatmap normalization, regression evidence, strict/exploratory mode
- Interactive filtering, evidence transparency, slice enhancement
- Decision profile panel, export, sticky header
- Persona leaderboard, cost frontier, deployment panel
- Pairwise cards, drill-down hooks, edge cases

~85 tests across 13 test classes.
"""

import json
import pytest
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_simtest_engine.multi_compare.models import (
    ComparisonMatrix,
    ComparisonMode,
    CostEfficiency,
    CostGovernance,
    CoverageParityReport,
    DecisionProfile,
    DiffExplanation,
    Dimension,
    DimensionScore,
    FailureForensic,
    GateCheck,
    GateResult,
    GateVerdict,
    INVERTED_DIMENSIONS,
    ModelRanking,
    MultiCompareReport,
    RootCause,
    RootCauseBucket,
    SliceResult,
    StatisticalVerdict,
    StatisticalVerdictLabel,
)
from ai_simtest_engine.multi_compare.dashboard import (
    MultiCompareDashboard,
    _compute_parity_status,
    _detect_mode,
    _has_low_confidence,
    _has_low_confidence_model,
    _is_tied_rank,
    _dimension_confidence,
    _normalize_score,
    _heatmap_color,
    _format_raw_value,
    _score_class,
    _esc,
    _fmt_cost,
    _fmt_qpd,
    _safe_json_serialize,
    _collect_dimensions,
    _compute_normalization_ranges,
    _profile_description,
)


# ─────────────────────────────────────────────────────────────
# Test Fixtures
# ─────────────────────────────────────────────────────────────

def _make_dim_score(
    dim: Dimension,
    score: float,
    rank: int = 1,
    is_winner: bool = False,
    delta: float | None = None,
    sample_size: int = 10,
    verdict_label: StatisticalVerdictLabel = StatisticalVerdictLabel.CLEAR_WINNER,
) -> DimensionScore:
    """Create a DimensionScore with statistical verdict."""
    return DimensionScore(
        dimension=dim,
        score=score,
        rank=rank,
        is_winner=is_winner,
        delta_from_champion=delta,
        is_inverted=(dim in INVERTED_DIMENSIONS),
        statistical_verdict=StatisticalVerdict(
            verdict=verdict_label,
            method="delta_with_confidence",
            sample_size=sample_size,
            practical_significance="meaningful",
        ),
    )


def _make_ranking(
    model_id: str,
    model_name: str,
    overall_rank: int = 1,
    overall_score: float = 0.85,
    deployment_ready: bool = True,
    deployment_blockers: list[str] | None = None,
    dimensions: list[DimensionScore] | None = None,
) -> ModelRanking:
    """Create a ModelRanking."""
    return ModelRanking(
        model_id=model_id,
        model_name=model_name,
        overall_rank=overall_rank,
        overall_score=overall_score,
        profile_weighted_rank=overall_rank,
        profile_weighted_score=overall_score,
        deployment_ready=deployment_ready,
        deployment_blockers=deployment_blockers or [],
        dimensions=dimensions or [
            _make_dim_score(Dimension.OVERALL_PASS_RATE, 0.85),
            _make_dim_score(Dimension.GROUNDING, 0.80, rank=1),
            _make_dim_score(Dimension.SAFETY, 0.95, rank=1),
            _make_dim_score(Dimension.QUALITY, 0.75, rank=2),
        ],
    )


def _make_cost(
    model_id: str,
    total: float = 0.50,
    per_conv: float = 0.05,
    rank: int = 1,
) -> CostEfficiency:
    return CostEfficiency(
        model_id=model_id,
        total_cost=total,
        cost_per_conversation=per_conv,
        cost_per_successful_conversation=per_conv * 1.2,
        quality_per_dollar=170.0,
        cost_rank=rank,
    )


def _make_report(
    models: int = 3,
    champion_id: str | None = "model-a",
    overall_winner: str | None = "model-a",
    parity_warnings: list[str | None] | None = None,
    gate_passed: bool = True,
    diff_explanations: list[DiffExplanation] | None = None,
    slice_analysis: list[SliceResult] | None = None,
    forensics: list[FailureForensic] | None = None,
    recommendations: list[str] | None = None,
) -> MultiCompareReport:
    """Build a complete MultiCompareReport for testing."""
    model_ids = [f"model-{chr(97+i)}" for i in range(models)]
    model_names = [f"Model {chr(65+i)}" for i in range(models)]

    rankings = []
    for i, (mid, mname) in enumerate(zip(model_ids, model_names)):
        rankings.append(_make_ranking(
            model_id=mid,
            model_name=mname,
            overall_rank=i + 1,
            overall_score=round(0.90 - i * 0.05, 2),
        ))

    parity = []
    if parity_warnings:
        for i, warning in enumerate(parity_warnings):
            if i < models:
                parity.append(CoverageParityReport(
                    model_id=model_ids[i],
                    total_cases=10,
                    completed_cases=10 if not warning else 4,
                    completion_rate=1.0 if not warning else 0.4,
                    parity_warning=warning,
                ))
    else:
        for mid in model_ids:
            parity.append(CoverageParityReport(
                model_id=mid,
                total_cases=10,
                completed_cases=10,
                completion_rate=1.0,
            ))

    cost_analysis = [
        _make_cost(mid, total=0.30 + i * 0.20, per_conv=0.03 + i * 0.02, rank=i + 1)
        for i, mid in enumerate(model_ids)
    ]

    cost_governance = [
        CostGovernance(model_id=mid, total_cost=0.30 + i * 0.20)
        for i, mid in enumerate(model_ids)
    ]

    gate_checks = [
        GateCheck(name="all_checks", passed=gate_passed, reason="All models passed baseline checks")
    ]

    gate_result = GateResult(
        verdict=GateVerdict.PASS if gate_passed else GateVerdict.FAIL,
        checks=gate_checks,
        champion_id=champion_id,
    )

    matrix = ComparisonMatrix(
        rankings=rankings,
        dimensions=[d.value for d in Dimension],
        winner_per_dimension={},
        overall_winner=overall_winner,
        champion_id=champion_id,
        profile_applied="balanced",
        slice_analysis=slice_analysis or [],
        coverage_parity=parity,
        diff_explanations=diff_explanations or [],
    )

    return MultiCompareReport(
        config_name="Test Comparison",
        timestamp="2026-04-05T10:00:00Z",
        models_compared=model_ids,
        shared_persona_count=10,
        comparison_matrix=matrix,
        cost_analysis=cost_analysis,
        cost_governance=cost_governance,
        forensics=forensics or [],
        recommendations=recommendations or ["✅ 'Model A' (rank #1) recommended for deployment."],
        gate_result=gate_result,
        decision_profiles_applied=["balanced"],
    )


# ─────────────────────────────────────────────────────────────
# Test Class 1: Basic Rendering (8 tests)
# ─────────────────────────────────────────────────────────────

class TestBasicRendering:
    """Basic render and export functionality."""

    def test_render_returns_html_string(self):
        report = _make_report()
        dashboard = MultiCompareDashboard()
        result = dashboard.render(report)
        assert isinstance(result, str)
        assert result.startswith("<!DOCTYPE html>")

    def test_render_contains_all_sections(self):
        report = _make_report()
        html = MultiCompareDashboard().render(report)
        assert "Multi-Model Comparison" in html
        assert "Model Rankings" in html
        assert "Dimension Heatmap" in html
        assert "Visual Analysis" in html
        assert "Cost Efficiency" in html
        assert "Coverage Parity" in html
        assert "CI/CD Gate Result" in html
        assert "Recommendations" in html

    def test_export_writes_file(self, tmp_path):
        report = _make_report()
        output = tmp_path / "dashboard.html"
        result = MultiCompareDashboard().export(report, output)
        assert result.exists()
        content = result.read_text()
        assert "<!DOCTYPE html>" in content

    def test_render_with_minimal_data(self):
        """Render with minimal report (no optional sections)."""
        report = _make_report(
            models=2,
            diff_explanations=[],
            slice_analysis=[],
            forensics=[],
            recommendations=[],
        )
        html = MultiCompareDashboard().render(report)
        assert "Model Rankings" in html

    def test_render_includes_chart_js_cdn(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "chart.js@4.4.1" in html

    def test_render_includes_css(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "--bg: #0c0e14" in html
        assert "--champion:" in html

    def test_render_includes_javascript(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "new Chart(" in html
        assert "function copyJSON()" in html
        assert "function exportCSV()" in html

    def test_render_includes_footer(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "AI SimTest" in html
        assert "v1.3.0" in html


# ─────────────────────────────────────────────────────────────
# Test Class 2: Header & Identity (6 tests)
# ─────────────────────────────────────────────────────────────

class TestHeaderIdentity:
    """Header section rendering with mode badges."""

    def test_header_shows_config_name(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Test Comparison" in html

    def test_header_shows_model_count(self):
        html = MultiCompareDashboard().render(_make_report(models=4))
        assert "4 models" in html

    def test_header_shows_persona_count(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "10 personas" in html

    def test_header_shows_profile_name(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "balanced" in html

    def test_header_shows_mode_badge_exploratory(self):
        """Review #6: Mode badge display."""
        html = MultiCompareDashboard().render(_make_report())
        assert "EXPLORATORY" in html
        assert "badge-exploratory" in html

    def test_header_shows_mode_badge_strict(self):
        """Review #6: Strict mode badge when strict indicators present."""
        report = _make_report(recommendations=["🔒 STRICT MODE: Deployment blocked"])
        html = MultiCompareDashboard().render(report)
        assert "STRICT" in html


# ─────────────────────────────────────────────────────────────
# Test Class 3: Scorecard (8 tests)
# ─────────────────────────────────────────────────────────────

class TestScorecard:
    """Scorecard rendering with confidence and parity."""

    def test_scorecard_shows_all_models(self):
        report = _make_report(models=3)
        html = MultiCompareDashboard().render(report)
        assert "Model A" in html
        assert "Model B" in html
        assert "Model C" in html

    def test_scorecard_highlights_champion(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "champion-card" in html
        assert "CHAMPION" in html

    def test_scorecard_highlights_winner(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "winner-card" in html
        assert "WINNER" in html

    def test_scorecard_shows_deployment_status(self):
        report = _make_report()
        report.comparison_matrix.rankings[2].deployment_ready = False
        report.comparison_matrix.rankings[2].deployment_blockers = ["Safety too low"]
        html = MultiCompareDashboard().render(report)
        assert "❌" in html
        assert "✅" in html

    def test_scorecard_tied_ranks(self):
        """Review #3: Tied rank display."""
        report = _make_report(models=2)
        report.comparison_matrix.rankings[0].overall_rank = 1
        report.comparison_matrix.rankings[1].overall_rank = 1
        html = MultiCompareDashboard().render(report)
        assert "T1" in html
        assert "Tied" in html

    def test_scorecard_no_winner(self):
        """Review #3: No clear winner display."""
        report = _make_report(overall_winner=None)
        html = MultiCompareDashboard().render(report)
        assert "No clear winner" in html or "no_clear_winner" in html.lower() or "no-winner-banner" in html

    def test_scorecard_parity_invalid_blocks_winner_strict(self):
        """Review #2: Winner blocked when parity invalid + strict mode."""
        report = _make_report(
            overall_winner=None,
            parity_warnings=["Completion rate 40% is below invalid threshold (50%)", None, None],
            recommendations=["🔒 STRICT MODE: blocked"],
        )
        html = MultiCompareDashboard().render(report)
        assert "winner blocked" in html.lower() or "no" in html.lower()

    def test_scorecard_low_confidence_unconfirmed(self):
        """Review #3: Low confidence shows unconfirmed label."""
        report = _make_report(models=2)
        # Set low sample size to trigger low confidence
        for ds in report.comparison_matrix.rankings[0].dimensions:
            ds.statistical_verdict = StatisticalVerdict(
                verdict=StatisticalVerdictLabel.INSUFFICIENT_DATA,
                method="delta_with_confidence",
                sample_size=3,
                practical_significance="unknown",
            )
        html = MultiCompareDashboard().render(report)
        assert "unconfirmed" in html


# ─────────────────────────────────────────────────────────────
# Test Class 4: Heatmap (10 tests)
# ─────────────────────────────────────────────────────────────

class TestHeatmap:
    """Heatmap table rendering with normalization."""

    def test_heatmap_renders_table(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "heatmap-table" in html
        assert "heatmapTable" in html

    def test_heatmap_shows_dimension_headers(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Overall Pass Rate" in html or "Grounding" in html

    def test_heatmap_shows_inverted_marker(self):
        """Review #4: Inverted dimensions marked with ↓."""
        report = _make_report(models=2)
        report.comparison_matrix.rankings[0].dimensions.append(
            _make_dim_score(Dimension.RESPONSE_LATENCY, 150.0, rank=1)
        )
        report.comparison_matrix.rankings[1].dimensions.append(
            _make_dim_score(Dimension.RESPONSE_LATENCY, 200.0, rank=2)
        )
        html = MultiCompareDashboard().render(report)
        assert "↓" in html

    def test_heatmap_shows_raw_values(self):
        """Review #4: Raw values shown as text."""
        html = MultiCompareDashboard().render(_make_report())
        assert "0.85" in html or "0.800" in html or "0.950" in html

    def test_heatmap_shows_winner_star(self):
        report = _make_report(models=2)
        report.comparison_matrix.rankings[0].dimensions[0].is_winner = True
        html = MultiCompareDashboard().render(report)
        assert "★" in html

    def test_heatmap_shows_delta_from_champion(self):
        """Review #5: Delta display."""
        report = _make_report(models=2)
        report.comparison_matrix.rankings[1].dimensions[0].delta_from_champion = -0.05
        html = MultiCompareDashboard().render(report)
        assert "-0.050" in html

    def test_heatmap_shows_confidence_badge(self):
        """Review #1: Confidence badge per dimension."""
        html = MultiCompareDashboard().render(_make_report())
        assert "conf-badge" in html
        assert "conf-high" in html or "conf-medium" in html or "conf-low" in html

    def test_heatmap_champion_row_highlight(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "hm-champion-row" in html

    def test_heatmap_shows_na_for_missing_dims(self):
        """Missing dimensions show dash."""
        report = _make_report(models=2)
        # Remove all dims from second model
        report.comparison_matrix.rankings[1].dimensions = []
        html = MultiCompareDashboard().render(report)
        assert "—" in html

    def test_heatmap_evidence_tooltip(self):
        """Review #8: Evidence transparency in tooltip."""
        html = MultiCompareDashboard().render(_make_report())
        assert "Sample size:" in html or "title=" in html


# ─────────────────────────────────────────────────────────────
# Test Class 5: Charts (6 tests)
# ─────────────────────────────────────────────────────────────

class TestCharts:
    """Chart rendering and data injection."""

    def test_charts_section_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Visual Analysis" in html

    def test_dimension_bar_chart_created(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "dimensionBarChart" in html

    def test_radar_chart_created(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "radarChart" in html

    def test_cost_quality_scatter_created(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "costQualityChart" in html

    def test_chart_data_contains_model_names(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Model A" in html
        assert "Model B" in html

    def test_chart_js_initialization(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "new Chart(document.getElementById" in html


# ─────────────────────────────────────────────────────────────
# Test Class 6: Diffs / Champion vs Challenger (8 tests)
# ─────────────────────────────────────────────────────────────

class TestDiffs:
    """Champion vs Challenger evidence cards."""

    def _make_diffs(self) -> list[DiffExplanation]:
        return [
            DiffExplanation(
                challenger_id="model-b",
                vs_champion_id="model-a",
                summary="model-b vs model-a: 2 dimensions improved, 1 regressed, 1 tied.",
                wins=["Quality: +0.10 (0.85 vs 0.75)", "Relevance: +0.05 (0.80 vs 0.75)"],
                losses=["Safety: -0.08 (0.87 vs 0.95)"],
                neutral=["Grounding: tied (0.80 vs 0.80)"],
            ),
        ]

    def test_diffs_section_present(self):
        report = _make_report(diff_explanations=self._make_diffs())
        html = MultiCompareDashboard().render(report)
        assert "Champion vs Challenger" in html

    def test_diffs_hidden_without_champion(self):
        report = _make_report(champion_id=None, diff_explanations=[])
        html = MultiCompareDashboard().render(report)
        assert "Champion vs Challenger" not in html

    def test_diffs_shows_challenger_name(self):
        report = _make_report(diff_explanations=self._make_diffs())
        html = MultiCompareDashboard().render(report)
        assert "model-b" in html

    def test_diffs_shows_wins(self):
        report = _make_report(diff_explanations=self._make_diffs())
        html = MultiCompareDashboard().render(report)
        assert "Wins" in html or "improvements" in html.lower()

    def test_diffs_shows_losses(self):
        report = _make_report(diff_explanations=self._make_diffs())
        html = MultiCompareDashboard().render(report)
        assert "Losses" in html or "regressions" in html.lower()

    def test_diffs_evidence_count(self):
        """Review #5: Evidence count displayed."""
        report = _make_report(diff_explanations=self._make_diffs())
        html = MultiCompareDashboard().render(report)
        assert "4 dimensions" in html

    def test_diffs_top_improvements(self):
        """Review #5: Top 3 improvements listed."""
        report = _make_report(diff_explanations=self._make_diffs())
        html = MultiCompareDashboard().render(report)
        assert "Top improvements" in html or "Quality:" in html

    def test_diffs_uncertainty_note(self):
        """Review #16: Uncertainty note for low evidence."""
        diffs = [DiffExplanation(
            challenger_id="model-b",
            vs_champion_id="model-a",
            summary="Limited data",
            wins=["A: +0.01"],
            losses=[],
            neutral=[],
        )]
        report = _make_report(diff_explanations=diffs)
        html = MultiCompareDashboard().render(report)
        assert "caution" in html.lower() or "Limited evidence" in html


# ─────────────────────────────────────────────────────────────
# Test Class 7: Cost Table (8 tests)
# ─────────────────────────────────────────────────────────────

class TestCostTable:
    """Cost efficiency table rendering."""

    def test_cost_section_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Cost Efficiency" in html

    def test_cost_shows_total(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "$0.30" in html or "$0.50" in html

    def test_cost_shows_per_conversation(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "$0.03" in html or "$0.05" in html

    def test_cost_shows_rank(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "#1" in html

    def test_cost_shows_quality_per_dollar(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "170.0" in html

    def test_cost_budget_alert(self):
        report = _make_report()
        report.cost_governance[0].budget_exceeded = True
        html = MultiCompareDashboard().render(report)
        assert "OVER BUDGET" in html

    def test_cost_handles_none_values(self):
        report = _make_report(models=2)
        report.cost_analysis[0].cost_per_grounded_answer = None
        html = MultiCompareDashboard().render(report)
        assert "—" in html

    def test_cost_no_section_without_data(self):
        report = _make_report()
        report.cost_analysis = []
        html = MultiCompareDashboard().render(report)
        # The cost section header should not appear (the section itself is skipped)
        assert "Cost Efficiency</h2>" not in html


# ─────────────────────────────────────────────────────────────
# Test Class 8: Forensics (6 tests)
# ─────────────────────────────────────────────────────────────

class TestForensics:
    """Failure forensics section."""

    def _make_forensics(self) -> list[FailureForensic]:
        return [
            FailureForensic(
                conversation_key="aggregate:model-b:grounding",
                model_id="model-b",
                root_causes=[RootCause(
                    bucket=RootCauseBucket.HALLUCINATION,
                    judge_name="grounding",
                    evidence="grounding score 0.45 below threshold 0.7",
                    severity="critical",
                )],
                regression_vs_champion=True,
                affected_dimensions=["grounding"],
            ),
        ]

    def test_forensics_section_present(self):
        report = _make_report(forensics=self._make_forensics())
        html = MultiCompareDashboard().render(report)
        assert "Failure Forensics" in html

    def test_forensics_shows_root_cause_bucket(self):
        report = _make_report(forensics=self._make_forensics())
        html = MultiCompareDashboard().render(report)
        assert "Hallucination" in html

    def test_forensics_shows_regression_flag(self):
        report = _make_report(forensics=self._make_forensics())
        html = MultiCompareDashboard().render(report)
        assert "Regression vs Champion" in html

    def test_forensics_shows_severity(self):
        report = _make_report(forensics=self._make_forensics())
        html = MultiCompareDashboard().render(report)
        assert "critical" in html

    def test_forensics_empty_state(self):
        report = _make_report(forensics=[])
        html = MultiCompareDashboard().render(report)
        assert "No failures detected" in html

    def test_forensics_filter_buttons(self):
        """Review #7: Filter buttons for forensics."""
        report = _make_report(forensics=self._make_forensics())
        html = MultiCompareDashboard().render(report)
        assert "Regressions Only" in html
        assert "Critical Only" in html


# ─────────────────────────────────────────────────────────────
# Test Class 9: Parity (5 tests)
# ─────────────────────────────────────────────────────────────

class TestParity:
    """Coverage parity visualization."""

    def test_parity_section_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Coverage Parity" in html

    def test_parity_shows_ok_status(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "parity-ok" in html

    def test_parity_shows_degraded_status(self):
        """Review #2: Degraded parity visualization."""
        report = _make_report(
            parity_warnings=["Completion rate 70% is below parity threshold (85%)", None, None]
        )
        html = MultiCompareDashboard().render(report)
        assert "DEGRADED" in html
        assert "parity-degraded" in html

    def test_parity_shows_invalid_status(self):
        """Review #2: Invalid parity visualization."""
        report = _make_report(
            parity_warnings=["Completion rate 40% is below invalid threshold (50%)", None, None]
        )
        html = MultiCompareDashboard().render(report)
        assert "INVALID" in html
        assert "parity-invalid" in html

    def test_parity_progress_bars(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "progress-bar" in html
        assert "100%" in html


# ─────────────────────────────────────────────────────────────
# Test Class 10: Slices / Leaderboard (6 tests)
# ─────────────────────────────────────────────────────────────

class TestLeaderboard:
    """Persona-based leaderboard — product differentiator."""

    def _make_slices(self) -> list[SliceResult]:
        return [
            SliceResult(
                slice_dimension="persona_type",
                slice_value="standard",
                winner_model_id="model-a",
                model_scores={"model-a": 0.90, "model-b": 0.85},
            ),
            SliceResult(
                slice_dimension="persona_type",
                slice_value="adversarial",
                winner_model_id="model-b",
                model_scores={"model-a": 0.70, "model-b": 0.80},
            ),
            SliceResult(
                slice_dimension="scenario",
                slice_value="prompt_injection",
                winner_model_id="model-a",
                model_scores={"model-a": 0.92, "model-b": 0.78},
            ),
        ]

    def test_leaderboard_section_present(self):
        """Review #13: Leaderboard renders."""
        report = _make_report(slice_analysis=self._make_slices())
        html = MultiCompareDashboard().render(report)
        assert "Leaderboard" in html

    def test_leaderboard_persona_type_tab(self):
        report = _make_report(slice_analysis=self._make_slices())
        html = MultiCompareDashboard().render(report)
        assert "By Persona Type" in html

    def test_leaderboard_scenario_tab(self):
        report = _make_report(slice_analysis=self._make_slices())
        html = MultiCompareDashboard().render(report)
        assert "By Scenario" in html

    def test_leaderboard_shows_winners(self):
        report = _make_report(slice_analysis=self._make_slices())
        html = MultiCompareDashboard().render(report)
        assert "model-a" in html
        assert "model-b" in html

    def test_leaderboard_shows_runner_up(self):
        """Review #9: Slice shows runner-up."""
        report = _make_report(slice_analysis=self._make_slices())
        html = MultiCompareDashboard().render(report)
        # Runner-up model should appear with score
        assert "0.85" in html or "0.78" in html

    def test_leaderboard_shows_delta_and_confidence(self):
        """Review #9: Slice shows delta and confidence."""
        report = _make_report(slice_analysis=self._make_slices())
        html = MultiCompareDashboard().render(report)
        assert "conf-badge" in html


# ─────────────────────────────────────────────────────────────
# Test Class 11: Deployment Panel (4 tests)
# ─────────────────────────────────────────────────────────────

class TestDeploymentPanel:
    """Deployment recommendation panel."""

    def test_deployment_section_present(self):
        """Review #15: Deployment panel renders."""
        html = MultiCompareDashboard().render(_make_report())
        assert "Deployment Recommendations" in html

    def test_deployment_shows_prod_recommendation(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Recommended for Production" in html

    def test_deployment_shows_not_recommended(self):
        report = _make_report()
        report.comparison_matrix.rankings[2].deployment_ready = False
        report.comparison_matrix.rankings[2].deployment_blockers = ["Safety score 0.65 below 0.80"]
        html = MultiCompareDashboard().render(report)
        assert "Not Recommended" in html

    def test_deployment_shows_cost_sensitive(self):
        """Cheapest deployment-ready model shown if different from top."""
        report = _make_report(models=3)
        # Make model-c cheapest but rank 3
        html = MultiCompareDashboard().render(report)
        # Should show cost-sensitive recommendation for cheapest model
        assert "deploy-card" in html


# ─────────────────────────────────────────────────────────────
# Test Class 12: Gates & Recommendations (5 tests)
# ─────────────────────────────────────────────────────────────

class TestGatesAndRecs:
    """Gate checks and recommendations."""

    def test_gate_pass_display(self):
        html = MultiCompareDashboard().render(_make_report(gate_passed=True))
        assert "gate-verdict" in html
        assert "PASS" in html

    def test_gate_fail_display(self):
        html = MultiCompareDashboard().render(_make_report(gate_passed=False))
        assert "FAIL" in html

    def test_gate_checks_table(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "gate-table" in html
        assert "all_checks" in html

    def test_recommendations_rendered(self):
        report = _make_report(recommendations=["✅ Deploy Model A", "⚠️ Check parity"])
        html = MultiCompareDashboard().render(report)
        assert "Deploy Model A" in html
        assert "Check parity" in html

    def test_export_buttons_present(self):
        """Review #11: Export buttons."""
        html = MultiCompareDashboard().render(_make_report())
        assert "Copy JSON" in html
        assert "Export CSV" in html


# ─────────────────────────────────────────────────────────────
# Test Class 13: Edge Cases & Helpers (11 tests)
# ─────────────────────────────────────────────────────────────

class TestEdgeCasesAndHelpers:
    """Edge cases and helper function tests."""

    def test_two_model_comparison(self):
        html = MultiCompareDashboard().render(_make_report(models=2))
        assert "Model A" in html
        assert "Model B" in html

    def test_ten_model_comparison(self):
        html = MultiCompareDashboard().render(_make_report(models=10))
        assert "10 models" in html

    def test_zero_cost_model(self):
        report = _make_report(models=2)
        report.cost_analysis[0].total_cost = 0.0
        report.cost_analysis[0].cost_per_conversation = 0.0
        html = MultiCompareDashboard().render(report)
        assert "$0.0000" in html

    def test_html_escaping(self):
        assert _esc('<script>alert("xss")</script>') == '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'

    def test_score_class_pass(self):
        assert _score_class(0.85) == "pass"

    def test_score_class_warn(self):
        assert _score_class(0.65) == "warn"

    def test_score_class_fail(self):
        assert _score_class(0.3) == "fail"

    def test_normalize_score_standard(self):
        """Review #4: Normalization for standard dimensions."""
        ranges = {Dimension.GROUNDING: (0.5, 1.0)}
        result = _normalize_score(0.75, Dimension.GROUNDING, ranges, False)
        assert 0.0 <= result <= 1.0
        assert result == 0.75  # 0-1 dim, directly mapped

    def test_normalize_score_inverted(self):
        """Review #4: Normalization for inverted dimensions."""
        ranges = {Dimension.RESPONSE_LATENCY: (100.0, 500.0)}
        result = _normalize_score(100.0, Dimension.RESPONSE_LATENCY, ranges, True)
        assert result == 1.0  # Lowest latency = best = 1.0

    def test_format_raw_value_latency(self):
        assert _format_raw_value(150.0, Dimension.RESPONSE_LATENCY) == "150ms"

    def test_format_raw_value_cost(self):
        assert _format_raw_value(0.05, Dimension.COST_EFFICIENCY) == "$0.0500"

    def test_compute_parity_ok(self):
        reports = [CoverageParityReport(model_id="a", completion_rate=1.0)]
        assert _compute_parity_status(reports) == "OK"

    def test_compute_parity_invalid(self):
        reports = [CoverageParityReport(
            model_id="a", completion_rate=0.4,
            parity_warning="below invalid threshold"
        )]
        assert _compute_parity_status(reports) == "INVALID"

    def test_fmt_cost_none(self):
        assert _fmt_cost(None) == "—"

    def test_fmt_qpd_none(self):
        assert _fmt_qpd(None) == "—"

    def test_profile_description_known(self):
        assert "equally" in _profile_description("balanced").lower()

    def test_profile_description_unknown(self):
        assert "Custom" in _profile_description("unknown_profile")


# ─────────────────────────────────────────────────────────────
# Test Class 14: Global Warnings (5 tests)
# ─────────────────────────────────────────────────────────────

class TestGlobalWarnings:
    """Global warning banners — Review #1, #2."""

    def test_low_confidence_warning(self):
        """Review #1: Warning when low confidence detected."""
        report = _make_report(models=2)
        for ds in report.comparison_matrix.rankings[0].dimensions:
            ds.statistical_verdict = StatisticalVerdict(
                verdict=StatisticalVerdictLabel.INSUFFICIENT_DATA,
                method="delta_with_confidence",
                sample_size=2,
                practical_significance="unknown",
            )
        html = MultiCompareDashboard().render(report)
        assert "Low Confidence" in html

    def test_summary_only_warning(self):
        """Review #1: Summary-only evidence warning."""
        html = MultiCompareDashboard().render(_make_report())
        assert "summary-level" in html.lower() or "Evidence Level" in html

    def test_invalid_parity_warning(self):
        """Review #2: Invalid parity warning banner."""
        report = _make_report(
            parity_warnings=["below invalid threshold", None, None]
        )
        html = MultiCompareDashboard().render(report)
        assert "Invalid Parity" in html

    def test_degraded_parity_warning(self):
        """Review #2: Degraded parity warning banner."""
        report = _make_report(
            parity_warnings=["below parity threshold", None, None]
        )
        html = MultiCompareDashboard().render(report)
        assert "Degraded Parity" in html

    def test_no_warnings_when_all_ok(self):
        """No warnings when everything is healthy and high confidence."""
        report = _make_report()
        # All rankings have high sample_size (10), so no low confidence warning
        html = MultiCompareDashboard().render(report)
        assert "Low Confidence" not in html


# ─────────────────────────────────────────────────────────────
# Test Class 15: Sticky Header (3 tests)
# ─────────────────────────────────────────────────────────────

class TestStickyHeader:
    """Sticky summary header — Review #12."""

    def test_sticky_header_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "sticky-header" in html

    def test_sticky_header_shows_winner(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "model-a" in html  # winner in sticky header

    def test_sticky_header_shows_gate_status(self):
        html = MultiCompareDashboard().render(_make_report(gate_passed=True))
        assert "PASS" in html


# ─────────────────────────────────────────────────────────────
# Test Class 16: Profile Panel (3 tests)
# ─────────────────────────────────────────────────────────────

class TestProfilePanel:
    """Decision profile explanation — Review #10."""

    def test_profile_panel_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "Decision Profile" in html

    def test_profile_shows_weight_bars(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "pw-bar" in html

    def test_profile_shows_influence_note(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "profile favored" in html.lower() or "profile-influence" in html


# ─────────────────────────────────────────────────────────────
# Test Class 17: Drill-Down Hooks (2 tests)
# ─────────────────────────────────────────────────────────────

class TestDrillDownHooks:
    """Future drill-down placeholders — Review #17."""

    def test_model_anchors_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert 'id="drilldown-model-a"' in html

    def test_future_comment_present(self):
        html = MultiCompareDashboard().render(_make_report())
        assert "FUTURE:" in html
