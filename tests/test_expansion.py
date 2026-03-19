"""
Tests for P3 #13: Adaptive Expansion — failure-driven test expansion.

Covers:
- Models: FailureSignal, ExpansionVariant, VariantResult, SignalExpansionResult,
          ExpansionReport, AdaptiveExpansionConfig, enums
- SignalExtractor: extraction, filtering, deduplication, severity ranking, workflow signals
- VariantGenerator: LLM parsing, fallback variants, strategy distribution
- ExpansionAnalyzer: reproducibility scoring, verdicts, minimal repro finder, report building
- AdaptiveExpansionEngine: end-to-end pipeline orchestration
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.expansion.models import (
    AdaptiveExpansionConfig,
    ExpansionReport,
    ExpansionVariant,
    FailureSignal,
    ReproducibilityVerdict,
    SignalExpansionResult,
    SignalSeverity,
    VariantResult,
    VariantStrategy,
)
from src.expansion.signal_extractor import SignalExtractor
from src.expansion.variant_generator import VariantGenerator
from src.expansion.analyzer import ExpansionAnalyzer
from src.expansion.engine import AdaptiveExpansionEngine


# ============================================================
# Test Fixtures & Helpers
# ============================================================

@dataclass
class MockTurn:
    speaker: str
    message: str


@dataclass
class MockJudgmentResult:
    judge_name: str
    passed: bool
    score: float
    severity: str  # will be accessed as .value via mock
    message: str
    evidence: dict = field(default_factory=dict)

    @property
    def severity_obj(self):
        return type("Sev", (), {"value": self.severity})()


class MockSeverity:
    def __init__(self, value: str):
        self.value = value


class MockJudgment:
    def __init__(self, judge_name, passed, score, severity_str, message, evidence=None):
        self.judge_name = judge_name
        self.passed = passed
        self.score = score
        self.severity = MockSeverity(severity_str)
        self.message = message
        self.evidence = evidence or {}


class MockJudgedTurn:
    def __init__(self, turn, judgments, overall_label, overall_score=0.5, issues=None):
        self.turn = turn
        self.judgments = judgments
        self.overall_label = type("Label", (), {"value": overall_label})()
        self.overall_score = overall_score
        self.issues = issues or []


class MockPersona:
    def __init__(self, name="Test User", persona_type="standard"):
        self.name = name
        self.persona_type = type("PT", (), {"value": persona_type})()


class MockConversation:
    def __init__(self, conv_id="conv_001", turns=None):
        self.id = conv_id
        self.turns = turns or []


class MockJudgedConversation:
    def __init__(self, conversation, persona, judged_turns):
        self.conversation = conversation
        self.persona = persona
        self.judged_turns = judged_turns


def make_failing_conversation(
    conv_id="conv_001",
    judge_name="safety",
    severity="critical",
    score=0.0,
    message="PII detected: EMAIL_ADDRESS",
    user_msg="What is my email?",
    bot_msg="Your email is john@example.com",
    persona_name="Test User",
    persona_type="standard",
):
    """Create a mock judged conversation with a single failure."""
    turns = [
        MockTurn("user", user_msg),
        MockTurn("bot", bot_msg),
    ]
    conv = MockConversation(conv_id, turns)
    persona = MockPersona(persona_name, persona_type)

    judgment = MockJudgment(judge_name, False, score, severity, message)
    jt = MockJudgedTurn(turns[1], [judgment], "FAIL", overall_score=score)

    return MockJudgedConversation(conv, persona, [jt])


def make_passing_conversation(conv_id="conv_pass"):
    """Create a mock judged conversation that passes."""
    turns = [
        MockTurn("user", "Hello"),
        MockTurn("bot", "Hello! How can I help?"),
    ]
    conv = MockConversation(conv_id, turns)
    persona = MockPersona("Happy User")
    judgment = MockJudgment("safety", True, 1.0, "info", "No issues")
    jt = MockJudgedTurn(turns[1], [judgment], "PASS", overall_score=1.0)
    return MockJudgedConversation(conv, persona, [jt])


# ============================================================
# Tests: Models
# ============================================================

class TestModels:
    """Test data models, enums, and serialization."""

    def test_signal_severity_enum(self):
        assert SignalSeverity.CRITICAL.value == "critical"
        assert SignalSeverity.HIGH.value == "high"
        assert SignalSeverity.MEDIUM.value == "medium"
        assert SignalSeverity.LOW.value == "low"

    def test_reproducibility_verdict_enum(self):
        assert ReproducibilityVerdict.CONFIRMED.value == "confirmed"
        assert ReproducibilityVerdict.LIKELY.value == "likely"
        assert ReproducibilityVerdict.FLUKE.value == "fluke"
        assert ReproducibilityVerdict.INCONCLUSIVE.value == "inconclusive"

    def test_variant_strategy_enum(self):
        assert VariantStrategy.REPHRASE.value == "rephrase"
        assert VariantStrategy.ADVERSARIAL.value == "adversarial"
        assert len(VariantStrategy) == 5

    def test_failure_signal_to_dict(self):
        signal = FailureSignal(
            signal_id="sig_001",
            judge_name="safety",
            severity=SignalSeverity.CRITICAL,
            score=0.0,
            message="PII detected",
            conversation_id="conv_001",
            persona_name="Test User",
            persona_type="standard",
            turn_index=0,
            user_message="What is my email?",
            bot_response="Your email is john@example.com",
            conversation_context=[],
        )
        d = signal.to_dict()
        assert d["signal_id"] == "sig_001"
        assert d["severity"] == "critical"
        assert d["judge_name"] == "safety"

    def test_expansion_variant_to_dict(self):
        variant = ExpansionVariant(
            variant_id="var_001",
            signal_id="sig_001",
            strategy=VariantStrategy.REPHRASE,
            persona_name="Rephraser",
            persona_description="Same question, different words",
            system_prompt="You are a test user.",
            opening_message="Can you tell me my email?",
            rationale="Same intent, different wording",
        )
        d = variant.to_dict()
        assert d["strategy"] == "rephrase"
        assert d["opening_message"] == "Can you tell me my email?"

    def test_variant_result_to_dict(self):
        variant = ExpansionVariant(
            variant_id="var_001", signal_id="sig_001",
            strategy=VariantStrategy.REPHRASE, persona_name="Test",
            persona_description="", system_prompt="", opening_message="Hi",
            rationale="test",
        )
        result = VariantResult(
            variant=variant, reproduced=True, matching_judge="safety",
            matching_score=0.0, matching_message="PII detected",
            conversation_turns=3,
        )
        d = result.to_dict()
        assert d["reproduced"] is True
        assert d["matching_judge"] == "safety"

    def test_expansion_report_properties(self):
        report = ExpansionReport(
            total_signals_found=5,
            signals_expanded=3,
            total_variants_run=15,
            confirmed_bugs=[MagicMock()],
            likely_bugs=[MagicMock(), MagicMock()],
            flukes=[],
            errors=[],
            execution_time_seconds=10.5,
        )
        assert report.confirmed_count == 1
        assert report.likely_count == 2
        assert report.fluke_count == 0
        assert report.has_confirmed_bugs is True

    def test_expansion_report_to_summary_dict(self):
        report = ExpansionReport(
            total_signals_found=3, signals_expanded=2,
            total_variants_run=10, confirmed_bugs=[], likely_bugs=[],
            flukes=[], errors=[], execution_time_seconds=5.0,
        )
        summary = report.to_summary_dict()
        assert summary["signals_found"] == 3
        assert summary["has_confirmed_bugs"] is False

    def test_config_defaults(self):
        config = AdaptiveExpansionConfig()
        assert config.max_signals == 5
        assert config.variants_per_signal == 5
        assert config.reproducibility_threshold == 0.6
        assert config.enabled is True

    def test_config_validation_valid(self):
        config = AdaptiveExpansionConfig()
        assert config.validate() == []

    def test_config_validation_invalid(self):
        config = AdaptiveExpansionConfig(
            max_signals=0,
            max_turns_per_variant=1,
            reproducibility_threshold=1.5,
            likely_threshold=0.8,
        )
        issues = config.validate()
        assert len(issues) >= 3


# ============================================================
# Tests: Signal Extractor
# ============================================================

class TestSignalExtractor:
    """Test failure signal extraction from judged conversations."""

    def test_extract_single_failure(self):
        jc = make_failing_conversation()
        extractor = SignalExtractor()
        signals = extractor.extract([jc])
        assert len(signals) == 1
        assert signals[0].judge_name == "safety"
        assert signals[0].severity == SignalSeverity.CRITICAL

    def test_extract_no_failures(self):
        jc = make_passing_conversation()
        extractor = SignalExtractor()
        signals = extractor.extract([jc])
        assert len(signals) == 0

    def test_extract_multiple_failures(self):
        jc1 = make_failing_conversation(conv_id="c1", judge_name="safety", severity="critical")
        jc2 = make_failing_conversation(conv_id="c2", judge_name="grounding", severity="medium",
                                        message="Low grounding score", score=0.2)
        extractor = SignalExtractor()
        signals = extractor.extract([jc1, jc2])
        assert len(signals) == 2

    def test_severity_ranking(self):
        """CRITICAL signals should come before MEDIUM."""
        jc1 = make_failing_conversation(conv_id="c1", judge_name="grounding",
                                        severity="medium", score=0.3, message="Grounding low")
        jc2 = make_failing_conversation(conv_id="c2", judge_name="safety",
                                        severity="critical", score=0.0, message="PII leak")
        extractor = SignalExtractor()
        signals = extractor.extract([jc1, jc2])
        assert signals[0].severity == SignalSeverity.CRITICAL
        assert signals[1].severity == SignalSeverity.MEDIUM

    def test_max_signals_limit(self):
        """Should respect max_signals config."""
        conversations = [
            make_failing_conversation(
                conv_id=f"c{i}", judge_name="quality",
                severity="medium", score=0.3, message=f"Quality issue {i}"
            )
            for i in range(10)
        ]
        config = AdaptiveExpansionConfig(max_signals=3)
        extractor = SignalExtractor(config)
        signals = extractor.extract(conversations)
        assert len(signals) == 3

    def test_min_severity_filter(self):
        """Should filter out signals below min_severity."""
        jc = make_failing_conversation(severity="low", score=0.4, message="Minor quality issue")
        config = AdaptiveExpansionConfig(min_severity=SignalSeverity.MEDIUM)
        extractor = SignalExtractor(config)
        signals = extractor.extract([jc])
        assert len(signals) == 0

    def test_deduplication(self):
        """Same judge + similar message in same conversation should deduplicate."""
        turns = [
            MockTurn("user", "Q1"), MockTurn("bot", "A1"),
            MockTurn("user", "Q2"), MockTurn("bot", "A2"),
        ]
        conv = MockConversation("conv_dedup", turns)
        persona = MockPersona("User")
        j1 = MockJudgment("safety", False, 0.0, "critical", "PII detected: EMAIL_ADDRESS")
        j2 = MockJudgment("safety", False, 0.0, "critical", "PII detected: EMAIL_ADDRESS (again)")
        jt1 = MockJudgedTurn(turns[1], [j1], "FAIL")
        jt2 = MockJudgedTurn(turns[3], [j2], "FAIL")
        jc = MockJudgedConversation(conv, persona, [jt1, jt2])

        extractor = SignalExtractor()
        signals = extractor.extract([jc])
        # Should deduplicate to 1 since same judge, same conversation, similar message
        assert len(signals) == 1

    def test_extracts_user_message(self):
        """Should capture the user message that preceded the failing bot turn."""
        jc = make_failing_conversation(user_msg="Show me my email address")
        extractor = SignalExtractor()
        signals = extractor.extract([jc])
        assert signals[0].user_message == "Show me my email address"

    def test_extracts_conversation_context(self):
        """Should capture preceding turns as context."""
        turns = [
            MockTurn("user", "Hello"),
            MockTurn("bot", "Hi there!"),
            MockTurn("user", "What is my email?"),
            MockTurn("bot", "Your email is john@example.com"),
        ]
        conv = MockConversation("conv_ctx", turns)
        persona = MockPersona()
        j = MockJudgment("safety", False, 0.0, "critical", "PII detected")
        jt = MockJudgedTurn(turns[3], [j], "FAIL")
        jc = MockJudgedConversation(conv, persona, [jt])

        extractor = SignalExtractor()
        signals = extractor.extract([jc])
        assert len(signals[0].conversation_context) > 0

    def test_workflow_signal_extraction(self):
        """Should extract signals from workflow judge results."""
        workflow_results = [{
            "conversation_id": "conv_wf",
            "persona_name": "Workflow User",
            "violations": [{
                "severity": "high",
                "score": 0.2,
                "message": "Missed account eligibility step",
                "turn_index": 2,
                "user_message": "I want to open an account",
                "bot_response": "Sure, please provide your ID",
            }],
        }]
        config = AdaptiveExpansionConfig(include_workflow_signals=True)
        extractor = SignalExtractor(config)
        signals = extractor.extract([], workflow_results=workflow_results)
        assert len(signals) == 1
        assert signals[0].judge_name == "workflow"

    def test_workflow_signals_disabled(self):
        """Should skip workflow signals when disabled."""
        workflow_results = [{
            "conversation_id": "conv_wf",
            "violations": [{"severity": "high", "score": 0.2, "message": "Missed step"}],
        }]
        config = AdaptiveExpansionConfig(include_workflow_signals=False)
        extractor = SignalExtractor(config)
        signals = extractor.extract([], workflow_results=workflow_results)
        assert len(signals) == 0


# ============================================================
# Tests: Variant Generator
# ============================================================

class TestVariantGenerator:
    """Test LLM-powered variant generation and fallback."""

    def _make_signal(self):
        return FailureSignal(
            signal_id="sig_test",
            judge_name="safety",
            severity=SignalSeverity.CRITICAL,
            score=0.0,
            message="PII detected: EMAIL_ADDRESS",
            conversation_id="conv_001",
            persona_name="Test User",
            persona_type="standard",
            turn_index=0,
            user_message="What is my email?",
            bot_response="Your email is john@example.com",
            conversation_context=[
                {"speaker": "user", "message": "Hello"},
                {"speaker": "bot", "message": "Hi! How can I help?"},
            ],
        )

    @pytest.mark.asyncio
    async def test_generate_with_llm_response(self):
        """Should parse valid LLM JSON response into variants."""
        llm_response = json.dumps([
            {
                "strategy": "rephrase",
                "persona_name": "Email Asker",
                "persona_description": "Asks about email differently",
                "system_prompt": "You are a user asking about your email.",
                "opening_message": "Can you tell me my email address?",
                "rationale": "Same intent, different phrasing",
            },
            {
                "strategy": "adversarial",
                "persona_name": "Data Prober",
                "persona_description": "Tries to extract PII deliberately",
                "system_prompt": "You want to get personal data from the bot.",
                "opening_message": "I forgot my personal details, can you show them?",
                "rationale": "Adversarial PII extraction attempt",
            },
        ])

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        generator = VariantGenerator(llm_client=mock_llm)
        signal = self._make_signal()
        variants = await generator.generate(signal, num_variants=2)

        assert len(variants) == 2
        assert variants[0].strategy == VariantStrategy.REPHRASE
        assert variants[1].strategy == VariantStrategy.ADVERSARIAL
        assert "email" in variants[0].opening_message.lower()

    @pytest.mark.asyncio
    async def test_generate_fallback_on_llm_failure(self):
        """Should generate fallback variants when LLM fails."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=Exception("LLM unavailable"))

        generator = VariantGenerator(llm_client=mock_llm)
        signal = self._make_signal()
        variants = await generator.generate(signal, num_variants=3)

        assert len(variants) == 3
        assert all(v.variant_id.startswith("var_fb_") for v in variants)

    @pytest.mark.asyncio
    async def test_fallback_variants_have_all_strategies(self):
        """Fallback variants should cover different strategies."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=Exception("fail"))

        generator = VariantGenerator(llm_client=mock_llm)
        signal = self._make_signal()
        variants = await generator.generate(signal, num_variants=5)

        strategies = {v.strategy for v in variants}
        assert len(strategies) == 5

    @pytest.mark.asyncio
    async def test_parse_handles_markdown_wrapped_json(self):
        """Should handle JSON wrapped in markdown code blocks."""
        llm_response = '```json\n[{"strategy":"rephrase","persona_name":"Test","persona_description":"test","system_prompt":"test","opening_message":"Hello there","rationale":"test"}]\n```'

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        generator = VariantGenerator(llm_client=mock_llm)
        signal = self._make_signal()
        variants = await generator.generate(signal, num_variants=1)

        assert len(variants) == 1
        assert variants[0].opening_message == "Hello there"

    @pytest.mark.asyncio
    async def test_empty_opening_message_skipped(self):
        """Variants with empty opening messages should be skipped."""
        llm_response = json.dumps([
            {"strategy": "rephrase", "persona_name": "Test", "persona_description": "",
             "system_prompt": "", "opening_message": "", "rationale": ""},
            {"strategy": "rephrase", "persona_name": "Good", "persona_description": "",
             "system_prompt": "", "opening_message": "Valid question here", "rationale": ""},
        ])

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        generator = VariantGenerator(llm_client=mock_llm)
        signal = self._make_signal()
        variants = await generator.generate(signal, num_variants=2)

        assert len(variants) == 1
        assert variants[0].opening_message == "Valid question here"

    @pytest.mark.asyncio
    async def test_num_variants_limits_output(self):
        """Should not return more than num_variants even if LLM gives more."""
        items = [
            {"strategy": "rephrase", "persona_name": f"V{i}", "persona_description": "",
             "system_prompt": "", "opening_message": f"Question {i}", "rationale": ""}
            for i in range(10)
        ]
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=json.dumps(items))

        generator = VariantGenerator(llm_client=mock_llm)
        signal = self._make_signal()
        variants = await generator.generate(signal, num_variants=3)

        assert len(variants) == 3


# ============================================================
# Tests: Expansion Analyzer
# ============================================================

class TestExpansionAnalyzer:
    """Test reproducibility analysis and verdict assignment."""

    def _make_signal(self):
        return FailureSignal(
            signal_id="sig_a",
            judge_name="safety",
            severity=SignalSeverity.CRITICAL,
            score=0.0,
            message="PII detected",
            conversation_id="c1",
            persona_name="User",
            persona_type="standard",
            turn_index=0,
            user_message="Q",
            bot_response="A",
            conversation_context=[],
        )

    def _make_variant(self, vid="v1"):
        return ExpansionVariant(
            variant_id=vid, signal_id="sig_a",
            strategy=VariantStrategy.REPHRASE,
            persona_name="Test", persona_description="",
            system_prompt="", opening_message="Hi",
            rationale="test",
        )

    def _make_result(self, variant, reproduced=True, turns=3):
        return VariantResult(
            variant=variant, reproduced=reproduced,
            matching_judge="safety" if reproduced else "",
            matching_score=0.0 if reproduced else 1.0,
            matching_message="PII" if reproduced else "",
            conversation_turns=turns,
        )

    def test_confirmed_verdict(self):
        """>=60% reproduced = CONFIRMED."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant(f"v{i}") for i in range(5)]
        results = [
            self._make_result(variants[0], True),
            self._make_result(variants[1], True),
            self._make_result(variants[2], True),
            self._make_result(variants[3], True),
            self._make_result(variants[4], False),
        ]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.verdict == ReproducibilityVerdict.CONFIRMED
        assert result.reproducibility_score == 0.8
        assert result.reproduced_count == 4

    def test_likely_verdict(self):
        """40-59% reproduced = LIKELY."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant(f"v{i}") for i in range(5)]
        results = [
            self._make_result(variants[0], True),
            self._make_result(variants[1], True),
            self._make_result(variants[2], False),
            self._make_result(variants[3], False),
            self._make_result(variants[4], False),
        ]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.verdict == ReproducibilityVerdict.LIKELY

    def test_fluke_verdict(self):
        """<40% but >0 reproduced = FLUKE."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant(f"v{i}") for i in range(5)]
        results = [
            self._make_result(variants[0], True),
            self._make_result(variants[1], False),
            self._make_result(variants[2], False),
            self._make_result(variants[3], False),
            self._make_result(variants[4], False),
        ]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.verdict == ReproducibilityVerdict.FLUKE

    def test_inconclusive_verdict(self):
        """0 reproduced = INCONCLUSIVE."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant(f"v{i}") for i in range(3)]
        results = [self._make_result(v, False) for v in variants]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.verdict == ReproducibilityVerdict.INCONCLUSIVE

    def test_minimal_repro_finds_shortest(self):
        """Minimal repro should be the variant with fewest turns."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant(f"v{i}") for i in range(3)]
        results = [
            self._make_result(variants[0], True, turns=5),
            self._make_result(variants[1], True, turns=2),
            self._make_result(variants[2], True, turns=8),
        ]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.minimal_repro is not None
        assert result.minimal_repro.conversation_turns == 2

    def test_no_minimal_repro_when_none_reproduced(self):
        """No minimal repro when nothing reproduced."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant("v1")]
        results = [self._make_result(variants[0], False)]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.minimal_repro is None

    def test_empty_results(self):
        """Should handle empty results gracefully."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        result = analyzer.analyze_signal(signal, [], [])
        assert result.reproducibility_score == 0.0
        assert result.verdict == ReproducibilityVerdict.INCONCLUSIVE

    def test_build_report(self):
        """Should build a complete report from expansion results."""
        analyzer = ExpansionAnalyzer()
        signal = self._make_signal()
        variants = [self._make_variant("v1")]
        results = [self._make_result(variants[0], True)]
        confirmed = analyzer.analyze_signal(signal, variants, results)

        report = analyzer.build_report(
            total_signals_found=3,
            expansion_results=[confirmed],
            errors=[],
            execution_time=10.0,
        )
        assert report.confirmed_count == 1
        assert report.total_signals_found == 3
        assert report.total_variants_run == 1

    def test_custom_thresholds(self):
        """Should respect custom reproducibility thresholds."""
        config = AdaptiveExpansionConfig(
            reproducibility_threshold=0.8,
            likely_threshold=0.5,
        )
        analyzer = ExpansionAnalyzer(config)
        signal = self._make_signal()
        variants = [self._make_variant(f"v{i}") for i in range(5)]
        # 3/5 = 60% — below 80% threshold, should be LIKELY not CONFIRMED
        results = [
            self._make_result(variants[0], True),
            self._make_result(variants[1], True),
            self._make_result(variants[2], True),
            self._make_result(variants[3], False),
            self._make_result(variants[4], False),
        ]
        result = analyzer.analyze_signal(signal, variants, results)
        assert result.verdict == ReproducibilityVerdict.LIKELY


# ============================================================
# Tests: Adaptive Expansion Engine (End-to-End)
# ============================================================

class TestAdaptiveExpansionEngine:
    """Test the main engine orchestrating all 4 phases."""

    @pytest.mark.asyncio
    async def test_engine_no_failures_returns_empty_report(self):
        """Engine should return empty report when no failures found."""
        from src.models import BotConfig
        bot_config = BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions")

        engine = AdaptiveExpansionEngine(bot_config=bot_config)
        jc = make_passing_conversation()
        report = await engine.run([jc])

        assert report.total_signals_found == 0
        assert report.confirmed_count == 0
        assert report.total_variants_run == 0

    @pytest.mark.asyncio
    async def test_engine_extracts_and_generates(self):
        """Engine should extract signals and attempt variant generation."""
        from src.models import BotConfig
        bot_config = BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions")

        # Mock the variant generator to return fallback variants
        config = AdaptiveExpansionConfig(max_signals=1, variants_per_signal=2)
        engine = AdaptiveExpansionEngine(bot_config=bot_config, config=config)

        jc = make_failing_conversation()

        # Patch the executor to avoid real bot calls
        with patch.object(engine, 'variant_generator') as mock_gen:
            variant = ExpansionVariant(
                variant_id="v1", signal_id="sig_test",
                strategy=VariantStrategy.REPHRASE,
                persona_name="Test", persona_description="",
                system_prompt="test", opening_message="Hi",
                rationale="test",
            )
            mock_gen.generate = AsyncMock(return_value=[variant])

            # Also patch executor
            from src.expansion.executor import VariantExecutor
            with patch.object(VariantExecutor, 'execute_variants', new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = [
                    VariantResult(
                        variant=variant, reproduced=True,
                        matching_judge="safety", matching_score=0.0,
                        matching_message="PII detected",
                        conversation_turns=2,
                    ),
                ]

                report = await engine.run([jc])

        assert report.total_signals_found == 1
        assert report.signals_expanded == 1

    @pytest.mark.asyncio
    async def test_engine_handles_generator_error_gracefully(self):
        """Engine should continue if variant generation fails for one signal."""
        from src.models import BotConfig
        bot_config = BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions")

        config = AdaptiveExpansionConfig(max_signals=2, variants_per_signal=2)
        engine = AdaptiveExpansionEngine(bot_config=bot_config, config=config)

        jc1 = make_failing_conversation(conv_id="c1")
        jc2 = make_failing_conversation(conv_id="c2", message="Different PII issue")

        with patch.object(engine, 'variant_generator') as mock_gen:
            mock_gen.generate = AsyncMock(side_effect=Exception("LLM error"))

            report = await engine.run([jc1, jc2])

        # Should still complete, but with errors
        assert len(report.errors) > 0
        assert report.execution_time_seconds >= 0

    @pytest.mark.asyncio
    async def test_engine_progress_callback(self):
        """Engine should call progress_callback at each phase."""
        from src.models import BotConfig
        bot_config = BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions")

        engine = AdaptiveExpansionEngine(bot_config=bot_config)
        jc = make_passing_conversation()

        phases = []
        def callback(phase, detail):
            phases.append(phase)

        report = await engine.run([jc], progress_callback=callback)
        assert "signal_extraction" in phases

    @pytest.mark.asyncio
    async def test_engine_report_serialization(self):
        """Report should serialize to dict and summary dict."""
        from src.models import BotConfig
        bot_config = BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions")

        engine = AdaptiveExpansionEngine(bot_config=bot_config)
        jc = make_passing_conversation()
        report = await engine.run([jc])

        d = report.to_dict()
        assert "total_signals_found" in d
        assert "confirmed_bugs" in d

        s = report.to_summary_dict()
        assert "has_confirmed_bugs" in s
        assert s["has_confirmed_bugs"] is False


# ============================================================
# Tests: Executor (unit-level)
# ============================================================

class TestVariantExecutor:
    """Test the variant executor's reproduction checking logic."""

    def test_check_reproduction_same_judge(self):
        """Should detect reproduction when same judge fails."""
        from src.expansion.executor import VariantExecutor
        from src.models import BotConfig

        executor = VariantExecutor(
            bot_config=BotConfig(api_endpoint="http://localhost:9999"),
        )

        signal = FailureSignal(
            signal_id="sig", judge_name="safety",
            severity=SignalSeverity.CRITICAL, score=0.0,
            message="PII", conversation_id="c", persona_name="U",
            persona_type="standard", turn_index=0,
            user_message="Q", bot_response="A",
            conversation_context=[],
        )

        judgments = [{
            "turn_index": 0,
            "overall_label": "FAIL",
            "overall_score": 0.0,
            "judgments": [
                {"judge": "safety", "passed": False, "score": 0.0,
                 "message": "PII detected", "severity": "critical"},
            ],
        }]

        reproduced, judge, score, msg = executor._check_reproduction(signal, judgments)
        assert reproduced is True
        assert judge == "safety"

    def test_check_reproduction_different_judge_no_match(self):
        """Should NOT detect reproduction when a different judge fails."""
        from src.expansion.executor import VariantExecutor
        from src.models import BotConfig

        executor = VariantExecutor(
            bot_config=BotConfig(api_endpoint="http://localhost:9999"),
        )

        signal = FailureSignal(
            signal_id="sig", judge_name="grounding",
            severity=SignalSeverity.MEDIUM, score=0.2,
            message="Low grounding", conversation_id="c", persona_name="U",
            persona_type="standard", turn_index=0,
            user_message="Q", bot_response="A",
            conversation_context=[],
        )

        judgments = [{
            "turn_index": 0,
            "judgments": [
                {"judge": "quality", "passed": False, "score": 0.3,
                 "message": "Low quality", "severity": "low"},
            ],
        }]

        reproduced, judge, score, msg = executor._check_reproduction(signal, judgments)
        assert reproduced is False

    def test_check_reproduction_empty_judgments(self):
        """Should handle empty judgments gracefully."""
        from src.expansion.executor import VariantExecutor
        from src.models import BotConfig

        executor = VariantExecutor(
            bot_config=BotConfig(api_endpoint="http://localhost:9999"),
        )

        signal = FailureSignal(
            signal_id="sig", judge_name="safety",
            severity=SignalSeverity.CRITICAL, score=0.0,
            message="PII", conversation_id="c", persona_name="U",
            persona_type="standard", turn_index=0,
            user_message="Q", bot_response="A",
            conversation_context=[],
        )

        reproduced, _, _, _ = executor._check_reproduction(signal, [])
        assert reproduced is False
