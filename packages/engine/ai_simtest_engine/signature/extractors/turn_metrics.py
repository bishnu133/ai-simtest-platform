"""
P3 #19 — Turn Metrics Extractor

Converts raw conversations into TurnSignals — the atomic unit of analysis.
Uses TextFeatureExtractor for all text parsing (no duplicate tokenization).
"""

from __future__ import annotations

from ..models import TurnSignals
from ..utils import truncate_text
from .text_features import (
    TextFeatureExtractor,
    EMPATHY_PHRASES,
    HEDGE_PHRASES,
    DIRECTIVE_PHRASES,
    APOLOGY_PHRASES,
    POSITIVE_MARKERS,
    NEGATIVE_MARKERS,
)


class TurnMetricsExtractor:
    """
    Extracts TurnSignals from conversations.

    This is the bridge between raw conversation data and the analyzer layer.
    Accepts generic turn data (speaker + message) to avoid tight coupling
    with specific model classes.
    """

    def __init__(self):
        self._text_extractor = TextFeatureExtractor()

    def extract_from_conversations(
        self,
        conversations: list[dict],
    ) -> list[TurnSignals]:
        """
        Extract TurnSignals from a list of conversation dicts.

        Each conversation dict should have:
          - "id": str
          - "turns": list of {"speaker": str, "message": str}

        Only bot turns are analyzed (speaker == "bot").
        """
        all_signals = []
        for conv in conversations:
            conv_id = conv.get("id", "unknown")
            turns = conv.get("turns", [])
            bot_turn_idx = 0
            for turn in turns:
                if turn.get("speaker", "").lower() == "bot":
                    signal = self._extract_single(
                        text=turn.get("message", ""),
                        conversation_id=conv_id,
                        turn_index=bot_turn_idx,
                    )
                    all_signals.append(signal)
                    bot_turn_idx += 1
        return all_signals

    def extract_from_judged_conversations(
        self,
        judged_conversations: list,
    ) -> list[TurnSignals]:
        """
        Extract TurnSignals from JudgedConversation objects.

        Accepts our internal JudgedConversation model objects.
        Accesses .conversation.id, .conversation.turns, turn.speaker, turn.message.
        """
        all_signals = []
        for jc in judged_conversations:
            conv = jc.conversation if hasattr(jc, "conversation") else jc
            conv_id = getattr(conv, "id", "unknown")
            turns = getattr(conv, "turns", [])
            bot_turn_idx = 0
            for turn in turns:
                speaker = getattr(turn, "speaker", "").lower()
                if speaker == "bot":
                    message = getattr(turn, "message", "")
                    signal = self._extract_single(
                        text=message,
                        conversation_id=conv_id,
                        turn_index=bot_turn_idx,
                    )
                    all_signals.append(signal)
                    bot_turn_idx += 1
        return all_signals

    def _extract_single(
        self,
        text: str,
        conversation_id: str,
        turn_index: int,
    ) -> TurnSignals:
        """Extract all signals from a single bot response."""
        features = self._text_extractor.extract(text)
        ext = self._text_extractor

        # Tone markers
        positive = ext.count_phrase_matches(features.text_lower, POSITIVE_MARKERS)
        negative = ext.count_phrase_matches(features.text_lower, NEGATIVE_MARKERS)
        empathy = ext.count_phrase_matches(features.text_lower, EMPATHY_PHRASES)
        hedge = ext.count_phrase_matches(features.text_lower, HEDGE_PHRASES)
        directive = ext.count_phrase_matches(features.text_lower, DIRECTIVE_PHRASES)
        apology = ext.count_phrase_matches(features.text_lower, APOLOGY_PHRASES)

        # Neutral: if neither positive nor negative markers dominate
        total_sentiment = positive + negative
        neutral = 1 if total_sentiment == 0 else 0

        # Formality
        formality = ext.compute_formality_score(features.text_lower, features.words)

        # Refusal
        is_refusal, has_redirect = ext.detect_refusal(features.text_lower)

        # Structure
        has_list, has_numbered, question_count, section_count = ext.detect_structure(text)

        return TurnSignals(
            turn_index=turn_index,
            conversation_id=conversation_id,
            word_count=features.word_count,
            sentence_count=features.sentence_count,
            avg_words_per_sentence=features.avg_words_per_sentence,
            unique_word_ratio=features.unique_word_ratio,
            positive_markers=positive,
            negative_markers=negative,
            neutral_indicators=neutral,
            empathy_phrases=empathy,
            hedge_phrases=hedge,
            directive_phrases=directive,
            apology_phrases=apology,
            formality_score=formality,
            is_refusal=is_refusal,
            refusal_has_redirect=has_redirect,
            has_list=has_list,
            has_numbered_list=has_numbered,
            question_count=question_count,
            section_count=section_count,
            text_snippet=truncate_text(text, 200),
        )
