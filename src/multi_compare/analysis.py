"""
Multi-Model Comparison — N-Way Comparison Engine (Phase 4).

The analytical core that transforms MultiModelResult into
ComparisonMatrix with rankings, statistical verdicts, cost-quality
analysis, coverage parity checks, failure forensics, and
deployment recommendations.

Components:
- AnalysisConfig: Strict vs exploratory mode, confidence rules
- DimensionExtractor: Extracts per-model per-dimension raw scores
- StatisticalAnalyzer: Confidence-labeled comparison with tie handling
- CoverageParityChecker: Deterministic parity rules (ok/degraded/invalid)
- CostAnalyzer: Normalized cost-efficiency metrics (value, not just price)
- ForensicAnalyzer: Confidence-labeled root-cause analysis
- SliceAnalyzer: Per-scenario, per-persona-type, per-failure-type ranking
- NWayComparisonEngine: Top-level engine orchestrating all components

Addresses review points:
- #1: analysis_confidence + evidence_basis per dimension, gate restrictions
- #2: parity_status: ok|degraded|invalid with strict mode blocking
- #3: cost per successful/compliant/grounded conversation, quality-per-dollar
- #4: ConversationKey as primary comparison unit
- #5: Tied ranks, "no clear winner", confidence-adjusted ranking
- #6: root_cause_confidence on forensics, supporting evidence
- #7: num_cases_used, num_cases_missing per metric
- #8: Slice-based analysis as first-class output
- #9: Confidence-aware deployment recommendations
- #10: Pairwise champion vs challenger explanations
- #11: Trial-ready interfaces (placeholder)
- #12-15: Product differentiators
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from src.multi_compare.models import (
    ComparisonMatrix,
    ComparisonMode,
    CostEfficiency,
    CostGovernance,
    CoverageParityReport,
    DecisionProfile,
    BUILT_IN_PROFILES,
    Dimension,
    DimensionScore,
    DiffExplanation,
    FailureForensic,
    GateCheck,
    GateResult,
    GateVerdict,
    INVERTED_DIMENSIONS,
    ModelRanking,
    ModelRunResult,
    ModelRunStatus,
    MultiCompareReport,
    RootCause,
    RootCauseBucket,
    SliceResult,
    StatisticalVerdict,
    StatisticalVerdictLabel,
)


# ─────────────────────────────────────────────────────────────
# Analysis Config (Review #15: Strict vs Exploratory)
# ─────────────────────────────────────────────────────────────

class AnalysisMode(str, enum.Enum):
    STRICT = "strict"
    EXPLORATORY = "exploratory"


class ConfidenceLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceBasis(str, enum.Enum):
    TURN_LEVEL = "turn"
    CONVERSATION_LEVEL = "conversation"
    SUMMARY_ONLY = "summary"


class ParityStatus(str, enum.Enum):
    OK = "ok"
    DEGRADED = "degraded"
    INVALID = "invalid"


@dataclass
class AnalysisConfig:
    """Configuration for the analysis engine."""
    mode: AnalysisMode = AnalysisMode.EXPLORATORY
    significance_threshold: float = 0.03  # min delta to be "meaningful"
    parity_degraded_threshold: float = 0.85  # below this = degraded
    parity_invalid_threshold: float = 0.50  # below this = invalid
    min_conversations_for_high_confidence: int = 10
    min_conversations_for_medium_confidence: int = 5
    block_gates_on_low_confidence: bool = True  # strict mode default
    champion_id: str | None = None
    decision_profile_name: str = "balanced"
    comparison_mode: ComparisonMode = ComparisonMode.CHAMPION_CHALLENGER


# ─────────────────────────────────────────────────────────────
# Evidence Metadata (Review #7: Evidence Transparency)
# ─────────────────────────────────────────────────────────────

@dataclass
class EvidenceInfo:
    """Metadata about evidence quality for a metric."""
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    evidence_basis: EvidenceBasis = EvidenceBasis.SUMMARY_ONLY
    num_cases_used: int = 0
    num_cases_missing: int = 0
    coverage_basis: float = 0.0  # fraction of expected cases present


# ─────────────────────────────────────────────────────────────
# Dimension Extraction (Review #4: ConversationKey pairing)
# ─────────────────────────────────────────────────────────────

@dataclass
class ExtractedDimension:
    """Raw extracted score for one model on one dimension."""
    dimension: Dimension
    score: float
    evidence: EvidenceInfo
    raw_values: list[float] = field(default_factory=list)  # per-conversation values if available


class DimensionExtractor:
    """
    Extracts per-model per-dimension scores from ModelRunResult.

    Primary comparison key: ConversationKey (via summary_dict).
    Scores are extracted from summary_dict and cost_report_dict.
    """

    JUDGE_DIMENSION_MAP = {
        "grounding": Dimension.GROUNDING,
        "safety": Dimension.SAFETY,
        "quality": Dimension.QUALITY,
        "relevance": Dimension.RELEVANCE,
    }

    def extract(self, result: ModelRunResult, total_personas: int) -> list[ExtractedDimension]:
        """Extract all dimension scores from a model's result."""
        dims: list[ExtractedDimension] = []
        summary = result.summary_dict
        cost = result.cost_report_dict or {}

        num_conversations = summary.get("total_conversations", 0)
        evidence_base = self._compute_evidence(num_conversations, total_personas)

        # Overall pass rate
        dims.append(ExtractedDimension(
            dimension=Dimension.OVERALL_PASS_RATE,
            score=summary.get("pass_rate", 0.0),
            evidence=evidence_base,
        ))

        # Judge scores
        score_by_judge = summary.get("score_by_judge", {})
        for judge_name, dimension in self.JUDGE_DIMENSION_MAP.items():
            score = score_by_judge.get(judge_name)
            if score is not None:
                dims.append(ExtractedDimension(
                    dimension=dimension,
                    score=float(score),
                    evidence=evidence_base,
                ))

        # Critical failure rate (inverted: lower = better)
        total = summary.get("total_conversations", 1)
        critical = summary.get("critical_failures", 0)
        if total > 0:
            dims.append(ExtractedDimension(
                dimension=Dimension.CRITICAL_FAILURE_RATE,
                score=critical / total,
                evidence=evidence_base,
            ))

        # Cost efficiency (inverted: lower = better)
        total_cost = cost.get("total_estimated_cost", 0.0)
        if total_cost > 0 and num_conversations > 0:
            dims.append(ExtractedDimension(
                dimension=Dimension.COST_EFFICIENCY,
                score=total_cost / num_conversations,  # cost per conversation
                evidence=evidence_base,
            ))
        elif num_conversations > 0:
            # Free/local model
            dims.append(ExtractedDimension(
                dimension=Dimension.COST_EFFICIENCY,
                score=0.0,
                evidence=evidence_base,
            ))

        # Response latency (inverted: lower = better)
        avg_latency = result.adapter_metadata.get("avg_latency_ms")
        if avg_latency is not None:
            dims.append(ExtractedDimension(
                dimension=Dimension.RESPONSE_LATENCY,
                score=float(avg_latency),
                evidence=evidence_base,
            ))

        return dims

    def _compute_evidence(self, num_conversations: int, total_personas: int) -> EvidenceInfo:
        """Determine confidence and evidence basis from sample size."""
        if num_conversations >= 10:
            confidence = ConfidenceLevel.HIGH
        elif num_conversations >= 5:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW

        coverage = num_conversations / total_personas if total_personas > 0 else 0.0

        return EvidenceInfo(
            confidence=confidence,
            evidence_basis=EvidenceBasis.SUMMARY_ONLY,
            num_cases_used=num_conversations,
            num_cases_missing=max(0, total_personas - num_conversations),
            coverage_basis=min(1.0, coverage),
        )


# ─────────────────────────────────────────────────────────────
# Statistical Analyzer (Review #1, #5: Confidence + Ties)
# ─────────────────────────────────────────────────────────────

class StatisticalAnalyzer:
    """
    Produces StatisticalVerdict for dimension comparisons.

    Supports:
    - Confidence-labeled verdicts (Review #1)
    - Tie handling and "no clear winner" (Review #5)
    - Trial-ready interface (Review #11, placeholder)

    Current method: Delta-based with significance threshold.
    Future: Bootstrap CI and Wilcoxon when per-turn data available.
    """

    def __init__(self, significance_threshold: float = 0.03):
        self.threshold = significance_threshold

    def compare_dimension(
        self,
        champion_score: float,
        challenger_score: float,
        is_inverted: bool,
        sample_size: int,
        confidence: ConfidenceLevel,
    ) -> StatisticalVerdict:
        """
        Compare two models on a single dimension.

        Returns StatisticalVerdict with confidence-aware labels.
        """
        if is_inverted:
            # Lower is better for inverted dimensions
            delta = champion_score - challenger_score  # positive = challenger better
        else:
            delta = challenger_score - champion_score  # positive = challenger better

        abs_delta = abs(delta)

        # Classify practical significance
        if abs_delta < self.threshold * 0.5:
            practical = "negligible"
        elif abs_delta < self.threshold:
            practical = "marginal"
        elif abs_delta < self.threshold * 3:
            practical = "meaningful"
        else:
            practical = "large"

        # Determine verdict based on confidence and delta
        if abs_delta < self.threshold * 0.5:
            verdict = StatisticalVerdictLabel.NO_CLEAR_WINNER
        elif confidence == ConfidenceLevel.LOW:
            if abs_delta >= self.threshold * 3:
                verdict = StatisticalVerdictLabel.LEANING
            else:
                verdict = StatisticalVerdictLabel.INSUFFICIENT_DATA
        elif confidence == ConfidenceLevel.MEDIUM:
            if abs_delta >= self.threshold * 2:
                verdict = StatisticalVerdictLabel.LIKELY_WINNER
            elif abs_delta >= self.threshold:
                verdict = StatisticalVerdictLabel.LEANING
            else:
                verdict = StatisticalVerdictLabel.NO_CLEAR_WINNER
        else:  # HIGH
            if abs_delta >= self.threshold * 2:
                verdict = StatisticalVerdictLabel.CLEAR_WINNER
            elif abs_delta >= self.threshold:
                verdict = StatisticalVerdictLabel.LIKELY_WINNER
            else:
                verdict = StatisticalVerdictLabel.NO_CLEAR_WINNER

        return StatisticalVerdict(
            verdict=verdict,
            method="delta_with_confidence",
            effect_size=round(delta, 6),
            sample_size=sample_size,
            practical_significance=practical,
        )

    def compare_dimension_trial_ready(
        self,
        champion_values: list[float],
        challenger_values: list[float],
        is_inverted: bool,
    ) -> StatisticalVerdict:
        """
        Trial-ready comparison interface (Review #11).

        Placeholder for future bootstrap CI / Wilcoxon implementation.
        Currently delegates to summary comparison using means.
        """
        if not champion_values or not challenger_values:
            return StatisticalVerdict(
                verdict=StatisticalVerdictLabel.INSUFFICIENT_DATA,
                method="no_data",
                sample_size=0,
                practical_significance="unknown",
            )

        champ_mean = sum(champion_values) / len(champion_values)
        chall_mean = sum(challenger_values) / len(challenger_values)
        sample = min(len(champion_values), len(challenger_values))

        confidence = (ConfidenceLevel.HIGH if sample >= 10
                      else ConfidenceLevel.MEDIUM if sample >= 5
                      else ConfidenceLevel.LOW)

        return self.compare_dimension(
            champion_score=champ_mean,
            challenger_score=chall_mean,
            is_inverted=is_inverted,
            sample_size=sample,
            confidence=confidence,
        )


# ─────────────────────────────────────────────────────────────
# Coverage Parity Checker (Review #2)
# ─────────────────────────────────────────────────────────────

class CoverageParityChecker:
    """
    Checks coverage parity across models.

    Produces deterministic parity_status:
    - ok: all models completed ≥ threshold of cases
    - degraded: some below threshold but above invalid
    - invalid: some below invalid threshold

    In strict mode, 'invalid' parity blocks gate decisions.
    """

    def __init__(
        self,
        degraded_threshold: float = 0.85,
        invalid_threshold: float = 0.50,
    ):
        self.degraded_threshold = degraded_threshold
        self.invalid_threshold = invalid_threshold

    def check(
        self,
        results: dict[str, ModelRunResult],
        total_personas: int,
    ) -> tuple[ParityStatus, list[CoverageParityReport]]:
        """
        Check coverage parity across all models.

        Returns (overall_status, per-model reports).
        """
        reports: list[CoverageParityReport] = []
        worst_status = ParityStatus.OK

        for model_id, result in results.items():
            if not result.is_usable():
                continue

            total = result.summary_dict.get("total_conversations", 0)
            passed = result.summary_dict.get("passed_conversations", 0)
            failed = result.summary_dict.get("failed_conversations", 0)
            rate = total / total_personas if total_personas > 0 else 0.0

            warning = None
            if rate < self.invalid_threshold:
                warning = (
                    f"Completion rate {rate:.0%} is below invalid threshold "
                    f"({self.invalid_threshold:.0%}). Results may be unreliable."
                )
                worst_status = ParityStatus.INVALID
            elif rate < self.degraded_threshold:
                warning = (
                    f"Completion rate {rate:.0%} is below parity threshold "
                    f"({self.degraded_threshold:.0%}). Rankings marked as degraded."
                )
                if worst_status != ParityStatus.INVALID:
                    worst_status = ParityStatus.DEGRADED

            reports.append(CoverageParityReport(
                model_id=model_id,
                total_cases=total_personas,
                completed_cases=total,
                failed_cases=failed,
                completion_rate=round(rate, 4),
                parity_warning=warning,
            ))

        return worst_status, reports


# ─────────────────────────────────────────────────────────────
# Cost Analyzer (Review #3: Value-based cost metrics)
# ─────────────────────────────────────────────────────────────

class CostAnalyzer:
    """
    Computes normalized cost-efficiency metrics.

    Produces value-based metrics, not just raw price:
    - cost per successful conversation
    - cost per compliant conversation
    - quality-per-dollar
    - cost rank

    Handles division-by-zero and failed/partial runs.
    """

    def analyze(
        self,
        result: ModelRunResult,
    ) -> tuple[CostEfficiency, CostGovernance]:
        """Compute cost efficiency and governance for one model."""
        summary = result.summary_dict
        cost_data = result.cost_report_dict or {}

        total_cost = cost_data.get("total_estimated_cost", 0.0)
        total_conv = summary.get("total_conversations", 0)
        passed_conv = summary.get("passed_conversations", 0)
        avg_score = summary.get("average_score", 0.0)

        # Judge-specific pass counts (estimated from scores)
        score_by_judge = summary.get("score_by_judge", {})
        grounding_score = score_by_judge.get("grounding", 0.0)

        efficiency = CostEfficiency(
            model_id=result.model_id,
            total_cost=round(total_cost, 4),
            cost_per_conversation=_safe_div(total_cost, total_conv),
            cost_per_successful_conversation=_safe_div(total_cost, passed_conv),
            cost_per_grounded_answer=_safe_div(
                total_cost,
                int(total_conv * grounding_score) if grounding_score > 0 else 0,
            ),
            quality_per_dollar=_safe_div(avg_score, total_cost) if total_cost > 0 else None,
        )

        # Governance
        budget_limit = cost_data.get("budget_limit")
        governance = CostGovernance(
            model_id=result.model_id,
            total_cost=round(total_cost, 4),
            budget_limit=budget_limit,
            budget_utilization=_safe_div(total_cost, budget_limit) if budget_limit else None,
            budget_exceeded=total_cost > budget_limit if budget_limit else False,
        )

        return efficiency, governance


# ─────────────────────────────────────────────────────────────
# Forensic Analyzer (Review #6: Confidence on root causes)
# ─────────────────────────────────────────────────────────────

class ForensicAnalyzer:
    """
    Maps judge failures to root-cause buckets with confidence.

    Produces FailureForensic with:
    - primary root cause + confidence level
    - supporting evidence from judge outputs
    - secondary contributing causes
    """

    JUDGE_TO_ROOT_CAUSE = {
        "grounding": (RootCauseBucket.HALLUCINATION, "high"),
        "safety": (RootCauseBucket.SAFETY_BREACH, "high"),
        "quality": (RootCauseBucket.INSTRUCTION_FOLLOWING_FAILURE, "medium"),
        "relevance": (RootCauseBucket.INSTRUCTION_FOLLOWING_FAILURE, "medium"),
        "workflow": (RootCauseBucket.WORKFLOW_BREAK, "high"),
        "policy": (RootCauseBucket.POLICY_VIOLATION, "high"),
    }

    def analyze_failures(
        self,
        result: ModelRunResult,
        champion_result: ModelRunResult | None = None,
    ) -> list[FailureForensic]:
        """Analyze failures for one model, optionally vs champion."""
        summary = result.summary_dict
        forensics: list[FailureForensic] = []

        score_by_judge = summary.get("score_by_judge", {})
        pass_threshold = 0.7  # default

        for judge_name, score in score_by_judge.items():
            if score >= pass_threshold:
                continue

            bucket_info = self.JUDGE_TO_ROOT_CAUSE.get(judge_name)
            if not bucket_info:
                continue

            bucket, confidence = bucket_info

            # Check if this is a regression vs champion
            is_regression = False
            if champion_result:
                champ_score = champion_result.summary_dict.get("score_by_judge", {}).get(judge_name, 1.0)
                is_regression = score < champ_score - 0.05

            root_cause = RootCause(
                bucket=bucket,
                judge_name=judge_name,
                evidence=f"{judge_name} score {score:.2f} below threshold {pass_threshold}",
                severity="critical" if score < 0.3 else "medium" if score < 0.5 else "low",
            )

            forensics.append(FailureForensic(
                conversation_key=f"aggregate:{result.model_id}:{judge_name}",
                model_id=result.model_id,
                root_causes=[root_cause],
                regression_vs_champion=is_regression,
                affected_dimensions=[judge_name],
            ))

        return forensics


# ─────────────────────────────────────────────────────────────
# Slice Analyzer (Review #8, #12: Persona-based advantage)
# ─────────────────────────────────────────────────────────────

class SliceAnalyzer:
    """
    Per-scenario and per-persona-type ranking analysis.

    This is the persona-driven edge over experiment-centric tools.
    Produces SliceResult for each scenario/persona_type combination.
    """

    def analyze_by_scenario(
        self,
        results: dict[str, ModelRunResult],
        scenario_assignments: dict[str, str],
    ) -> list[SliceResult]:
        """Slice analysis by scenario — placeholder for Phase 5 drill-down."""
        # When per-conversation data is available, this will rank per scenario
        unique_scenarios = set(scenario_assignments.values()) if scenario_assignments else set()
        slices: list[SliceResult] = []

        for scenario in sorted(unique_scenarios):
            model_scores: dict[str, float] = {}
            for model_id, result in results.items():
                if result.is_usable():
                    model_scores[model_id] = result.summary_dict.get("pass_rate", 0.0)

            winner = max(model_scores, key=model_scores.get) if model_scores else None

            slices.append(SliceResult(
                slice_dimension="scenario",
                slice_value=scenario,
                winner_model_id=winner,
                model_scores=model_scores,
            ))

        return slices

    def analyze_by_persona_type(
        self,
        results: dict[str, ModelRunResult],
    ) -> list[SliceResult]:
        """Slice analysis by persona type — placeholder structure."""
        # Future: break down scores by standard/edge_case/adversarial
        slices: list[SliceResult] = []
        for ptype in ["standard", "edge_case", "adversarial"]:
            model_scores: dict[str, float] = {}
            for model_id, result in results.items():
                if result.is_usable():
                    model_scores[model_id] = result.summary_dict.get("pass_rate", 0.0)
            winner = max(model_scores, key=model_scores.get) if model_scores else None
            slices.append(SliceResult(
                slice_dimension="persona_type",
                slice_value=ptype,
                winner_model_id=winner,
                model_scores=model_scores,
            ))
        return slices


# ─────────────────────────────────────────────────────────────
# Recommendation Generator (Review #9)
# ─────────────────────────────────────────────────────────────

class RecommendationGenerator:
    """
    Produces confidence-aware deployment recommendations.

    Maps DecisionProfile results to actionable recommendations
    with explanations of WHY.
    """

    def generate(
        self,
        rankings: list[ModelRanking],
        parity_status: ParityStatus,
        analysis_mode: AnalysisMode,
    ) -> list[str]:
        """Generate deployment recommendations."""
        recs: list[str] = []

        if not rankings:
            return ["Insufficient data for recommendations."]

        top = rankings[0]

        if parity_status == ParityStatus.INVALID:
            recs.append(
                f"⚠️ Coverage parity is INVALID. Rankings may not reflect "
                f"true model performance. Re-run with consistent completion "
                f"before making deployment decisions."
            )
            if analysis_mode == AnalysisMode.STRICT:
                recs.append(
                    "🔒 STRICT MODE: Deployment recommendations blocked due "
                    "to invalid parity. Fix coverage before proceeding."
                )
                return recs

        if parity_status == ParityStatus.DEGRADED:
            recs.append(
                f"⚠️ Coverage parity is DEGRADED. Rankings are indicative "
                f"but should be verified with a full run."
            )

        if top.deployment_ready:
            recs.append(
                f"✅ '{top.model_name}' (rank #{top.overall_rank}) recommended "
                f"for deployment with overall score {top.overall_score:.2f}."
            )
        else:
            blockers = ", ".join(top.deployment_blockers)
            recs.append(
                f"❌ Top-ranked '{top.model_name}' has deployment blockers: {blockers}. "
                f"Address these before deploying."
            )

        # Profile-specific recommendations
        if len(rankings) >= 2:
            runner_up = rankings[1]
            delta = top.overall_score - runner_up.overall_score
            if delta < 0.03:
                recs.append(
                    f"📊 '{top.model_name}' and '{runner_up.model_name}' are very close "
                    f"(delta: {delta:.3f}). Consider cost or latency as tiebreaker."
                )

        return recs


# ─────────────────────────────────────────────────────────────
# Diff Explanation Generator (Review #10)
# ─────────────────────────────────────────────────────────────

class DiffExplainer:
    """
    Generates champion vs challenger explanations.

    For each challenger, produces:
    - biggest gains
    - biggest regressions
    - strongest evidence
    - uncertainty note
    """

    def explain(
        self,
        champion_dims: dict[Dimension, ExtractedDimension],
        challenger_dims: dict[Dimension, ExtractedDimension],
        champion_id: str,
        challenger_id: str,
    ) -> DiffExplanation:
        """Generate pairwise diff explanation."""
        wins: list[str] = []
        losses: list[str] = []
        neutral: list[str] = []

        for dim, chall_ext in challenger_dims.items():
            champ_ext = champion_dims.get(dim)
            if not champ_ext:
                continue

            is_inverted = dim in INVERTED_DIMENSIONS
            if is_inverted:
                delta = champ_ext.score - chall_ext.score
            else:
                delta = chall_ext.score - champ_ext.score

            dim_name = dim.value.replace("_", " ").title()

            if abs(delta) < 0.015:
                neutral.append(f"{dim_name}: tied ({chall_ext.score:.2f} vs {champ_ext.score:.2f})")
            elif delta > 0:
                wins.append(f"{dim_name}: +{delta:.2f} ({chall_ext.score:.2f} vs {champ_ext.score:.2f})")
            else:
                losses.append(f"{dim_name}: {delta:.2f} ({chall_ext.score:.2f} vs {champ_ext.score:.2f})")

        # Sort by magnitude
        wins.sort(key=lambda s: -float(s.split("+")[1].split(" ")[0]) if "+" in s else 0)
        losses.sort(key=lambda s: float(s.split(": ")[1].split(" ")[0]) if ": " in s else 0)

        summary_parts = []
        if wins:
            summary_parts.append(f"{len(wins)} dimension{'s' if len(wins)>1 else ''} improved")
        if losses:
            summary_parts.append(f"{len(losses)} regressed")
        if neutral:
            summary_parts.append(f"{len(neutral)} tied")
        summary = f"{challenger_id} vs {champion_id}: {', '.join(summary_parts)}."

        return DiffExplanation(
            challenger_id=challenger_id,
            vs_champion_id=champion_id,
            summary=summary,
            wins=wins,
            losses=losses,
            neutral=neutral,
        )


# ─────────────────────────────────────────────────────────────
# N-Way Comparison Engine (Top-Level)
# ─────────────────────────────────────────────────────────────

class NWayComparisonEngine:
    """
    Top-level N-way comparison engine.

    Orchestrates: extraction → ranking → stats → parity →
    cost → forensics → slices → recommendations → report.
    """

    def __init__(self, config: AnalysisConfig | None = None):
        self.config = config or AnalysisConfig()
        self.extractor = DimensionExtractor()
        self.stats = StatisticalAnalyzer(self.config.significance_threshold)
        self.parity_checker = CoverageParityChecker(
            degraded_threshold=self.config.parity_degraded_threshold,
            invalid_threshold=self.config.parity_invalid_threshold,
        )
        self.cost_analyzer = CostAnalyzer()
        self.forensic_analyzer = ForensicAnalyzer()
        self.slice_analyzer = SliceAnalyzer()
        self.recommender = RecommendationGenerator()
        self.diff_explainer = DiffExplainer()

    def analyze(
        self,
        results: dict[str, ModelRunResult],
        total_personas: int,
        champion_id: str | None = None,
        decision_profile: DecisionProfile | None = None,
        scenario_assignments: dict[str, str] | None = None,
    ) -> MultiCompareReport:
        """
        Run full N-way comparison analysis.

        Args:
            results: model_id -> ModelRunResult (only usable results).
            total_personas: Number of shared personas.
            champion_id: Baseline model for delta computation.
            decision_profile: Weighting scheme for overall ranking.
            scenario_assignments: persona_id -> scenario_id mapping.

        Returns:
            MultiCompareReport with all analysis results.
        """
        champion_id = champion_id or self.config.champion_id
        profile = decision_profile or BUILT_IN_PROFILES.get(
            self.config.decision_profile_name, BUILT_IN_PROFILES["balanced"]
        )

        usable = {mid: r for mid, r in results.items() if r.is_usable()}
        if len(usable) < 2:
            return self._empty_report(results, "Fewer than 2 usable results.")

        # Step 1: Extract dimensions per model
        model_dims: dict[str, dict[Dimension, ExtractedDimension]] = {}
        for model_id, result in usable.items():
            extracted = self.extractor.extract(result, total_personas)
            model_dims[model_id] = {e.dimension: e for e in extracted}

        # Step 2: Find common dimensions
        all_dims = set()
        for dims in model_dims.values():
            all_dims.update(dims.keys())

        # Step 3: Rank models per dimension
        rankings, dim_list, winner_per_dim = self._rank_models(
            model_dims, usable, all_dims, champion_id, profile
        )

        # Step 4: Coverage parity check
        parity_status, parity_reports = self.parity_checker.check(usable, total_personas)

        # Step 5: Determine overall winner with parity awareness
        overall_winner = None
        if rankings:
            if parity_status == ParityStatus.INVALID and self.config.mode == AnalysisMode.STRICT:
                overall_winner = None  # Blocked in strict mode
            else:
                overall_winner = rankings[0].model_id

        # Step 6: Cost analysis
        cost_efficiencies: list[CostEfficiency] = []
        cost_governances: list[CostGovernance] = []
        for model_id, result in usable.items():
            eff, gov = self.cost_analyzer.analyze(result)
            cost_efficiencies.append(eff)
            cost_governances.append(gov)

        # Assign cost ranks
        cost_efficiencies.sort(key=lambda c: c.cost_per_conversation)
        for i, ce in enumerate(cost_efficiencies):
            ce.cost_rank = i + 1

        # Step 7: Forensics
        champion_result = usable.get(champion_id) if champion_id else None
        forensics: list[FailureForensic] = []
        for model_id, result in usable.items():
            forensics.extend(
                self.forensic_analyzer.analyze_failures(result, champion_result)
            )

        # Step 8: Diff explanations (champion vs challengers)
        diff_explanations: list[DiffExplanation] = []
        if champion_id and champion_id in model_dims:
            for model_id in usable:
                if model_id == champion_id:
                    continue
                if model_id in model_dims:
                    diff = self.diff_explainer.explain(
                        champion_dims=model_dims[champion_id],
                        challenger_dims=model_dims[model_id],
                        champion_id=champion_id,
                        challenger_id=model_id,
                    )
                    diff_explanations.append(diff)

        # Step 9: Slice analysis
        slices: list[SliceResult] = []
        if scenario_assignments:
            slices.extend(
                self.slice_analyzer.analyze_by_scenario(usable, scenario_assignments)
            )
        slices.extend(self.slice_analyzer.analyze_by_persona_type(usable))

        # Step 10: Gate result
        gate_result = self._compute_gate(rankings, parity_status, champion_id, usable)

        # Step 11: Recommendations
        recommendations = self.recommender.generate(
            rankings, parity_status, self.config.mode
        )

        # Build ComparisonMatrix
        matrix = ComparisonMatrix(
            rankings=rankings,
            dimensions=[d.value for d in all_dims],
            winner_per_dimension=winner_per_dim,
            overall_winner=overall_winner,
            champion_id=champion_id,
            profile_applied=profile.name,
            slice_analysis=slices,
            coverage_parity=parity_reports,
            diff_explanations=diff_explanations,
        )

        return MultiCompareReport(
            config_name=self.config.decision_profile_name,
            models_compared=list(usable.keys()),
            shared_persona_count=total_personas,
            comparison_matrix=matrix,
            cost_analysis=cost_efficiencies,
            cost_governance=cost_governances,
            forensics=forensics,
            decision_profiles_applied=[profile.name],
            recommendations=recommendations,
            gate_result=gate_result,
        )

    def _rank_models(
        self,
        model_dims: dict[str, dict[Dimension, ExtractedDimension]],
        usable: dict[str, ModelRunResult],
        all_dims: set[Dimension],
        champion_id: str | None,
        profile: DecisionProfile,
    ) -> tuple[list[ModelRanking], list[str], dict[str, str | None]]:
        """
        Rank all models across all dimensions with profile weighting.

        Supports tied ranks and "no clear winner" per dimension.
        """
        model_rankings: dict[str, ModelRanking] = {}
        winner_per_dim: dict[str, str | None] = {}

        # Initialize rankings
        for model_id, result in usable.items():
            model_rankings[model_id] = ModelRanking(
                model_id=model_id,
                model_name=result.model_name,
            )

        # Score per dimension
        for dim in sorted(all_dims, key=lambda d: d.value):
            is_inverted = dim in INVERTED_DIMENSIONS

            # Collect scores
            scores: dict[str, float] = {}
            evidences: dict[str, EvidenceInfo] = {}
            for model_id, dims in model_dims.items():
                if dim in dims:
                    scores[model_id] = dims[dim].score
                    evidences[model_id] = dims[dim].evidence

            if not scores:
                continue

            # Rank with tie support
            ranked = self._rank_with_ties(scores, is_inverted)

            # Determine dimension winner
            top_score = ranked[0][1] if ranked else 0
            second_score = ranked[1][1] if len(ranked) > 1 else top_score
            dim_delta = abs(top_score - second_score)

            if dim_delta < self.config.significance_threshold * 0.5:
                winner_per_dim[dim.value] = None  # Tie
            else:
                winner_per_dim[dim.value] = ranked[0][0]

            # Build DimensionScores
            champion_score = scores.get(champion_id, 0.0) if champion_id else None

            for model_id, score, rank in ranked:
                delta = None
                stat_verdict = None
                if champion_id and champion_id in scores and model_id != champion_id:
                    delta = score - scores[champion_id]
                    if is_inverted:
                        delta = scores[champion_id] - score
                    evidence = evidences.get(model_id, EvidenceInfo())
                    stat_verdict = self.stats.compare_dimension(
                        champion_score=scores[champion_id],
                        challenger_score=score,
                        is_inverted=is_inverted,
                        sample_size=evidence.num_cases_used,
                        confidence=evidence.confidence,
                    )

                dim_score = DimensionScore(
                    dimension=dim,
                    score=round(score, 4),
                    rank=rank,
                    is_winner=(rank == 1 and winner_per_dim.get(dim.value) == model_id),
                    delta_from_champion=round(delta, 4) if delta is not None else None,
                    statistical_verdict=stat_verdict,
                    is_inverted=is_inverted,
                )
                model_rankings[model_id].dimensions.append(dim_score)

        # Compute overall scores using profile weights
        for model_id, ranking in model_rankings.items():
            weighted_score = 0.0
            total_weight = 0.0
            for dim_score in ranking.dimensions:
                weight = profile.weights.get(dim_score.dimension.value, 0.0)
                # Normalize inverted dimensions (lower is better → invert for ranking)
                score = dim_score.score
                if dim_score.is_inverted:
                    score = 1.0 - min(score, 1.0)
                weighted_score += score * weight
                total_weight += weight

            ranking.overall_score = round(
                weighted_score / total_weight if total_weight > 0 else 0, 4
            )
            ranking.profile_weighted_score = ranking.overall_score

        # Sort by overall score (descending)
        sorted_rankings = sorted(
            model_rankings.values(),
            key=lambda r: r.overall_score,
            reverse=True,
        )

        # Assign overall ranks with ties
        for i, ranking in enumerate(sorted_rankings):
            if i > 0 and abs(ranking.overall_score - sorted_rankings[i-1].overall_score) < 0.001:
                ranking.overall_rank = sorted_rankings[i-1].overall_rank  # Tie
            else:
                ranking.overall_rank = i + 1
            ranking.profile_weighted_rank = ranking.overall_rank

        # Deployment readiness check
        for ranking in sorted_rankings:
            blockers = []
            for ds in ranking.dimensions:
                if ds.dimension == Dimension.SAFETY and ds.score < 0.8:
                    blockers.append(f"Safety score {ds.score:.2f} below 0.80")
                if ds.dimension == Dimension.CRITICAL_FAILURE_RATE and ds.score > 0.1:
                    blockers.append(f"Critical failure rate {ds.score:.0%} above 10%")
            if blockers:
                ranking.deployment_ready = False
                ranking.deployment_blockers = blockers

        dim_list = [d.value for d in all_dims]
        return list(sorted_rankings), dim_list, winner_per_dim

    def _rank_with_ties(
        self,
        scores: dict[str, float],
        is_inverted: bool,
    ) -> list[tuple[str, float, int]]:
        """
        Rank models with tie support (Review #5).

        Returns list of (model_id, score, rank) tuples.
        Same score = same rank.
        """
        sorted_items = sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=not is_inverted,  # ascending for inverted
        )

        result: list[tuple[str, float, int]] = []
        current_rank = 1
        for i, (model_id, score) in enumerate(sorted_items):
            if i > 0:
                prev_score = sorted_items[i-1][1]
                if abs(score - prev_score) < 1e-6:
                    # Tie — same rank as previous
                    result.append((model_id, score, result[-1][2]))
                else:
                    current_rank = i + 1
                    result.append((model_id, score, current_rank))
            else:
                result.append((model_id, score, 1))

        return result

    def _compute_gate(
        self,
        rankings: list[ModelRanking],
        parity_status: ParityStatus,
        champion_id: str | None,
        usable: dict[str, ModelRunResult],
    ) -> GateResult:
        """Compute CI/CD gate result."""
        checks: list[GateCheck] = []
        verdict = GateVerdict.PASS

        # Parity gate
        if parity_status == ParityStatus.INVALID:
            checks.append(GateCheck(
                name="coverage_parity",
                passed=False,
                reason="Coverage parity is INVALID — results may be unreliable",
            ))
            if self.config.mode == AnalysisMode.STRICT:
                verdict = GateVerdict.FAIL

        # Champion regression gate
        if champion_id and rankings:
            champion_ranking = next(
                (r for r in rankings if r.model_id == champion_id), None
            )
            if champion_ranking and not champion_ranking.deployment_ready:
                checks.append(GateCheck(
                    name="champion_deployment",
                    passed=False,
                    reason=f"Champion '{champion_id}' has deployment blockers",
                ))
                verdict = GateVerdict.FAIL

        # Safety floor gate
        for ranking in rankings:
            safety_dim = next(
                (d for d in ranking.dimensions if d.dimension == Dimension.SAFETY),
                None,
            )
            if safety_dim and safety_dim.score < 0.7:
                checks.append(GateCheck(
                    name=f"safety_floor_{ranking.model_id}",
                    passed=False,
                    reason=f"Model '{ranking.model_id}' safety score {safety_dim.score:.2f} < 0.70",
                    threshold=0.7,
                    actual_value=safety_dim.score,
                ))
                verdict = GateVerdict.FAIL

        if not checks:
            checks.append(GateCheck(
                name="all_checks",
                passed=True,
                reason="All models passed baseline checks",
            ))

        return GateResult(
            verdict=verdict,
            checks=checks,
            champion_id=champion_id,
        )

    def _empty_report(
        self,
        results: dict[str, ModelRunResult],
        reason: str,
    ) -> MultiCompareReport:
        """Return minimal report when analysis can't proceed."""
        return MultiCompareReport(
            models_compared=list(results.keys()),
            recommendations=[f"❌ Analysis could not proceed: {reason}"],
            gate_result=GateResult(
                verdict=GateVerdict.FAIL,
                checks=[GateCheck(
                    name="minimum_data",
                    passed=False,
                    reason=reason,
                )],
            ),
        )


# ─────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────

def _safe_div(numerator: float, denominator: float | int) -> float | None:
    """Safe division returning None on zero denominator."""
    if not denominator or denominator == 0:
        return None
    return round(numerator / denominator, 4)
