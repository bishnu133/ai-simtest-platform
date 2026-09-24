"""
P3 #19 — Verbosity Analyzer

Measures how much the bot says: length stats, information density,
trend over conversation turns, drift detection (early vs late).

Consumes TurnSignals — no raw text parsing.
"""

from __future__ import annotations

from ..models import (
    TurnSignals,
    VerbosityProfile,
    VerbosityRawMetrics,
    VerbosityDerivedScores,
    VerbosityLevel,
    DriftDirection,
)
from ..thresholds import SignatureThresholds, compute_reliability
from ..utils import safe_mean, safe_median, safe_percentile


class VerbosityAnalyzer:
    """Analyzes verbosity patterns from pre-extracted turn signals."""

    def __init__(self, thresholds: SignatureThresholds | None = None):
        self._thresholds = thresholds or SignatureThresholds()

    def analyze(self, signals: list[TurnSignals]) -> VerbosityProfile:
        """
        Produce a VerbosityProfile from turn signals.

        Returns partial profile with low confidence if below sample threshold.
        """
        if not signals:
            return VerbosityProfile(
                reliability=compute_reliability(0, self._thresholds.min_responses_for_verbosity),
            )

        word_counts = [s.word_count for s in signals]
        sentence_counts = [s.sentence_count for s in signals]
        wps_values = [s.avg_words_per_sentence for s in signals]
        uwr_values = [s.unique_word_ratio for s in signals]

        n = len(signals)
        total_words = sum(word_counts)

        # Raw metrics
        raw = VerbosityRawMetrics(
            total_responses=n,
            total_words=total_words,
            mean_words=safe_mean(word_counts),
            median_words=safe_median(word_counts),
            p95_words=safe_percentile(word_counts, 95),
            min_words=min(word_counts) if word_counts else 0,
            max_words=max(word_counts) if word_counts else 0,
            mean_sentences=safe_mean(sentence_counts),
            mean_words_per_sentence=safe_mean(wps_values),
            mean_unique_word_ratio=safe_mean(uwr_values),
        )

        # Early vs late drift (if enough samples)
        extra_notes = []
        if n >= self._thresholds.min_responses_for_drift:
            third = max(n // 3, 1)
            early = [s.word_count for s in signals[:third]]
            late = [s.word_count for s in signals[-third:]]
            raw.early_turn_mean_words = safe_mean(early)
            raw.late_turn_mean_words = safe_mean(late)
        else:
            extra_notes.append("Too few responses for drift analysis")

        # Derived scores
        derived = self._compute_derived(raw, n)

        # Evidence
        evidence = self._collect_evidence(signals, raw, derived)

        # Reliability
        reliability = compute_reliability(
            n,
            self._thresholds.min_responses_for_verbosity,
            extra_notes=extra_notes,
        )

        return VerbosityProfile(
            raw=raw,
            derived=derived,
            reliability=reliability,
            evidence=evidence,
        )

    def _compute_derived(
        self, raw: VerbosityRawMetrics, n: int,
    ) -> VerbosityDerivedScores:
        """Compute interpreted labels from raw metrics."""

        # Verbosity level based on median word count
        if raw.median_words < 30:
            level = VerbosityLevel.LOW
        elif raw.median_words < 100:
            level = VerbosityLevel.MEDIUM
        else:
            level = VerbosityLevel.HIGH

        # Drift direction and magnitude
        drift_dir = DriftDirection.STABLE
        drift_mag = 0.0
        if raw.early_turn_mean_words > 0 and raw.late_turn_mean_words > 0:
            drift_mag = (
                (raw.late_turn_mean_words - raw.early_turn_mean_words)
                / raw.early_turn_mean_words
            )
            if drift_mag > 0.15:
                drift_dir = DriftDirection.INCREASING
            elif drift_mag < -0.15:
                drift_dir = DriftDirection.DECREASING

        return VerbosityDerivedScores(
            verbosity_level=level,
            verbosity_drift_direction=drift_dir,
            verbosity_drift_magnitude=abs(drift_mag),
            information_density=raw.mean_unique_word_ratio,
        )

    def _collect_evidence(
        self,
        signals: list[TurnSignals],
        raw: VerbosityRawMetrics,
        derived: VerbosityDerivedScores,
    ) -> list[dict[str, str]]:
        """Collect 2-3 evidence snippets for the report."""
        evidence = []

        evidence.append({
            "metric": f"Verbosity: {derived.verbosity_level.value}",
            "value": f"Median {raw.median_words:.0f} words (range {raw.min_words}-{raw.max_words})",
            "example": "",
        })

        # Find shortest and longest response for contrast
        if signals:
            shortest = min(signals, key=lambda s: s.word_count)
            longest = max(signals, key=lambda s: s.word_count)
            if shortest.text_snippet and longest.text_snippet:
                evidence.append({
                    "metric": "Shortest response",
                    "value": f"{shortest.word_count} words",
                    "example": shortest.text_snippet,
                })
                evidence.append({
                    "metric": "Longest response",
                    "value": f"{longest.word_count} words",
                    "example": longest.text_snippet,
                })

        if derived.verbosity_drift_direction != DriftDirection.STABLE:
            evidence.append({
                "metric": f"Drift: {derived.verbosity_drift_direction.value}",
                "value": f"{derived.verbosity_drift_magnitude:.0%} change early→late",
                "example": f"Early avg: {raw.early_turn_mean_words:.0f} words → Late avg: {raw.late_turn_mean_words:.0f} words",
            })

        return evidence
