"""
P3 #19 — Tone Analyzer

Measures emotional tone markers, formality indicators, empathy phrase frequency,
hedge frequency, directive language frequency, and refusal behavior.

All rule-based — no LLM calls. Uses pre-extracted TurnSignals.
"""

from __future__ import annotations

from ..models import (
    TurnSignals,
    ToneProfile,
    ToneRawMetrics,
    ToneDerivedScores,
    FormalityLevel,
)
from ..thresholds import SignatureThresholds, compute_reliability
from ..utils import safe_mean, safe_stdev


class ToneAnalyzer:
    """Analyzes tone patterns from pre-extracted turn signals."""

    def __init__(self, thresholds: SignatureThresholds | None = None):
        self._thresholds = thresholds or SignatureThresholds()

    def analyze(self, signals: list[TurnSignals]) -> ToneProfile:
        """Produce a ToneProfile from turn signals."""
        n = len(signals)
        if not signals:
            return ToneProfile(
                reliability=compute_reliability(0, self._thresholds.min_responses_for_tone),
            )

        # Aggregate raw metrics
        raw = ToneRawMetrics(
            total_responses=n,
            positive_marker_count=sum(s.positive_markers for s in signals),
            negative_marker_count=sum(s.negative_markers for s in signals),
            neutral_indicator_count=sum(s.neutral_indicators for s in signals),
            empathy_phrase_count=sum(s.empathy_phrases for s in signals),
            hedge_phrase_count=sum(s.hedge_phrases for s in signals),
            directive_phrase_count=sum(s.directive_phrases for s in signals),
            apology_phrase_count=sum(s.apology_phrases for s in signals),
            avg_formality_score=safe_mean([s.formality_score for s in signals]),
            formality_std_dev=safe_stdev([s.formality_score for s in signals]),
            refusal_count=sum(1 for s in signals if s.is_refusal),
            refusal_with_redirect_count=sum(1 for s in signals if s.is_refusal and s.refusal_has_redirect),
        )

        # Derived scores
        derived = self._compute_derived(raw, n)

        # Evidence
        evidence = self._collect_evidence(signals, raw, derived, n)

        # Reliability
        reliability = compute_reliability(
            n, self._thresholds.min_responses_for_tone,
        )

        return ToneProfile(
            raw=raw,
            derived=derived,
            reliability=reliability,
            evidence=evidence,
        )

    def _compute_derived(self, raw: ToneRawMetrics, n: int) -> ToneDerivedScores:
        """Compute interpreted tone labels from raw metrics."""

        # Sentiment ratios
        total_markers = (
            raw.positive_marker_count
            + raw.negative_marker_count
            + raw.neutral_indicator_count
        )
        if total_markers > 0:
            pos_ratio = raw.positive_marker_count / total_markers
            neg_ratio = raw.negative_marker_count / total_markers
            neu_ratio = raw.neutral_indicator_count / total_markers
        else:
            pos_ratio = 0.0
            neg_ratio = 0.0
            neu_ratio = 1.0

        # Per-response frequencies
        empathy_freq = raw.empathy_phrase_count / n if n > 0 else 0.0
        hedge_freq = raw.hedge_phrase_count / n if n > 0 else 0.0
        directive_freq = raw.directive_phrase_count / n if n > 0 else 0.0
        apology_freq = raw.apology_phrase_count / n if n > 0 else 0.0

        # Formality level
        if raw.avg_formality_score >= 0.65:
            formality = FormalityLevel.FORMAL
        elif raw.avg_formality_score <= 0.35:
            formality = FormalityLevel.INFORMAL
        else:
            formality = FormalityLevel.NEUTRAL

        # Refusal rates
        refusal_rate = raw.refusal_count / n if n > 0 else 0.0
        redirect_rate = (
            raw.refusal_with_redirect_count / raw.refusal_count
            if raw.refusal_count > 0
            else 0.0
        )

        return ToneDerivedScores(
            positive_ratio=pos_ratio,
            negative_ratio=neg_ratio,
            neutral_ratio=neu_ratio,
            empathy_frequency=empathy_freq,
            hedge_frequency=hedge_freq,
            directive_frequency=directive_freq,
            apology_frequency=apology_freq,
            formality_level=formality,
            refusal_rate=refusal_rate,
            refusal_redirect_rate=redirect_rate,
        )

    def _collect_evidence(
        self,
        signals: list[TurnSignals],
        raw: ToneRawMetrics,
        derived: ToneDerivedScores,
        n: int,
    ) -> list[dict[str, str]]:
        """Collect evidence snippets for the report."""
        evidence = []

        # Formality
        evidence.append({
            "metric": f"Formality: {derived.formality_level.value}",
            "value": f"Avg score {raw.avg_formality_score:.2f} (std {raw.formality_std_dev:.2f})",
            "example": "",
        })

        # Empathy
        if derived.empathy_frequency > 0:
            # Find a response with empathy
            empathy_example = next(
                (s.text_snippet for s in signals if s.empathy_phrases > 0), ""
            )
            evidence.append({
                "metric": "Empathy phrase frequency",
                "value": f"{derived.empathy_frequency:.2f} per response ({raw.empathy_phrase_count} total)",
                "example": empathy_example,
            })

        # Hedging
        if derived.hedge_frequency > 0.3:
            hedge_example = next(
                (s.text_snippet for s in signals if s.hedge_phrases > 0), ""
            )
            evidence.append({
                "metric": "High hedge frequency",
                "value": f"{derived.hedge_frequency:.2f} per response ({raw.hedge_phrase_count} total)",
                "example": hedge_example,
            })

        # Apologies
        if derived.apology_frequency > 0.2:
            apology_example = next(
                (s.text_snippet for s in signals if s.apology_phrases > 0), ""
            )
            evidence.append({
                "metric": "Frequent apologies",
                "value": f"{derived.apology_frequency:.2f} per response ({raw.apology_phrase_count} total)",
                "example": apology_example,
            })

        # Refusals
        if raw.refusal_count > 0:
            refusal_example = next(
                (s.text_snippet for s in signals if s.is_refusal), ""
            )
            evidence.append({
                "metric": "Refusal behavior",
                "value": f"{derived.refusal_rate:.0%} refusal rate, {derived.refusal_redirect_rate:.0%} include redirects",
                "example": refusal_example,
            })

        return evidence
