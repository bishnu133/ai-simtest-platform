"""
P3 #19 — Pattern Analyzer

Two sub-parts:
  (A) Repetition detection — repeated openings, closings, apologies, disclaimers
  (B) Signature phrase extraction — top n-grams, phrase clusters, preferred templates

Also measures interaction style: question-asking, list usage, response structure.

Consumes TurnSignals + PhraseStats from shared extractors.
"""

from __future__ import annotations

from ..models import (
    TurnSignals,
    PatternProfile,
    PatternRawMetrics,
    PatternDerivedScores,
    RepeatedPhrase,
)
from ..extractors.phrase_features import PhraseFeatureExtractor, PhraseStats
from ..thresholds import SignatureThresholds, compute_reliability


class PatternAnalyzer:
    """Analyzes behavioral patterns from turn signals and phrase stats."""

    def __init__(self, thresholds: SignatureThresholds | None = None):
        self._thresholds = thresholds or SignatureThresholds()
        self._phrase_extractor = PhraseFeatureExtractor()

    def analyze(
        self,
        signals: list[TurnSignals],
        bot_responses: list[str],
    ) -> PatternProfile:
        """
        Produce a PatternProfile from turn signals and raw response texts.

        Args:
            signals: Pre-extracted turn signals.
            bot_responses: Raw bot response texts (for phrase extraction).
        """
        n = len(signals)
        if not signals:
            return PatternProfile(
                reliability=compute_reliability(0, self._thresholds.min_responses_for_patterns),
            )

        # Extract phrase stats from raw texts
        phrase_stats = self._phrase_extractor.extract_batch(bot_responses)

        # Raw metrics
        raw = PatternRawMetrics(
            total_responses=n,
            unique_opening_phrases=len(phrase_stats.opening_phrases),
            unique_closing_phrases=len(phrase_stats.closing_phrases),
            total_unique_ngrams=len(phrase_stats.top_bigrams) + len(phrase_stats.top_trigrams),
            question_asking_responses=sum(1 for s in signals if s.question_count > 0),
            list_usage_responses=sum(1 for s in signals if s.has_list or s.has_numbered_list),
            multi_section_responses=sum(1 for s in signals if s.section_count > 0),
        )

        # Derived scores
        derived = self._compute_derived(raw, n, phrase_stats, bot_responses)

        # Evidence
        evidence = self._collect_evidence(derived, phrase_stats, n)

        # Reliability
        extra_notes = []
        if n < self._thresholds.min_responses_for_ngrams:
            extra_notes.append("Too few responses for statistically significant n-gram analysis")

        reliability = compute_reliability(
            n,
            self._thresholds.min_responses_for_patterns,
            extra_notes=extra_notes,
        )

        return PatternProfile(
            raw=raw,
            derived=derived,
            reliability=reliability,
            evidence=evidence,
        )

    def _compute_derived(
        self,
        raw: PatternRawMetrics,
        n: int,
        phrase_stats: PhraseStats,
        bot_responses: list[str],
    ) -> PatternDerivedScores:
        """Compute interpreted pattern labels."""

        # Repetition score
        repetition_score = self._phrase_extractor.compute_repetition_score(
            bot_responses, phrase_stats
        )

        # Top repeated phrases (openings)
        top_openings = []
        for phrase, count in phrase_stats.opening_phrases.most_common(5):
            if count >= 2:
                top_openings.append(RepeatedPhrase(
                    phrase=phrase,
                    count=count,
                    percentage=count / n * 100,
                    category="opening",
                ))

        # Top repeated phrases (closings)
        top_closings = []
        for phrase, count in phrase_stats.closing_phrases.most_common(5):
            if count >= 2:
                top_closings.append(RepeatedPhrase(
                    phrase=phrase,
                    count=count,
                    percentage=count / n * 100,
                    category="closing",
                ))

        # Top repeated general phrases (trigrams)
        top_repeated = []
        for phrase, count in phrase_stats.top_trigrams[:10]:
            if count >= 2:
                top_repeated.append(RepeatedPhrase(
                    phrase=phrase,
                    count=count,
                    percentage=count / n * 100,
                    category="general",
                ))

        # Signature phrases
        sig_tuples = self._phrase_extractor.identify_signature_phrases(
            phrase_stats, n
        )
        signature_phrases = [
            RepeatedPhrase(
                phrase=phrase,
                count=count,
                percentage=pct * 100,
                category="signature",
            )
            for phrase, count, pct in sig_tuples
        ]

        # Interaction style
        question_rate = raw.question_asking_responses / n if n > 0 else 0.0
        list_rate = raw.list_usage_responses / n if n > 0 else 0.0
        section_rate = raw.multi_section_responses / n if n > 0 else 0.0

        return PatternDerivedScores(
            repetition_score=repetition_score,
            top_repeated_phrases=top_repeated,
            top_openings=top_openings,
            top_closings=top_closings,
            question_asking_rate=question_rate,
            list_usage_rate=list_rate,
            multi_section_rate=section_rate,
            signature_phrases=signature_phrases,
        )

    def _collect_evidence(
        self,
        derived: PatternDerivedScores,
        phrase_stats: PhraseStats,
        n: int,
    ) -> list[dict[str, str]]:
        """Collect evidence snippets for the report."""
        evidence = []

        # Repetition score
        if derived.repetition_score > 0.3:
            label = "High" if derived.repetition_score > 0.6 else "Moderate"
            evidence.append({
                "metric": f"{label} repetition",
                "value": f"Score: {derived.repetition_score:.2f}",
                "example": "",
            })

        # Top openings
        for op in derived.top_openings[:2]:
            evidence.append({
                "metric": "Repeated opening",
                "value": f"Used in {op.percentage:.0f}% of responses ({op.count}/{n})",
                "example": f'"{op.phrase}"',
            })

        # Top closings
        for cl in derived.top_closings[:2]:
            evidence.append({
                "metric": "Repeated closing",
                "value": f"Used in {cl.percentage:.0f}% of responses ({cl.count}/{n})",
                "example": f'"{cl.phrase}"',
            })

        # Interaction style
        if derived.question_asking_rate > 0.3:
            evidence.append({
                "metric": "Frequent question-asking",
                "value": f"{derived.question_asking_rate:.0%} of responses ask questions",
                "example": "",
            })

        if derived.list_usage_rate > 0.3:
            evidence.append({
                "metric": "List-heavy responses",
                "value": f"{derived.list_usage_rate:.0%} of responses use lists",
                "example": "",
            })

        return evidence
