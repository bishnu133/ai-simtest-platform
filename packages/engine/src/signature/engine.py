"""
P3 #19 — Signature Engine

Orchestrates all analyzers in the correct order:
  1. Extract turn signals (shared)
  2. Run tone, verbosity, pattern analyzers (parallel-capable)
  3. Run consistency meta-analyzer (consumes other 3)
  4. Detect anomalies (hybrid: z-score + percentile + rule-based)
  5. Generate deterministic summary
  6. Assemble BotSignature
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    TurnSignals,
    BotSignature,
    AnomalyFlag,
    AnomalyType,
    ReliabilityInfo,
    Confidence,
)
from .extractors.turn_metrics import TurnMetricsExtractor
from .extractors.phrase_features import PhraseFeatureExtractor
from .analyzers.tone_analyzer import ToneAnalyzer
from .analyzers.verbosity_analyzer import VerbosityAnalyzer
from .analyzers.pattern_analyzer import PatternAnalyzer
from .analyzers.consistency_analyzer import ConsistencyAnalyzer
from .thresholds import SignatureThresholds, DEFAULT_THRESHOLDS, compute_reliability
from .summary import generate_summary
from .utils import safe_mean, safe_stdev, safe_median, safe_percentile


class SignatureEngine:
    """
    Main orchestrator for behavioral signature analysis.

    Loosely coupled: accepts either JudgedConversation objects or
    raw conversation dicts. Future UI calls this directly.
    """

    def __init__(self, thresholds: SignatureThresholds | None = None):
        self._thresholds = thresholds or DEFAULT_THRESHOLDS
        self._turn_extractor = TurnMetricsExtractor()
        self._tone = ToneAnalyzer(self._thresholds)
        self._verbosity = VerbosityAnalyzer(self._thresholds)
        self._pattern = PatternAnalyzer(self._thresholds)
        self._consistency = ConsistencyAnalyzer(self._thresholds)

    def analyze(
        self,
        judged_conversations: list | None = None,
        raw_conversations: list[dict] | None = None,
        persona_map: dict[str, str] | None = None,
        run_id: str = "",
    ) -> BotSignature:
        """
        Run full behavioral signature analysis.

        Args:
            judged_conversations: List of JudgedConversation objects (from simulation).
            raw_conversations: Alternative: list of {"id": str, "turns": [{"speaker", "message"}]}.
            persona_map: Optional {conversation_id: persona_type} for consistency analysis.
            run_id: Identifier for this run.

        Exactly one of judged_conversations or raw_conversations should be provided.

        Raises:
            ValueError: If both judged_conversations and raw_conversations are provided.
        """
        # Validate input: exactly one source
        if judged_conversations and raw_conversations:
            raise ValueError(
                "Provide exactly one of judged_conversations or raw_conversations, not both."
            )

        # Step 1: Extract turn signals
        if judged_conversations:
            signals = self._turn_extractor.extract_from_judged_conversations(
                judged_conversations
            )
            bot_responses = self._extract_bot_texts_from_judged(judged_conversations)
            # Build persona map if not provided
            if not persona_map:
                persona_map = self._build_persona_map(judged_conversations)
        elif raw_conversations:
            signals = self._turn_extractor.extract_from_conversations(
                raw_conversations
            )
            bot_responses = self._extract_bot_texts_from_raw(raw_conversations)
        else:
            return BotSignature(
                run_id=run_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reliability=compute_reliability(0, self._thresholds.min_responses_for_tone),
            )

        if not signals:
            return BotSignature(
                run_id=run_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reliability=compute_reliability(0, self._thresholds.min_responses_for_tone),
            )

        # Step 2: Run tone, verbosity, pattern analyzers
        tone_profile = self._tone.analyze(signals)
        verbosity_profile = self._verbosity.analyze(signals)
        pattern_profile = self._pattern.analyze(signals, bot_responses)

        # Step 3: Run consistency meta-analyzer
        consistency_profile = self._consistency.analyze(
            signals=signals,
            tone_profile=tone_profile,
            verbosity_profile=verbosity_profile,
            pattern_profile=pattern_profile,
            persona_map=persona_map,
        )

        # Step 4: Detect anomalies
        anomalies = self._detect_anomalies(signals)

        # Step 5: Generate summary
        summary_text = generate_summary(
            tone_profile, verbosity_profile, pattern_profile,
            consistency_profile, anomalies,
        )

        # Count unique conversations
        conv_ids = set(s.conversation_id for s in signals)

        # Overall reliability: minimum of all analyzer reliabilities
        min_confidence = min(
            tone_profile.reliability.confidence.value,
            verbosity_profile.reliability.confidence.value,
            pattern_profile.reliability.confidence.value,
            consistency_profile.reliability.confidence.value,
            key=lambda x: {"high": 2, "medium": 1, "low": 0}.get(x, 0),
        )
        # Deduplicate reliability notes (preserving order)
        all_notes = [
            n for profile in [tone_profile, verbosity_profile, pattern_profile, consistency_profile]
            for n in profile.reliability.notes
        ]
        unique_notes = list(dict.fromkeys(all_notes))

        overall_reliability = ReliabilityInfo(
            confidence=Confidence(min_confidence),
            sample_count=len(signals),
            notes=unique_notes,
        )

        return BotSignature(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            conversation_count=len(conv_ids),
            turn_count=len(signals),
            tone=tone_profile,
            verbosity=verbosity_profile,
            patterns=pattern_profile,
            consistency=consistency_profile,
            anomalies=anomalies,
            anomaly_count=len(anomalies),
            summary_text=summary_text,
            reliability=overall_reliability,
        )

    def _detect_anomalies(self, signals: list[TurnSignals]) -> list[AnomalyFlag]:
        """
        Hybrid anomaly detection:
          - Z-score for roughly symmetric metrics (formality)
          - Percentile threshold for skewed data (word counts, hedge counts)
          - Rule-based triggers for known high-risk patterns
        """
        if len(signals) < self._thresholds.min_responses_for_anomaly:
            return []

        anomalies = []
        word_counts = [s.word_count for s in signals]
        formalities = [s.formality_score for s in signals]
        hedge_counts = [s.hedge_phrases for s in signals]

        mean_wc = safe_mean(word_counts)
        std_wc = safe_stdev(word_counts)
        median_wc = safe_median(word_counts)
        mean_form = safe_mean(formalities)
        std_form = safe_stdev(formalities)

        # Percentile thresholds for skewed metrics (word count is often right-skewed)
        p95_wc = safe_percentile(word_counts, 95)
        p5_wc = safe_percentile(word_counts, 5)
        p95_hedge = safe_percentile(hedge_counts, 95)

        for s in signals:
            flagged_length = False

            # Rule-based: response > 3× median length
            if median_wc > 0 and s.word_count > 3 * median_wc:
                anomalies.append(AnomalyFlag(
                    anomaly_type=AnomalyType.LENGTH_OUTLIER,
                    conversation_id=s.conversation_id,
                    turn_index=s.turn_index,
                    description=f"Response is {s.word_count} words (median: {median_wc:.0f})",
                    metric_value=s.word_count,
                    baseline_value=median_wc,
                    text_snippet=s.text_snippet,
                    detection_method="rule_based",
                ))
                flagged_length = True

            # Percentile: word count beyond p95 (handles right-skewed distributions)
            if not flagged_length and p95_wc > 0 and s.word_count > p95_wc * 1.5:
                anomalies.append(AnomalyFlag(
                    anomaly_type=AnomalyType.LENGTH_OUTLIER,
                    conversation_id=s.conversation_id,
                    turn_index=s.turn_index,
                    description=f"Response {s.word_count} words exceeds 1.5× p95 ({p95_wc:.0f})",
                    metric_value=s.word_count,
                    baseline_value=p95_wc,
                    text_snippet=s.text_snippet,
                    detection_method="percentile",
                ))
                flagged_length = True

            # Z-score: word count > 2.5 std from mean (for roughly normal distributions)
            if not flagged_length and std_wc > 0 and abs(s.word_count - mean_wc) > 2.5 * std_wc:
                anomalies.append(AnomalyFlag(
                    anomaly_type=AnomalyType.LENGTH_OUTLIER,
                    conversation_id=s.conversation_id,
                    turn_index=s.turn_index,
                    description=f"Response length {s.word_count} words (z-score: {(s.word_count - mean_wc) / std_wc:.1f})",
                    metric_value=s.word_count,
                    baseline_value=mean_wc,
                    text_snippet=s.text_snippet,
                    detection_method="z_score",
                ))

            # Z-score: formality shift (roughly symmetric metric)
            if std_form > 0 and abs(s.formality_score - mean_form) > 2.5 * std_form:
                anomalies.append(AnomalyFlag(
                    anomaly_type=AnomalyType.FORMALITY_SHIFT,
                    conversation_id=s.conversation_id,
                    turn_index=s.turn_index,
                    description=f"Formality {s.formality_score:.2f} vs avg {mean_form:.2f}",
                    metric_value=s.formality_score,
                    baseline_value=mean_form,
                    text_snippet=s.text_snippet,
                    detection_method="z_score",
                ))

            # Rule-based: excessive apology (4+ apology phrases in one response)
            if s.apology_phrases >= 4:
                anomalies.append(AnomalyFlag(
                    anomaly_type=AnomalyType.EXCESSIVE_APOLOGY,
                    conversation_id=s.conversation_id,
                    turn_index=s.turn_index,
                    description=f"{s.apology_phrases} apology phrases in one response",
                    metric_value=s.apology_phrases,
                    baseline_value=0,
                    text_snippet=s.text_snippet,
                    detection_method="rule_based",
                ))

            # Rule-based: excessive hedging (3+ or beyond p95 × 1.5)
            hedge_threshold = max(3, p95_hedge * 1.5) if p95_hedge > 0 else 3
            if s.hedge_phrases >= hedge_threshold:
                anomalies.append(AnomalyFlag(
                    anomaly_type=AnomalyType.EXCESSIVE_HEDGING,
                    conversation_id=s.conversation_id,
                    turn_index=s.turn_index,
                    description=f"{s.hedge_phrases} hedge phrases in one response (threshold: {hedge_threshold:.0f})",
                    metric_value=s.hedge_phrases,
                    baseline_value=hedge_threshold,
                    text_snippet=s.text_snippet,
                    detection_method="rule_based" if hedge_threshold == 3 else "percentile",
                ))

        return anomalies

    def _extract_bot_texts_from_judged(self, judged_conversations: list) -> list[str]:
        """Extract raw bot response texts from JudgedConversation objects."""
        texts = []
        for jc in judged_conversations:
            conv = jc.conversation if hasattr(jc, "conversation") else jc
            for turn in getattr(conv, "turns", []):
                if getattr(turn, "speaker", "").lower() == "bot":
                    texts.append(getattr(turn, "message", ""))
        return texts

    def _extract_bot_texts_from_raw(self, conversations: list[dict]) -> list[str]:
        """Extract raw bot response texts from dict-based conversations."""
        texts = []
        for conv in conversations:
            for turn in conv.get("turns", []):
                if turn.get("speaker", "").lower() == "bot":
                    texts.append(turn.get("message", ""))
        return texts

    def _build_persona_map(self, judged_conversations: list) -> dict[str, str]:
        """Build conversation_id → persona_type map from JudgedConversation objects."""
        persona_map = {}
        for jc in judged_conversations:
            conv = jc.conversation if hasattr(jc, "conversation") else jc
            conv_id = getattr(conv, "id", "unknown")
            persona = getattr(jc, "persona", None)
            if persona:
                ptype = getattr(persona, "persona_type", None)
                if ptype:
                    # Handle both enum and string
                    persona_map[conv_id] = ptype.value if hasattr(ptype, "value") else str(ptype)
        return persona_map
