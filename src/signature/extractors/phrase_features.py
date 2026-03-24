"""
P3 #19 — Shared Phrase Feature Extractor

N-gram extraction, opening/closing phrase detection, crutch phrase identification.
Operates on collections of responses (not single responses).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class PhraseStats:
    """Aggregated phrase statistics across all responses."""
    # Top n-grams (2-gram and 3-gram)
    top_bigrams: list[tuple[str, int]] = field(default_factory=list)
    top_trigrams: list[tuple[str, int]] = field(default_factory=list)

    # Opening phrases (first sentence of each response)
    opening_phrases: Counter = field(default_factory=Counter)

    # Closing phrases (last sentence of each response)
    closing_phrases: Counter = field(default_factory=Counter)

    # All normalized first-N-words
    opening_stems: Counter = field(default_factory=Counter)
    closing_stems: Counter = field(default_factory=Counter)


class PhraseFeatureExtractor:
    """
    Extracts phrase-level features across a batch of responses.

    Call `extract_batch()` once with all bot response texts.
    """

    _WORD_RE = re.compile(r"[a-zA-Z0-9]+(?:'[a-zA-Z]+)?")

    def extract_batch(
        self,
        responses: list[str],
        max_ngrams: int = 15,
        stem_words: int = 5,
    ) -> PhraseStats:
        """
        Extract phrase-level features from a list of bot responses.

        Args:
            responses: All bot response texts.
            max_ngrams: Max top n-grams to return.
            stem_words: Number of leading/trailing words for opening/closing stems.
        """
        if not responses:
            return PhraseStats()

        bigram_counter: Counter = Counter()
        trigram_counter: Counter = Counter()
        opening_phrases: Counter = Counter()
        closing_phrases: Counter = Counter()
        opening_stems: Counter = Counter()
        closing_stems: Counter = Counter()

        for text in responses:
            text = text.strip()
            if not text:
                continue

            words = self._WORD_RE.findall(text.lower())

            # N-grams: count each phrase at most ONCE per response
            seen_bigrams: set[str] = set()
            for i in range(len(words) - 1):
                bg = " ".join(words[i:i+2])
                if bg not in seen_bigrams:
                    bigram_counter[bg] += 1
                    seen_bigrams.add(bg)

            seen_trigrams: set[str] = set()
            for i in range(len(words) - 2):
                tg = " ".join(words[i:i+3])
                if tg not in seen_trigrams:
                    trigram_counter[tg] += 1
                    seen_trigrams.add(tg)

            # Opening/closing sentences
            sentences = self._split_to_sentences(text)
            if sentences:
                opening = self._normalize_phrase(sentences[0])
                closing = self._normalize_phrase(sentences[-1])
                if opening:
                    opening_phrases[opening] += 1
                if closing:
                    closing_phrases[closing] += 1

            # Opening/closing stems (first/last N words)
            if len(words) >= stem_words:
                o_stem = " ".join(words[:stem_words])
                c_stem = " ".join(words[-stem_words:])
                opening_stems[o_stem] += 1
                closing_stems[c_stem] += 1

        # Filter to meaningful n-grams (appeared 2+ times)
        top_bigrams = [
            (phrase, count)
            for phrase, count in bigram_counter.most_common(max_ngrams * 2)
            if count >= 2
        ][:max_ngrams]

        top_trigrams = [
            (phrase, count)
            for phrase, count in trigram_counter.most_common(max_ngrams * 2)
            if count >= 2
        ][:max_ngrams]

        return PhraseStats(
            top_bigrams=top_bigrams,
            top_trigrams=top_trigrams,
            opening_phrases=opening_phrases,
            closing_phrases=closing_phrases,
            opening_stems=opening_stems,
            closing_stems=closing_stems,
        )

    def compute_repetition_score(
        self,
        responses: list[str],
        phrase_stats: PhraseStats,
    ) -> float:
        """
        Compute a repetition score 0-1.

        Higher = more repetitive. Based on:
        - How concentrated the top opening/closing patterns are
        - How many responses share the same n-gram phrases
        """
        if len(responses) < 2:
            return 0.0

        n = len(responses)
        scores = []

        # Opening concentration
        if phrase_stats.opening_stems:
            top_opening_count = phrase_stats.opening_stems.most_common(1)[0][1]
            scores.append(top_opening_count / n)

        # Closing concentration
        if phrase_stats.closing_stems:
            top_closing_count = phrase_stats.closing_stems.most_common(1)[0][1]
            scores.append(top_closing_count / n)

        # Trigram repetition: what fraction of responses contain the top trigram
        if phrase_stats.top_trigrams:
            top_trigram_count = phrase_stats.top_trigrams[0][1]
            scores.append(min(top_trigram_count / n, 1.0))

        if not scores:
            return 0.0

        return sum(scores) / len(scores)

    def identify_signature_phrases(
        self,
        phrase_stats: PhraseStats,
        total_responses: int,
        min_frequency: float = 0.15,
        max_phrases: int = 10,
    ) -> list[tuple[str, int, float]]:
        """
        Identify distinctive signature phrases.

        These are n-grams that appear in at least `min_frequency` of responses,
        making them characteristic of this bot's style.

        Returns list of (phrase, count, percentage).
        """
        if total_responses < 2:
            return []

        candidates = []
        for phrase, count in phrase_stats.top_trigrams:
            pct = count / total_responses
            if pct >= min_frequency:
                candidates.append((phrase, count, pct))

        # Also check bigrams but only highly repeated ones
        for phrase, count in phrase_stats.top_bigrams:
            pct = count / total_responses
            if pct >= min_frequency * 1.5:  # higher bar for shorter phrases
                candidates.append((phrase, count, pct))

        # Sort by percentage descending, take top N
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[:max_phrases]

    def _split_to_sentences(self, text: str) -> list[str]:
        """Simple sentence split for phrase extraction."""
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        return [s.strip() for s in sentences if s.strip()]

    def _normalize_phrase(self, phrase: str) -> str:
        """Normalize a phrase for comparison: lowercase, strip punctuation, limit length."""
        normalized = phrase.lower().strip()
        # Remove trailing punctuation
        normalized = re.sub(r'[.!?]+$', '', normalized).strip()
        # Limit to ~60 chars for readable comparison
        if len(normalized) > 60:
            normalized = normalized[:60]
        return normalized
