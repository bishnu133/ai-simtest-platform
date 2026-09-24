"""
Expansion Analyzer — Reproducibility analysis and minimal reproducible conversation finder.

Phase 4 of the Adaptive Expansion pipeline:
  VariantResult[] → SignalExpansionResult (verdict + minimal repro)
"""

from __future__ import annotations

import time
from typing import Any

from .models import (
    AdaptiveExpansionConfig,
    ExpansionReport,
    FailureSignal,
    ReproducibilityVerdict,
    SignalExpansionResult,
    VariantResult,
)


class ExpansionAnalyzer:
    """
    Analyzes variant execution results to determine reproducibility
    and identify minimal reproducible conversations.
    """

    def __init__(self, config: AdaptiveExpansionConfig | None = None):
        self.config = config or AdaptiveExpansionConfig()

    def analyze_signal(
        self,
        signal: FailureSignal,
        variants_run: list,  # ExpansionVariant list
        results: list[VariantResult],
    ) -> SignalExpansionResult:
        """
        Analyze results for a single failure signal.

        Computes reproducibility score, assigns a verdict,
        and identifies the minimal reproducible conversation.
        """
        total = len(results)
        reproduced_results = [r for r in results if r.reproduced]
        reproduced_count = len(reproduced_results)

        # Compute reproducibility score
        if total == 0:
            repro_score = 0.0
        else:
            repro_score = reproduced_count / total

        # Assign verdict
        verdict = self._assign_verdict(repro_score)

        # Find minimal reproducible conversation
        minimal = self._find_minimal_repro(reproduced_results)

        return SignalExpansionResult(
            signal=signal,
            variants=variants_run,
            results=results,
            reproducibility_score=repro_score,
            verdict=verdict,
            minimal_repro=minimal,
            total_variants=total,
            reproduced_count=reproduced_count,
        )

    def build_report(
        self,
        total_signals_found: int,
        expansion_results: list[SignalExpansionResult],
        errors: list[str],
        execution_time: float,
    ) -> ExpansionReport:
        """
        Build the complete expansion report from all signal results.
        """
        confirmed = [r for r in expansion_results if r.verdict == ReproducibilityVerdict.CONFIRMED]
        likely = [r for r in expansion_results if r.verdict == ReproducibilityVerdict.LIKELY]
        flukes = [r for r in expansion_results if r.verdict in (
            ReproducibilityVerdict.FLUKE, ReproducibilityVerdict.INCONCLUSIVE
        )]

        total_variants = sum(r.total_variants for r in expansion_results)

        return ExpansionReport(
            total_signals_found=total_signals_found,
            signals_expanded=len(expansion_results),
            total_variants_run=total_variants,
            confirmed_bugs=confirmed,
            likely_bugs=likely,
            flukes=flukes,
            errors=errors,
            execution_time_seconds=execution_time,
        )

    def _assign_verdict(self, repro_score: float) -> ReproducibilityVerdict:
        """Assign a reproducibility verdict based on score."""
        if repro_score >= self.config.reproducibility_threshold:
            return ReproducibilityVerdict.CONFIRMED
        elif repro_score >= self.config.likely_threshold:
            return ReproducibilityVerdict.LIKELY
        elif repro_score > 0:
            return ReproducibilityVerdict.FLUKE
        else:
            return ReproducibilityVerdict.INCONCLUSIVE

    def _find_minimal_repro(
        self, reproduced_results: list[VariantResult]
    ) -> VariantResult | None:
        """
        Find the variant with the fewest turns that still reproduced the failure.

        This is the "minimal reproducible example" — the simplest test case
        that triggers the bug.
        """
        if not reproduced_results:
            return None

        # Sort by conversation turns (ascending), then by matching score (ascending = worse failure)
        sorted_results = sorted(
            reproduced_results,
            key=lambda r: (r.conversation_turns, r.matching_score),
        )

        return sorted_results[0]
