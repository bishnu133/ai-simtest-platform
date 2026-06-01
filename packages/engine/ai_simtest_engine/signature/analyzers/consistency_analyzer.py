"""
P3 #19 — Consistency Analyzer (Meta-Analyzer)

Consumes outputs from Tone, Verbosity, and Pattern analyzers.
Measures behavioral consistency across:
  - Conversations (does the bot behave the same across different chats?)
  - Persona types (does it change behavior for frustrated vs friendly users?)
  - Within conversations (early vs late turn drift)

Does NOT re-parse raw text — works entirely from pre-computed profiles
and per-conversation signal aggregates.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import (
    TurnSignals,
    ToneProfile,
    VerbosityProfile,
    PatternProfile,
    ConsistencyProfile,
    ConsistencyRawMetrics,
    ConsistencyDerivedScores,
    DriftDirection,
)
from ..thresholds import SignatureThresholds, compute_reliability
from ..utils import safe_mean, safe_stdev


class ConsistencyAnalyzer:
    """
    Meta-analyzer: measures behavioral consistency using outputs from
    tone, verbosity, and pattern analyzers.
    """

    def __init__(self, thresholds: SignatureThresholds | None = None):
        self._thresholds = thresholds or SignatureThresholds()

    def analyze(
        self,
        signals: list[TurnSignals],
        tone_profile: ToneProfile,
        verbosity_profile: VerbosityProfile,
        pattern_profile: PatternProfile,
        persona_map: dict[str, str] | None = None,
    ) -> ConsistencyProfile:
        """
        Produce a ConsistencyProfile by comparing metrics across segments.

        Args:
            signals: All turn signals (needed for per-conversation breakdown).
            tone_profile: Output from ToneAnalyzer.
            verbosity_profile: Output from VerbosityAnalyzer.
            pattern_profile: Output from PatternAnalyzer.
            persona_map: Optional {conversation_id: persona_type} mapping.
        """
        persona_map = persona_map or {}

        # Group signals by conversation
        by_conv: dict[str, list[TurnSignals]] = defaultdict(list)
        for s in signals:
            by_conv[s.conversation_id].append(s)

        conv_count = len(by_conv)
        if conv_count < 1:
            return ConsistencyProfile(
                reliability=compute_reliability(0, self._thresholds.min_conversations_for_consistency),
            )

        # Per-conversation metrics
        conv_formalities = []
        conv_verbosities = []
        conv_tone_drifts = []
        conv_verbosity_drifts = []

        for conv_id, conv_signals in by_conv.items():
            if not conv_signals:
                continue

            # Average formality per conversation
            conv_formalities.append(
                safe_mean([s.formality_score for s in conv_signals])
            )

            # Average word count per conversation
            conv_verbosities.append(
                safe_mean([s.word_count for s in conv_signals])
            )

            # Within-conversation drift (if enough turns)
            if len(conv_signals) >= 4:
                half = len(conv_signals) // 2
                early_form = safe_mean([s.formality_score for s in conv_signals[:half]])
                late_form = safe_mean([s.formality_score for s in conv_signals[half:]])
                conv_tone_drifts.append(late_form - early_form)  # signed: +ve = more formal over time

                early_verb = safe_mean([s.word_count for s in conv_signals[:half]])
                late_verb = safe_mean([s.word_count for s in conv_signals[half:]])
                if early_verb > 0:
                    conv_verbosity_drifts.append((late_verb - early_verb) / early_verb)  # signed: +ve = longer over time

        # Raw metrics
        raw = ConsistencyRawMetrics(
            conversation_count=conv_count,
            persona_group_count=len(set(persona_map.values())) if persona_map else 0,
            tone_formality_std_across_conversations=safe_stdev(conv_formalities),
            verbosity_std_across_conversations=safe_stdev(conv_verbosities),
            tone_drift_early_vs_late=safe_mean(conv_tone_drifts),
            verbosity_drift_early_vs_late=safe_mean(conv_verbosity_drifts),
        )

        # Derived scores
        derived = self._compute_derived(
            raw, conv_formalities, conv_verbosities,
            tone_profile, verbosity_profile, pattern_profile,
            persona_map, by_conv,
        )

        # Evidence
        evidence = self._collect_evidence(raw, derived, conv_count)

        # Reliability
        extra_notes = []
        if conv_count < self._thresholds.min_conversations_for_consistency:
            extra_notes.append("Too few conversations for reliable consistency measurement")
        if raw.persona_group_count < self._thresholds.min_persona_groups_for_comparison:
            extra_notes.append("Too few persona types for persona-based comparison")

        reliability = compute_reliability(
            conv_count,
            self._thresholds.min_conversations_for_consistency,
            extra_notes=extra_notes,
        )

        return ConsistencyProfile(
            raw=raw,
            derived=derived,
            reliability=reliability,
            evidence=evidence,
        )

    def _compute_derived(
        self,
        raw: ConsistencyRawMetrics,
        conv_formalities: list[float],
        conv_verbosities: list[float],
        tone_profile: ToneProfile,
        verbosity_profile: VerbosityProfile,
        pattern_profile: PatternProfile,
        persona_map: dict[str, str],
        by_conv: dict[str, list[TurnSignals]],
    ) -> ConsistencyDerivedScores:
        """Compute interpreted consistency labels from analyzer profiles and per-conversation metrics."""

        # Tone consistency: use formality std from tone profile if available,
        # fall back to cross-conversation std
        formality_std = raw.tone_formality_std_across_conversations
        tone_consistency = max(0.0, 1.0 - formality_std * 5)
        tone_consistency = min(tone_consistency, 1.0)

        # Verbosity consistency: use coefficient of variation from per-conversation means
        verb_mean = safe_mean(conv_verbosities) if conv_verbosities else 1.0
        verb_std = safe_stdev(conv_verbosities)
        cv = verb_std / verb_mean if verb_mean > 0 else 0.0
        verbosity_consistency = max(0.0, min(1.0, 1.0 - cv))

        # Pattern consistency: consume pattern profile output directly
        pattern_consistency = pattern_profile.derived.repetition_score

        # Overall consistency: weighted average of normalized sub-scores
        overall = (
            0.35 * tone_consistency
            + 0.35 * verbosity_consistency
            + 0.30 * pattern_consistency
        )

        # Persona-type variations: avg verbosity per persona type
        persona_type_avg_verbosity = {}
        if persona_map:
            by_type: dict[str, list[float]] = defaultdict(list)
            for conv_id, conv_signals in by_conv.items():
                ptype = persona_map.get(conv_id, "unknown")
                by_type[ptype].append(
                    safe_mean([s.word_count for s in conv_signals])
                )
            for ptype, values in by_type.items():
                if len(values) >= 2:
                    persona_type_avg_verbosity[ptype] = safe_mean(values)

        # Within-conversation drift
        drift = DriftDirection.STABLE
        if raw.verbosity_drift_early_vs_late > 0.15:
            drift = DriftDirection.INCREASING
        elif raw.verbosity_drift_early_vs_late < -0.15:
            drift = DriftDirection.DECREASING

        return ConsistencyDerivedScores(
            overall_consistency_score=overall,
            tone_consistency=tone_consistency,
            verbosity_consistency=verbosity_consistency,
            pattern_consistency=pattern_consistency,
            persona_type_avg_verbosity=persona_type_avg_verbosity,
            within_conversation_drift=drift,
        )

    def _collect_evidence(
        self,
        raw: ConsistencyRawMetrics,
        derived: ConsistencyDerivedScores,
        conv_count: int,
    ) -> list[dict[str, str]]:
        """Collect evidence snippets for the report."""
        evidence = []

        # Overall
        level = "high" if derived.overall_consistency_score > 0.7 else (
            "moderate" if derived.overall_consistency_score > 0.4 else "low"
        )
        evidence.append({
            "metric": f"Overall consistency: {level}",
            "value": f"Score: {derived.overall_consistency_score:.2f} across {conv_count} conversations",
            "example": "",
        })

        # Tone consistency
        if derived.tone_consistency < 0.5:
            evidence.append({
                "metric": "Low tone consistency",
                "value": f"Formality std: {raw.tone_formality_std_across_conversations:.3f} across conversations",
                "example": "Bot's formality level varies significantly between different conversations",
            })

        # Persona-type differences
        if derived.persona_type_avg_verbosity:
            sorted_types = sorted(
                derived.persona_type_avg_verbosity.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            if len(sorted_types) >= 2:
                most_verbose = sorted_types[0]
                least_verbose = sorted_types[-1]
                evidence.append({
                    "metric": "Persona-type behavior difference",
                    "value": (
                        f"Most verbose with {most_verbose[0]} ({most_verbose[1]:.0f} avg words), "
                        f"least with {least_verbose[0]} ({least_verbose[1]:.0f} avg words)"
                    ),
                    "example": "",
                })

        return evidence
