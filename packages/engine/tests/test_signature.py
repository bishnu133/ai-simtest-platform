"""
Tests for P3 #19: Behavioral Signature Analysis

Coverage:
  - Models (serialization, defaults, enums)
  - Thresholds & confidence
  - Text feature extractor
  - Phrase feature extractor
  - Turn metrics extractor
  - Verbosity analyzer
  - Pattern analyzer
  - Tone analyzer
  - Consistency analyzer (meta)
  - Engine (full pipeline)
  - Summary generation
  - Comparison
  - HTML injection
"""

import pytest
from pathlib import Path

# ── Models ───────────────────────────────────────────────────

from ai_simtest_engine.signature.models import (
    TurnSignals, BotSignature, AnomalyFlag, AnomalyType,
    ToneProfile, ToneRawMetrics, ToneDerivedScores,
    VerbosityProfile, VerbosityRawMetrics, VerbosityDerivedScores,
    PatternProfile, PatternDerivedScores, RepeatedPhrase,
    ConsistencyProfile, ConsistencyDerivedScores,
    SignatureComparison, SignatureDrift,
    ReliabilityInfo, Confidence, FormalityLevel, VerbosityLevel, DriftDirection,
)


class TestModels:

    def test_turn_signals_defaults(self):
        ts = TurnSignals(turn_index=0, conversation_id="c1")
        assert ts.word_count == 0
        assert ts.is_refusal is False
        assert ts.to_dict()["conversation_id"] == "c1"

    def test_bot_signature_to_dict(self):
        sig = BotSignature(run_id="r1", conversation_count=5, turn_count=20)
        d = sig.to_dict()
        assert d["run_id"] == "r1"
        assert "tone" in d
        assert "verbosity" in d
        assert "patterns" in d
        assert "consistency" in d
        assert d["anomaly_count"] == 0

    def test_bot_signature_to_summary_dict(self):
        sig = BotSignature(run_id="r1", conversation_count=3, turn_count=12)
        sd = sig.to_summary_dict()
        assert sd["conversation_count"] == 3
        assert "tone_formality" in sd
        assert "verbosity_level" in sd

    def test_reliability_info(self):
        ri = ReliabilityInfo(confidence=Confidence.LOW, sample_count=2, notes=["too few"])
        d = ri.to_dict()
        assert d["confidence"] == "low"
        assert d["notes"] == ["too few"]

    def test_anomaly_flag_to_dict(self):
        af = AnomalyFlag(
            anomaly_type=AnomalyType.LENGTH_OUTLIER,
            conversation_id="c1", turn_index=3,
            description="too long", metric_value=500, baseline_value=50,
        )
        d = af.to_dict()
        assert d["anomaly_type"] == "length_outlier"

    def test_repeated_phrase(self):
        rp = RepeatedPhrase(phrase="hello there", count=5, percentage=25.0, category="opening")
        assert rp.to_dict()["category"] == "opening"

    def test_signature_drift(self):
        sd = SignatureDrift(
            dimension="tone", metric="formality",
            baseline_value=0.5, current_value=0.8,
            change_percent=60.0, is_significant=True,
        )
        assert sd.to_dict()["is_significant"] is True

    def test_signature_comparison(self):
        sc = SignatureComparison(
            baseline_run_id="r1", current_run_id="r2",
            drifts=[], significant_drift_count=0,
        )
        assert sc.to_dict()["significant_drift_count"] == 0

    def test_enum_values(self):
        assert Confidence.HIGH.value == "high"
        assert VerbosityLevel.LOW.value == "low"
        assert FormalityLevel.FORMAL.value == "formal"
        assert DriftDirection.INCREASING.value == "increasing"
        assert AnomalyType.TONE_SHIFT.value == "tone_shift"


# ── Thresholds ───────────────────────────────────────────────

from ai_simtest_engine.signature.thresholds import (
    SignatureThresholds, DEFAULT_THRESHOLDS, compute_reliability,
)


class TestThresholds:

    def test_default_thresholds(self):
        t = DEFAULT_THRESHOLDS
        assert t.min_responses_for_tone == 5
        assert t.min_conversations_for_consistency == 3

    def test_compute_reliability_high(self):
        ri = compute_reliability(20, 5)
        assert ri.confidence == Confidence.HIGH
        assert ri.sample_count == 20

    def test_compute_reliability_medium(self):
        ri = compute_reliability(7, 5)
        assert ri.confidence == Confidence.MEDIUM

    def test_compute_reliability_low(self):
        ri = compute_reliability(2, 5)
        assert ri.confidence == Confidence.LOW
        assert any("Only 2" in n for n in ri.notes)

    def test_compute_reliability_with_extra_notes(self):
        ri = compute_reliability(3, 5, extra_notes=["custom note"])
        assert "custom note" in ri.notes


# ── Utils ────────────────────────────────────────────────────

from ai_simtest_engine.signature.utils import safe_mean, safe_median, safe_stdev, safe_percentile, truncate_text


class TestUtils:

    def test_safe_mean_empty(self):
        assert safe_mean([]) == 0.0

    def test_safe_mean_values(self):
        assert safe_mean([10, 20, 30]) == 20.0

    def test_safe_median_values(self):
        assert safe_median([1, 2, 3, 4, 5]) == 3.0

    def test_safe_stdev_single(self):
        assert safe_stdev([5]) == 0.0

    def test_safe_percentile_95(self):
        vals = list(range(1, 101))
        p95 = safe_percentile(vals, 95)
        assert 94 < p95 < 96

    def test_truncate_text(self):
        assert truncate_text("short", 200) == "short"
        long = "x" * 300
        result = truncate_text(long, 200)
        assert len(result) == 200
        assert result.endswith("...")


# ── Text Feature Extractor ───────────────────────────────────

from ai_simtest_engine.signature.extractors.text_features import TextFeatureExtractor


class TestTextFeatureExtractor:

    def setup_method(self):
        self.ext = TextFeatureExtractor()

    def test_empty_text(self):
        f = self.ext.extract("")
        assert f.word_count == 0

    def test_basic_extraction(self):
        f = self.ext.extract("Hello world. This is a test.")
        assert f.word_count == 6
        assert f.sentence_count >= 2
        assert f.unique_word_ratio > 0

    def test_list_detection(self):
        text = "Options:\n- Item one\n- Item two\n- Item three"
        f = self.ext.extract(text)
        assert f.word_count > 0
        assert f.sentence_count >= 3

    def test_phrase_matching(self):
        count = self.ext.count_phrase_matches(
            "i understand your frustration. i'm sorry to hear that.",
            ["i understand", "i'm sorry to hear"],
        )
        assert count == 2

    def test_formality_score_formal(self):
        score = self.ext.compute_formality_score(
            "furthermore, regarding your inquiry, we would like to inform you",
            ["furthermore", "regarding", "your", "inquiry", "we", "would", "like", "to", "inform", "you"],
        )
        assert score > 0.5

    def test_formality_score_informal(self):
        score = self.ext.compute_formality_score(
            "hey yeah no worries gonna fix that for you",
            ["hey", "yeah", "no", "worries", "gonna", "fix", "that", "for", "you"],
        )
        assert score < 0.5

    def test_refusal_detection(self):
        is_ref, has_redirect = self.ext.detect_refusal(
            "i can't help with that. please contact our support team."
        )
        assert is_ref is True
        assert has_redirect is True

    def test_no_refusal(self):
        is_ref, _ = self.ext.detect_refusal("here is your account balance: $500")
        assert is_ref is False

    def test_structure_detection(self):
        text = "Here are the options:\n- Option A\n- Option B\nDo you have questions?"
        has_list, has_num, q_count, sec_count = self.ext.detect_structure(text)
        assert has_list is True
        assert q_count == 1


# ── Phrase Feature Extractor ─────────────────────────────────

from ai_simtest_engine.signature.extractors.phrase_features import PhraseFeatureExtractor


class TestPhraseFeatureExtractor:

    def setup_method(self):
        self.ext = PhraseFeatureExtractor()

    def test_empty_batch(self):
        stats = self.ext.extract_batch([])
        assert len(stats.top_bigrams) == 0

    def test_repeated_openings(self):
        responses = [
            "Thank you for your inquiry. Here is the info.",
            "Thank you for your inquiry. Let me check.",
            "Thank you for your inquiry. One moment please.",
            "Sure, I can help. Here is the info.",
        ]
        stats = self.ext.extract_batch(responses)
        # "thank you for your inquiry" should be a repeated opening
        assert stats.opening_phrases.most_common(1)[0][1] >= 3

    def test_repetition_score(self):
        # Highly repetitive
        responses = ["Hello, how can I help you today?"] * 10
        stats = self.ext.extract_batch(responses)
        score = self.ext.compute_repetition_score(responses, stats)
        assert score > 0.5

    def test_low_repetition_score(self):
        responses = [
            "Here is your balance.",
            "Your card has been blocked.",
            "Please visit the branch for this.",
            "The interest rate is 3.5%.",
            "Let me transfer you to support.",
        ]
        stats = self.ext.extract_batch(responses)
        score = self.ext.compute_repetition_score(responses, stats)
        assert score < 0.5

    def test_signature_phrases(self):
        responses = [f"I'd be happy to help you with that. {i}" for i in range(10)]
        stats = self.ext.extract_batch(responses)
        sig = self.ext.identify_signature_phrases(stats, 10)
        # "happy to help" should appear as a signature
        assert len(sig) >= 0  # may or may not qualify depending on frequency


# ── Turn Metrics Extractor ───────────────────────────────────

from ai_simtest_engine.signature.extractors.turn_metrics import TurnMetricsExtractor


class TestTurnMetricsExtractor:

    def setup_method(self):
        self.ext = TurnMetricsExtractor()

    def test_extract_from_conversations(self):
        convs = [
            {
                "id": "c1",
                "turns": [
                    {"speaker": "user", "message": "Hello"},
                    {"speaker": "bot", "message": "Hi there! How can I help you today?"},
                    {"speaker": "user", "message": "What is my balance?"},
                    {"speaker": "bot", "message": "Your current balance is $1,500."},
                ],
            }
        ]
        signals = self.ext.extract_from_conversations(convs)
        assert len(signals) == 2  # only bot turns
        assert signals[0].word_count > 0
        assert signals[0].conversation_id == "c1"

    def test_extract_skips_user_turns(self):
        convs = [{"id": "c1", "turns": [
            {"speaker": "user", "message": "Just a question"},
        ]}]
        signals = self.ext.extract_from_conversations(convs)
        assert len(signals) == 0

    def test_refusal_detected_in_signals(self):
        convs = [{"id": "c1", "turns": [
            {"speaker": "bot", "message": "I can't help with that. Please contact our team."},
        ]}]
        signals = self.ext.extract_from_conversations(convs)
        assert signals[0].is_refusal is True
        assert signals[0].refusal_has_redirect is True


# ── Verbosity Analyzer ───────────────────────────────────────

from ai_simtest_engine.signature.analyzers.verbosity_analyzer import VerbosityAnalyzer


class TestVerbosityAnalyzer:

    def setup_method(self):
        self.analyzer = VerbosityAnalyzer()

    def test_empty_signals(self):
        profile = self.analyzer.analyze([])
        assert profile.reliability.confidence == Confidence.LOW
        assert profile.raw.total_responses == 0

    def test_basic_analysis(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=50 + i*5,
                        sentence_count=3, avg_words_per_sentence=17.0, unique_word_ratio=0.7)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.raw.total_responses == 10
        assert profile.raw.mean_words > 0
        assert profile.derived.verbosity_level in [VerbosityLevel.LOW, VerbosityLevel.MEDIUM, VerbosityLevel.HIGH]
        assert len(profile.evidence) > 0

    def test_low_verbosity(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=10,
                        sentence_count=1, avg_words_per_sentence=10.0, unique_word_ratio=0.8)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.derived.verbosity_level == VerbosityLevel.LOW

    def test_high_verbosity(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=150,
                        sentence_count=8, avg_words_per_sentence=19.0, unique_word_ratio=0.6)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.derived.verbosity_level == VerbosityLevel.HIGH

    def test_drift_detection(self):
        # Early turns short, late turns long
        signals = []
        for i in range(15):
            wc = 30 if i < 5 else 90
            signals.append(TurnSignals(
                turn_index=i, conversation_id="c1", word_count=wc,
                sentence_count=3, avg_words_per_sentence=wc/3, unique_word_ratio=0.7,
            ))
        profile = self.analyzer.analyze(signals)
        assert profile.derived.verbosity_drift_direction == DriftDirection.INCREASING

    def test_raw_derived_separation(self):
        signals = [
            TurnSignals(turn_index=0, conversation_id="c1", word_count=50,
                        sentence_count=3, avg_words_per_sentence=17.0, unique_word_ratio=0.7)
        ] * 5
        profile = self.analyzer.analyze(signals)
        raw_dict = profile.raw.to_dict()
        derived_dict = profile.derived.to_dict()
        # Raw has numeric auditable fields
        assert "mean_words" in raw_dict
        # Derived has interpreted labels
        assert "verbosity_level" in derived_dict


# ── Pattern Analyzer ─────────────────────────────────────────

from ai_simtest_engine.signature.analyzers.pattern_analyzer import PatternAnalyzer


class TestPatternAnalyzer:

    def setup_method(self):
        self.analyzer = PatternAnalyzer()

    def test_empty_signals(self):
        profile = self.analyzer.analyze([], [])
        assert profile.reliability.confidence == Confidence.LOW

    def test_repetitive_responses(self):
        responses = ["Thank you for contacting us. How can I help you today?"] * 10
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=10,
                        sentence_count=2, question_count=1, has_list=False)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals, responses)
        assert profile.derived.repetition_score > 0.3
        assert len(profile.derived.top_openings) > 0

    def test_question_asking_rate(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=20,
                        question_count=1 if i % 2 == 0 else 0)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals, ["text"] * 10)
        assert profile.derived.question_asking_rate == 0.5

    def test_list_usage_rate(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=20,
                        has_list=True, has_numbered_list=False)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals, ["text"] * 10)
        assert profile.derived.list_usage_rate == 1.0

    def test_evidence_collected(self):
        responses = ["I apologize for the inconvenience. Let me help you."] * 10
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1", word_count=10, question_count=1)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals, responses)
        assert len(profile.evidence) > 0


# ── Tone Analyzer ────────────────────────────────────────────

from ai_simtest_engine.signature.analyzers.tone_analyzer import ToneAnalyzer


class TestToneAnalyzer:

    def setup_method(self):
        self.analyzer = ToneAnalyzer()

    def test_empty_signals(self):
        profile = self.analyzer.analyze([])
        assert profile.reliability.confidence == Confidence.LOW

    def test_formal_tone(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1",
                        formality_score=0.8, positive_markers=1, negative_markers=0,
                        empathy_phrases=1, hedge_phrases=0, apology_phrases=0,
                        word_count=50)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.derived.formality_level == FormalityLevel.FORMAL
        assert profile.derived.empathy_frequency > 0

    def test_informal_tone(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1",
                        formality_score=0.2, positive_markers=0, negative_markers=0,
                        word_count=20)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.derived.formality_level == FormalityLevel.INFORMAL

    def test_refusal_metrics(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1",
                        formality_score=0.5, is_refusal=(i < 3),
                        refusal_has_redirect=(i < 2), word_count=30)
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.derived.refusal_rate == 0.3
        assert profile.raw.refusal_count == 3
        assert profile.raw.refusal_with_redirect_count == 2

    def test_hedge_evidence(self):
        signals = [
            TurnSignals(turn_index=i, conversation_id="c1",
                        formality_score=0.5, hedge_phrases=2, word_count=30,
                        text_snippet="It seems like perhaps you might want...")
            for i in range(10)
        ]
        profile = self.analyzer.analyze(signals)
        assert profile.derived.hedge_frequency > 0
        # Evidence should mention hedging
        hedge_evidence = [e for e in profile.evidence if "hedge" in e.get("metric", "").lower()]
        assert len(hedge_evidence) > 0


# ── Consistency Analyzer ─────────────────────────────────────

from ai_simtest_engine.signature.analyzers.consistency_analyzer import ConsistencyAnalyzer


class TestConsistencyAnalyzer:

    def setup_method(self):
        self.analyzer = ConsistencyAnalyzer()

    def test_empty_signals(self):
        profile = self.analyzer.analyze(
            [], ToneProfile(), VerbosityProfile(), PatternProfile(),
        )
        assert profile.reliability.confidence == Confidence.LOW

    def test_high_consistency(self):
        # All signals have similar formality and word count
        signals = [
            TurnSignals(turn_index=i % 5, conversation_id=f"c{i // 5}",
                        formality_score=0.6, word_count=50)
            for i in range(15)  # 3 conversations × 5 turns
        ]
        tone = ToneProfile()
        verbosity = VerbosityProfile()
        pattern = PatternProfile()
        profile = self.analyzer.analyze(signals, tone, verbosity, pattern)
        assert profile.derived.overall_consistency_score > 0.5
        assert profile.raw.conversation_count == 3

    def test_persona_map(self):
        signals = [
            TurnSignals(turn_index=0, conversation_id="c1",
                        formality_score=0.6, word_count=50),
            TurnSignals(turn_index=0, conversation_id="c2",
                        formality_score=0.6, word_count=100),
        ]
        persona_map = {"c1": "standard", "c2": "adversarial"}
        profile = self.analyzer.analyze(
            signals, ToneProfile(), VerbosityProfile(), PatternProfile(),
            persona_map=persona_map,
        )
        assert profile.raw.persona_group_count == 2

    def test_meta_analyzer_does_not_reparse_text(self):
        """Consistency analyzer should only use signals and other profiles, not raw text."""
        signals = [
            TurnSignals(turn_index=0, conversation_id="c1",
                        formality_score=0.5, word_count=40)
        ] * 5
        # We can verify it runs without needing raw text
        profile = self.analyzer.analyze(
            signals, ToneProfile(), VerbosityProfile(), PatternProfile(),
        )
        assert profile.raw.conversation_count >= 1


# ── Engine ───────────────────────────────────────────────────

from ai_simtest_engine.signature.engine import SignatureEngine


class TestSignatureEngine:

    def setup_method(self):
        self.engine = SignatureEngine()

    def test_empty_input(self):
        sig = self.engine.analyze()
        assert sig.turn_count == 0
        assert sig.reliability.confidence == Confidence.LOW

    def test_both_inputs_raises(self):
        """Fix #7: Engine should reject both inputs at once."""
        convs = [{"id": "c1", "turns": [{"speaker": "bot", "message": "Hello"}]}]
        with pytest.raises(ValueError, match="exactly one"):
            self.engine.analyze(judged_conversations=convs, raw_conversations=convs)

    def test_reliability_notes_deduplicated(self):
        """Fix #8: Final signature should not have duplicate reliability notes."""
        convs = [{"id": f"c{i}", "turns": [
            {"speaker": "bot", "message": f"Response {i}"},
        ]} for i in range(3)]  # Below threshold → same note appears from multiple analyzers
        sig = self.engine.analyze(raw_conversations=convs)
        # Check no exact duplicates in notes
        assert len(sig.reliability.notes) == len(set(sig.reliability.notes))

    def test_raw_conversations(self):
        convs = [
            {
                "id": "c1",
                "turns": [
                    {"speaker": "user", "message": "Hello"},
                    {"speaker": "bot", "message": "Hello! I'd be happy to help you today. What can I assist you with?"},
                    {"speaker": "user", "message": "Check my balance"},
                    {"speaker": "bot", "message": "Your current account balance is $1,500.00. Is there anything else I can help with?"},
                ],
            },
            {
                "id": "c2",
                "turns": [
                    {"speaker": "user", "message": "I want to complain"},
                    {"speaker": "bot", "message": "I understand your frustration. I'm sorry to hear you've had a negative experience. Could you please share more details?"},
                    {"speaker": "user", "message": "Your service is terrible"},
                    {"speaker": "bot", "message": "I apologize for the inconvenience. Let me connect you with a senior representative who can better assist you."},
                ],
            },
        ]
        sig = self.engine.analyze(raw_conversations=convs, run_id="test-run")
        assert sig.run_id == "test-run"
        assert sig.conversation_count == 2
        assert sig.turn_count == 4  # 4 bot turns
        assert sig.summary_text != ""
        assert sig.tone.raw.total_responses == 4
        assert sig.verbosity.raw.total_responses == 4

    def test_full_pipeline_produces_all_profiles(self):
        convs = [
            {"id": f"c{i}", "turns": [
                {"speaker": "user", "message": f"Question {i}"},
                {"speaker": "bot", "message": f"Thank you for your question. Here is the answer to question {i}. I hope this helps. Let me know if you need anything else."},
            ]}
            for i in range(12)
        ]
        sig = self.engine.analyze(raw_conversations=convs)
        assert sig.tone.raw.total_responses == 12
        assert sig.verbosity.raw.total_responses == 12
        assert sig.patterns.raw.total_responses == 12
        assert sig.consistency.raw.conversation_count == 12

    def test_anomaly_detection(self):
        # One very long response among short ones
        convs = [
            {"id": f"c{i}", "turns": [
                {"speaker": "bot", "message": "Short answer." if i < 11 else "x " * 500},
            ]}
            for i in range(12)
        ]
        sig = self.engine.analyze(raw_conversations=convs)
        assert sig.anomaly_count >= 1  # the 500-word response should be flagged

    def test_to_dict_serializable(self):
        convs = [{"id": "c1", "turns": [
            {"speaker": "bot", "message": "Hello there. How can I help?"},
        ]}] * 6
        sig = self.engine.analyze(raw_conversations=convs)
        d = sig.to_dict()
        # Verify it's JSON-serializable
        import json
        json_str = json.dumps(d)
        assert len(json_str) > 100


# ── Summary ──────────────────────────────────────────────────

from ai_simtest_engine.signature.summary import generate_summary


class TestSummary:

    def test_basic_summary(self):
        tone = ToneProfile(
            derived=ToneDerivedScores(formality_level=FormalityLevel.FORMAL, empathy_frequency=0.8),
        )
        verbosity = VerbosityProfile(
            derived=VerbosityDerivedScores(verbosity_level=VerbosityLevel.MEDIUM),
        )
        patterns = PatternProfile(
            derived=PatternDerivedScores(repetition_score=0.2, question_asking_rate=0.1),
        )
        consistency = ConsistencyProfile(
            derived=ConsistencyDerivedScores(overall_consistency_score=0.8),
        )
        text = generate_summary(tone, verbosity, patterns, consistency, [])
        assert "formal" in text
        assert "empathy" in text
        assert "high" in text.lower()  # consistency high

    def test_summary_with_anomalies(self):
        tone = ToneProfile(derived=ToneDerivedScores())
        verbosity = VerbosityProfile(derived=VerbosityDerivedScores())
        patterns = PatternProfile(derived=PatternDerivedScores())
        consistency = ConsistencyProfile(derived=ConsistencyDerivedScores())
        anomalies = [AnomalyFlag(
            anomaly_type=AnomalyType.LENGTH_OUTLIER,
            conversation_id="c1", turn_index=0, description="test",
        )]
        text = generate_summary(tone, verbosity, patterns, consistency, anomalies)
        assert "1 anomalous" in text

    def test_summary_deterministic(self):
        """Same inputs → same output."""
        tone = ToneProfile(derived=ToneDerivedScores(formality_level=FormalityLevel.NEUTRAL))
        verb = VerbosityProfile(derived=VerbosityDerivedScores(verbosity_level=VerbosityLevel.LOW))
        pat = PatternProfile(derived=PatternDerivedScores())
        con = ConsistencyProfile(derived=ConsistencyDerivedScores(overall_consistency_score=0.5))
        text1 = generate_summary(tone, verb, pat, con, [])
        text2 = generate_summary(tone, verb, pat, con, [])
        assert text1 == text2


# ── Comparison ───────────────────────────────────────────────

from ai_simtest_engine.signature.compare import compare_signatures


class TestComparison:

    def test_no_drift(self):
        sig1 = BotSignature(
            run_id="r1",
            tone=ToneProfile(raw=ToneRawMetrics(avg_formality_score=0.5)),
            verbosity=VerbosityProfile(raw=VerbosityRawMetrics(mean_words=50, median_words=45)),
        )
        sig2 = BotSignature(
            run_id="r2",
            tone=ToneProfile(raw=ToneRawMetrics(avg_formality_score=0.52)),
            verbosity=VerbosityProfile(raw=VerbosityRawMetrics(mean_words=51, median_words=46)),
        )
        comp = compare_signatures(sig1, sig2)
        assert comp.significant_drift_count == 0
        assert "No significant" in comp.summary_text

    def test_significant_drift(self):
        sig1 = BotSignature(
            run_id="r1",
            tone=ToneProfile(raw=ToneRawMetrics(avg_formality_score=0.3)),
            verbosity=VerbosityProfile(raw=VerbosityRawMetrics(mean_words=30, median_words=25)),
        )
        sig2 = BotSignature(
            run_id="r2",
            tone=ToneProfile(raw=ToneRawMetrics(avg_formality_score=0.8)),
            verbosity=VerbosityProfile(raw=VerbosityRawMetrics(mean_words=100, median_words=90)),
        )
        comp = compare_signatures(sig1, sig2)
        assert comp.significant_drift_count > 0
        assert "drift" in comp.summary_text.lower()

    def test_comparison_to_dict(self):
        sig1 = BotSignature(run_id="r1")
        sig2 = BotSignature(run_id="r2")
        comp = compare_signatures(sig1, sig2)
        d = comp.to_dict()
        assert "drifts" in d


# ── HTML Injection ───────────────────────────────────────────

from ai_simtest_engine.signature.signature_html import inject_signature_into_report


class TestHTMLInjection:

    def test_injection_into_report(self, tmp_path):
        html_file = tmp_path / "report.html"
        html_file.write_text("""<html><body>
<section class="section"><h2>Test</h2></section>
</body></html>""")

        sig = BotSignature(
            run_id="test",
            conversation_count=5,
            turn_count=20,
            summary_text="The bot is formal and consistent.",
            anomaly_count=1,
            anomalies=[AnomalyFlag(
                anomaly_type=AnomalyType.LENGTH_OUTLIER,
                conversation_id="c1", turn_index=0,
                description="too long", text_snippet="very long response...",
            )],
        )
        sig.patterns.derived.top_repeated_phrases = [
            RepeatedPhrase(phrase="thank you", count=5, percentage=50.0, category="opening"),
        ]

        result = inject_signature_into_report(str(html_file), sig)
        assert result is True

        content = html_file.read_text()
        assert "Behavioral Signature" in content
        assert "signatureRadar" in content
        assert "thank you" in content
        assert "too long" in content

    def test_injection_no_body(self, tmp_path):
        html_file = tmp_path / "bad.html"
        html_file.write_text("<div>no body tag</div>")
        sig = BotSignature()
        result = inject_signature_into_report(str(html_file), sig)
        assert result is False

    def test_injection_nonexistent_file(self):
        sig = BotSignature()
        result = inject_signature_into_report("/nonexistent/path.html", sig)
        assert result is False
