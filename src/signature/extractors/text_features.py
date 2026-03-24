"""
P3 #19 — Shared Text Feature Extractor

Tokenization, sentence splitting, word counts, formality scoring.
Computed once per response, reused by all analyzers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Keyword dictionaries ─────────────────────────────────────

EMPATHY_PHRASES = [
    "i understand", "i'm sorry to hear", "i apologize", "that must be",
    "i can see how", "i appreciate your patience", "thank you for sharing",
    "i hear you", "that sounds", "i'm here to help", "let me help",
    "i want to make sure", "i completely understand",
]

HEDGE_PHRASES = [
    "it seems", "it appears", "might be", "could be", "perhaps",
    "it's possible", "may want to", "you might", "generally speaking",
    "in most cases", "typically", "usually", "it depends", "not entirely sure",
    "i believe", "i think", "if i'm not mistaken",
]

DIRECTIVE_PHRASES = [
    "please do", "you need to", "you should", "you must", "make sure",
    "i recommend", "i suggest", "the best option", "go ahead and",
    "you'll want to", "the next step is", "here's what to do",
]

APOLOGY_PHRASES = [
    "i apologize", "i'm sorry", "sorry about", "sorry for the",
    "we apologize", "my apologies", "pardon", "forgive",
    "sorry to hear", "regret",
]

POSITIVE_MARKERS = [
    "great", "excellent", "perfect", "wonderful", "happy to help",
    "glad", "pleased", "absolutely", "certainly", "of course",
    "no problem", "welcome", "fantastic", "awesome",
]

NEGATIVE_MARKERS = [
    "unfortunately", "unable to", "cannot", "i'm afraid",
    "not possible", "regrettably", "not available", "denied",
    "we don't", "we can't", "not supported", "not allowed",
]

REFUSAL_INDICATORS = [
    "i can't help with", "i'm not able to", "i cannot assist",
    "that's outside", "beyond my scope", "not something i can",
    "i'm unable to provide", "i don't have access",
    "for security reasons", "i'm not authorized",
    "please contact", "you'll need to speak with",
]

FORMAL_INDICATORS = [
    "furthermore", "regarding", "in accordance", "pursuant",
    "kindly", "we would like to inform", "please be advised",
    "with respect to", "as per", "hereafter", "herein",
    "notwithstanding", "aforementioned",
]

INFORMAL_INDICATORS = [
    "hey", "yeah", "yep", "nope", "gonna", "wanna",
    "cool", "awesome", "no worries", "sure thing",
    "got it", "btw", "fyi", "lol", "haha",
]


@dataclass
class TextFeatures:
    """Extracted text features for a single response."""
    text: str
    word_count: int = 0
    sentence_count: int = 0
    avg_words_per_sentence: float = 0.0
    unique_words: int = 0
    unique_word_ratio: float = 0.0
    words: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)
    text_lower: str = ""


class TextFeatureExtractor:
    """
    Extracts basic text features from a response string.

    Computed once, results shared across all analyzers.
    """

    # Sentence splitting: period/question/exclamation followed by space+capital or end
    _SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$')
    # Word tokenizer: sequences of alphanumeric + apostrophes
    _WORD_RE = re.compile(r"[a-zA-Z0-9]+(?:'[a-zA-Z]+)?")

    def extract(self, text: str) -> TextFeatures:
        """Extract text features from a single response."""
        if not text or not text.strip():
            return TextFeatures(text=text)

        text_lower = text.lower().strip()
        words = self._WORD_RE.findall(text_lower)
        word_count = len(words)

        # Sentence splitting
        sentences = self._split_sentences(text.strip())
        sentence_count = len(sentences) if sentences else 1

        unique_words = len(set(words))
        unique_word_ratio = unique_words / word_count if word_count > 0 else 0.0

        avg_wps = word_count / sentence_count if sentence_count > 0 else 0.0

        return TextFeatures(
            text=text,
            word_count=word_count,
            sentence_count=sentence_count,
            avg_words_per_sentence=avg_wps,
            unique_words=unique_words,
            unique_word_ratio=unique_word_ratio,
            words=words,
            sentences=sentences,
            text_lower=text_lower,
        )

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        # Handle bullet/numbered list items as separate sentences
        lines = text.split("\n")
        sentences = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # List items are their own sentences
            if re.match(r'^[-*•]\s|^\d+[.)]\s', line):
                sentences.append(line)
                continue
            # Split by sentence-ending punctuation
            parts = self._SENTENCE_RE.split(line)
            for p in parts:
                p = p.strip()
                if p:
                    sentences.append(p)
        return sentences if sentences else [text.strip()]

    def count_phrase_matches(self, text_lower: str, phrases: list[str]) -> int:
        """Count how many distinct phrases from the list are present in the text.

        Note: counts presence (0 or 1 per phrase), not total occurrences.
        For example, if 'i understand' appears 3 times, it still contributes 1.
        """
        count = 0
        for phrase in phrases:
            if phrase in text_lower:
                count += 1
        return count

    def compute_formality_score(self, text_lower: str, words: list[str]) -> float:
        """
        Compute formality score 0-1.

        Uses ratio of formal vs informal indicators plus
        average word length as a proxy for complexity.
        """
        if not words:
            return 0.5

        formal_count = self.count_phrase_matches(text_lower, FORMAL_INDICATORS)
        informal_count = self.count_phrase_matches(text_lower, INFORMAL_INDICATORS)

        # Average word length (longer words → more formal)
        avg_word_len = sum(len(w) for w in words) / len(words)
        length_score = min(avg_word_len / 8.0, 1.0)  # normalize: 8+ chars = max

        # Indicator score
        total_indicators = formal_count + informal_count
        if total_indicators > 0:
            indicator_score = formal_count / total_indicators
        else:
            indicator_score = 0.5

        # Weighted combination
        return 0.6 * indicator_score + 0.4 * length_score

    def detect_refusal(self, text_lower: str) -> tuple[bool, bool]:
        """
        Detect if response is a refusal and whether it includes a redirect.

        Returns (is_refusal, has_redirect).
        """
        refusal_count = self.count_phrase_matches(text_lower, REFUSAL_INDICATORS)
        is_refusal = refusal_count >= 1

        has_redirect = False
        if is_refusal:
            redirect_phrases = [
                "please contact", "you can reach", "visit our",
                "call us", "speak with", "try", "instead",
                "alternatively", "another option",
            ]
            has_redirect = self.count_phrase_matches(text_lower, redirect_phrases) >= 1

        return is_refusal, has_redirect

    def detect_structure(self, text: str) -> tuple[bool, bool, int, int]:
        """
        Detect structural features in a response.

        Returns (has_list, has_numbered_list, question_count, section_count).
        """
        has_list = bool(re.search(r'^[-*•]\s', text, re.MULTILINE))
        has_numbered_list = bool(re.search(r'^\d+[.)]\s', text, re.MULTILINE))
        question_count = text.count("?")
        # Section headers: lines that are short, capitalized, possibly with # or bold markers
        section_count = len(re.findall(r'^#{1,3}\s|^\*\*[^*]+\*\*\s*$', text, re.MULTILINE))
        return has_list, has_numbered_list, question_count, section_count
