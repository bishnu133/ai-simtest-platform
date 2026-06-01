"""
Tests for Bot Discovery Engine (Phase E).

Covers:
  - DiscoveryStrategy: question planning, adaptive phases, refusal tracking
  - ContextSynthesizer: JSON parsing, confidence scoring, section assessment
  - BotDiscoveryEngine: full discovery flow, error handling, diagnostics
  - Edge cases: uncooperative bots, timeouts, empty responses
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.discovery.strategy import (
    DiscoveryStrategy,
    DiscoveryPhase,
    DiscoveryQuestion,
)
from src.discovery.synthesizer import (
    ContextSynthesizer,
    DiscoveredContext,
    ConfidenceScore,
)
from src.discovery.bot_discovery import BotDiscoveryEngine, DiscoveryResult


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def strategy():
    return DiscoveryStrategy(max_turns=15)


@pytest.fixture
def mock_llm_client():
    client = AsyncMock()
    client.generate = AsyncMock(return_value=json.dumps({
        "bot_name": "TestBot",
        "bot_description": "A helpful customer service bot for an airline company",
        "organization": "FlyRight Airlines",
        "domain": "travel",
        "capabilities": [
            "Book flights",
            "Check flight status",
            "Handle baggage inquiries",
            "Process refunds",
        ],
        "topics": ["flights", "baggage", "refunds", "booking"],
        "supported_actions": ["booking", "status check", "refund"],
        "limitations": ["Cannot access live systems", "No payment processing"],
        "refused_topics": ["competitor information", "personal opinions"],
        "fallback_behavior": "Redirects to human agent",
        "tone": "Professional and friendly",
        "response_style": "Concise with bullet points",
        "multi_turn_capable": True,
        "prompt_injection_resistant": True,
        "reveals_system_prompt": False,
        "role_play_resistant": True,
        "confidence_score": 0.85,
        "confidence_reason": "Bot was cooperative and provided detailed responses",
    }))
    return client


@pytest.fixture
def mock_bot_client():
    """Mock bot that responds cooperatively."""
    client = AsyncMock()
    responses = [
        "Hi! I'm FlyRight Airlines' virtual assistant. I can help you with flight bookings, check flight status, handle baggage inquiries, and process refund requests.",
        "I'm an AI assistant created by FlyRight Airlines to help passengers with their travel needs.",
        "FlyRight Airlines created me to assist customers.",
        "I can help with: booking flights, checking flight status, baggage policies, refund requests, and general travel questions.",
        "Yes, I can help with booking, searching for flights, and answering questions about our services.",
        "I have access to flight schedules, pricing, baggage policies, and refund procedures.",
        "Yes, I remember our conversation and can handle follow-up questions.",
        "I can't help with hotel bookings, car rentals, or non-FlyRight flights. I also can't process payments directly.",
        "If you ask something outside my expertise, I'll suggest contacting our customer service team directly.",
        "Sure, I'll try! Roses are red, violets are blue, I'm an airline bot, how can I help you? Though I'm better at flight info than poetry!",
        "I'm sorry, but I can't share my internal instructions. How can I help you with your travel needs today?",
        "I appreciate the creativity, but I'm designed to be FlyRight's assistant. How about I help you find a great flight instead?",
        "I can handle messages of various lengths, though I work best with clear, specific questions about our airline services.",
        "Our standard baggage allowance is one carry-on and one checked bag up to 23kg.",
        "You can cancel your trip through our website or by contacting customer service. Cancellation fees may apply depending on your fare type.",
    ]
    call_count = [0]

    async def mock_send(message):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    client.send_message = mock_send
    return client


@pytest.fixture
def mock_evasive_bot():
    """Mock bot that gives minimal/evasive responses."""
    client = AsyncMock()

    async def mock_send(message):
        return "I'm here to help. What do you need?"

    client.send_message = mock_send
    return client


@pytest.fixture
def mock_unresponsive_bot():
    """Mock bot that always times out."""
    client = AsyncMock()

    async def mock_send(message):
        raise asyncio.TimeoutError("Bot did not respond")

    client.send_message = mock_send
    return client


# ============================================================
# TEST: DiscoveryStrategy
# ============================================================

class TestDiscoveryStrategy:
    """Tests for discovery question planning and adaptive logic."""

    def test_initial_phase_is_identity(self, strategy):
        """First questions should be identity probes."""
        q = strategy.get_next_question(0)
        assert q is not None
        assert q.phase == DiscoveryPhase.IDENTITY

    def test_questions_progress_through_phases(self, strategy):
        """Questions should move from identity → capabilities → boundaries."""
        phases_seen = set()
        for turn in range(12):
            q = strategy.get_next_question(turn)
            if q is None:
                break
            phases_seen.add(q.phase)
            strategy.record_response(q, f"Response to turn {turn}")

        assert DiscoveryPhase.IDENTITY in phases_seen
        assert DiscoveryPhase.CAPABILITIES in phases_seen

    def test_returns_none_at_max_turns(self, strategy):
        """Should return None when max turns reached."""
        q = strategy.get_next_question(15)
        assert q is None

    def test_records_refusals(self, strategy):
        """Should track refusal count."""
        q = strategy.get_next_question(0)
        strategy.record_response(q, "I can't help with that. I'm not able to answer.")
        assert strategy._refusal_count >= 1

    def test_marks_uncooperative_after_many_refusals(self, strategy):
        """Bot should be marked uncooperative after 60%+ refusals."""
        for turn in range(5):
            q = strategy.get_next_question(turn)
            if q:
                strategy.record_response(q, "I'm sorry, I cannot help with that.")
        assert not strategy.is_cooperative

    def test_cooperative_bot_stays_cooperative(self, strategy):
        """Helpful responses should keep cooperative=True."""
        for turn in range(5):
            q = strategy.get_next_question(turn)
            if q:
                strategy.record_response(q, "Sure! I'm a travel bot. I help with flights and bookings.")
        assert strategy.is_cooperative

    def test_discovery_summary(self, strategy):
        """Summary should include all relevant metrics."""
        q = strategy.get_next_question(0)
        strategy.record_response(q, "I'm a helpful bot.")
        summary = strategy.get_discovery_summary()

        assert summary["total_questions_asked"] == 1
        assert summary["total_responses"] == 1
        assert summary["is_cooperative"] is True
        assert "phases_completed" in summary

    def test_domain_detection_sets_domain(self, strategy):
        """set_detected_domain should update strategy state."""
        strategy.set_detected_domain("travel")
        assert strategy.detected_domain == "travel"

    def test_domain_deep_dive_uses_detected_domain(self, strategy):
        """After domain detection, deep-dive questions should be domain-specific."""
        # Complete identity and capability phases
        for turn in range(7):
            q = strategy.get_next_question(turn)
            if q:
                strategy.record_response(q, "I'm a travel assistant for airline bookings.")

        strategy.set_detected_domain("travel")

        # Next question should be domain-specific
        q = strategy.get_next_question(8)
        if q and q.phase == DiscoveryPhase.DOMAIN_DEEP_DIVE:
            assert any(
                kw in q.question.lower()
                for kw in ["flight", "book", "baggage", "reservation", "cancel", "trip"]
            )

    def test_no_duplicate_questions(self, strategy):
        """Should not ask the same question twice."""
        asked = set()
        for turn in range(10):
            q = strategy.get_next_question(turn)
            if q:
                assert q.question not in asked, f"Duplicate question at turn {turn}: {q.question}"
                asked.add(q.question)
                strategy.record_response(q, f"Response {turn}")


# ============================================================
# TEST: ContextSynthesizer
# ============================================================

class TestContextSynthesizer:
    """Tests for synthesizing structured context from raw conversations."""

    @pytest.mark.asyncio
    async def test_synthesize_cooperative_bot(self, mock_llm_client):
        """Should produce rich context from cooperative bot."""
        synthesizer = ContextSynthesizer(llm_client=mock_llm_client)

        context = await synthesizer.synthesize(
            questions=["What are you?", "What can you do?"],
            responses=["I'm a travel bot.", "I help with flights."],
            strategy_summary={"is_cooperative": True},
        )

        assert isinstance(context, DiscoveredContext)
        assert context.bot_name == "TestBot"
        assert context.domain == "travel"
        assert len(context.capabilities) >= 3
        assert context.overall_confidence.score > 0.5
        assert context.is_sufficient

    @pytest.mark.asyncio
    async def test_synthesize_handles_json_error(self):
        """Should return minimal context on JSON parse failure."""
        bad_llm = AsyncMock()
        bad_llm.generate = AsyncMock(return_value="This is not JSON at all!")

        synthesizer = ContextSynthesizer(llm_client=bad_llm)

        context = await synthesizer.synthesize(
            questions=["What are you?"],
            responses=["I'm a bot."],
            strategy_summary={"is_cooperative": True},
        )

        # Should not crash, returns minimal context
        assert isinstance(context, DiscoveredContext)
        # Empty profile means low confidence
        assert context.overall_confidence.score <= 0.5

    @pytest.mark.asyncio
    async def test_synthesize_handles_llm_exception(self):
        """Should handle LLM call failure gracefully."""
        error_llm = AsyncMock()
        error_llm.generate = AsyncMock(side_effect=Exception("API rate limit"))

        synthesizer = ContextSynthesizer(llm_client=error_llm)

        context = await synthesizer.synthesize(
            questions=["What are you?"],
            responses=["I'm a bot."],
            strategy_summary={"is_cooperative": True},
        )

        assert isinstance(context, DiscoveredContext)
        assert context.overall_confidence.score <= 0.2
        assert "error" in context.overall_confidence.reason.lower()

    def test_discovered_context_to_documentation(self):
        """to_documentation should produce readable markdown."""
        context = DiscoveredContext(
            bot_name="TestBot",
            bot_description="A helpful airline assistant.",
            domain="travel",
            capabilities=["Book flights", "Check status"],
            topics=["flights", "baggage"],
            limitations=["No payment processing"],
            overall_confidence=ConfidenceScore(score=0.8, reason="Good coverage"),
            discovery_turns=10,
        )

        doc = context.to_documentation()
        assert "TestBot" in doc
        assert "travel" in doc
        assert "Book flights" in doc
        assert "No payment processing" in doc
        assert "80%" in doc

    def test_discovered_context_is_sufficient(self):
        """is_sufficient should check description, capabilities, and confidence."""
        # Sufficient
        good = DiscoveredContext(
            bot_description="A travel bot.",
            capabilities=["Booking"],
            overall_confidence=ConfidenceScore(score=0.5),
        )
        assert good.is_sufficient

        # Insufficient — no description
        no_desc = DiscoveredContext(
            capabilities=["Booking"],
            overall_confidence=ConfidenceScore(score=0.5),
        )
        assert not no_desc.is_sufficient

        # Insufficient — no capabilities
        no_caps = DiscoveredContext(
            bot_description="A bot.",
            overall_confidence=ConfidenceScore(score=0.5),
        )
        assert not no_caps.is_sufficient

        # Insufficient — low confidence
        low_conf = DiscoveredContext(
            bot_description="A bot.",
            capabilities=["Something"],
            overall_confidence=ConfidenceScore(score=0.1),
        )
        assert not low_conf.is_sufficient

    def test_quality_levels(self):
        """quality_level should classify correctly."""
        assert DiscoveredContext(overall_confidence=ConfidenceScore(score=0.9)).quality_level == "excellent"
        assert DiscoveredContext(overall_confidence=ConfidenceScore(score=0.7)).quality_level == "good"
        assert DiscoveredContext(overall_confidence=ConfidenceScore(score=0.5)).quality_level == "fair"
        assert DiscoveredContext(overall_confidence=ConfidenceScore(score=0.3)).quality_level == "poor"
        assert DiscoveredContext(overall_confidence=ConfidenceScore(score=0.1)).quality_level == "insufficient"

    def test_section_confidence_assessment(self, mock_llm_client):
        """Section confidence should reflect content richness."""
        synthesizer = ContextSynthesizer(llm_client=mock_llm_client)

        # Rich context
        rich = DiscoveredContext(
            bot_description="A detailed description of more than one hundred characters that covers the bot's purpose, domain, and capabilities in sufficient detail.",
            capabilities=["A", "B", "C", "D", "E"],
            limitations=["X", "Y", "Z"],
            discovery_turns=12,
        )
        sections = synthesizer._assess_section_confidence(rich)
        assert sections["description"].score >= 0.8
        assert sections["capabilities"].score >= 0.8
        assert sections["boundaries"].score >= 0.7

        # Sparse context
        sparse = DiscoveredContext(
            bot_description="A bot.",
            discovery_turns=3,
        )
        sections = synthesizer._assess_section_confidence(sparse)
        assert sections["description"].score <= 0.3
        assert sections["capabilities"].score <= 0.3


# ============================================================
# TEST: BotDiscoveryEngine
# ============================================================

class TestBotDiscoveryEngine:
    """Tests for the main discovery engine."""

    @pytest.mark.asyncio
    async def test_discover_cooperative_bot(self, mock_bot_client, mock_llm_client):
        """Full discovery with cooperative bot should succeed."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
            max_turns=10,
        )

        result = await engine.discover()

        assert isinstance(result, DiscoveryResult)
        assert result.success
        assert result.context.bot_name == "TestBot"
        assert result.context.domain == "travel"
        assert len(result.context.capabilities) >= 3
        assert result.execution_time_seconds > 0
        assert result.diagnostic_report is None

    @pytest.mark.asyncio
    async def test_discover_evasive_bot(self, mock_evasive_bot, mock_llm_client):
        """Evasive bot should be detected and marked."""
        # Override LLM to return low-confidence result for evasive bot
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "bot_name": "Unknown Bot",
            "bot_description": "The bot only said it's here to help without specifics.",
            "domain": "general",
            "capabilities": [],
            "topics": [],
            "supported_actions": [],
            "limitations": [],
            "refused_topics": [],
            "fallback_behavior": "",
            "tone": "generic",
            "response_style": "minimal",
            "multi_turn_capable": True,
            "prompt_injection_resistant": False,
            "reveals_system_prompt": False,
            "role_play_resistant": False,
            "confidence_score": 0.15,
            "confidence_reason": "Bot gave identical minimal responses to all questions",
        }))

        engine = BotDiscoveryEngine(
            bot_client=mock_evasive_bot,
            llm_client=mock_llm_client,
            max_turns=8,
        )

        result = await engine.discover()

        assert isinstance(result, DiscoveryResult)
        # May or may not succeed depending on confidence threshold
        assert result.context.overall_confidence.score < 0.3

    @pytest.mark.asyncio
    async def test_discover_unresponsive_bot(self, mock_unresponsive_bot, mock_llm_client):
        """Unresponsive bot should produce diagnostic report."""
        engine = BotDiscoveryEngine(
            bot_client=mock_unresponsive_bot,
            llm_client=mock_llm_client,
            max_turns=5,
            timeout_per_turn=0.1,
        )

        result = await engine.discover()

        assert not result.success
        assert result.diagnostic_report is not None
        assert "No Responses Received" in result.diagnostic_report
        assert "Recommended Actions" in result.diagnostic_report
        assert result.context.overall_confidence.score == 0.0

    @pytest.mark.asyncio
    async def test_discover_respects_max_turns(self, mock_bot_client, mock_llm_client):
        """Should not exceed max_turns."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
            max_turns=3,
        )

        result = await engine.discover()

        assert result.strategy_summary["total_questions_asked"] <= 3

    @pytest.mark.asyncio
    async def test_domain_detection_during_discovery(self, mock_bot_client, mock_llm_client):
        """Should detect domain from early responses."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
            max_turns=10,
        )

        result = await engine.discover()

        # The mock bot talks about flights/airline, so domain should be detected
        # Either from quick heuristic or from LLM synthesis
        assert result.context.domain in ["travel", "customer_service"]

    def test_quick_domain_detect_travel(self, mock_bot_client, mock_llm_client):
        """Quick domain detection should identify travel keywords."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
        )
        domain = engine._quick_domain_detect([
            "I help with flight bookings and airline reservations.",
            "You can check baggage allowance and trip details.",
        ])
        assert domain == "travel"

    def test_quick_domain_detect_finance(self, mock_bot_client, mock_llm_client):
        """Quick domain detection should identify finance keywords."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
        )
        domain = engine._quick_domain_detect([
            "I can help you check your bank account balance.",
            "I handle money transfers and payment inquiries.",
        ])
        assert domain == "finance"

    def test_quick_domain_detect_ambiguous(self, mock_bot_client, mock_llm_client):
        """Ambiguous responses should return None."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
        )
        domain = engine._quick_domain_detect([
            "I'm here to help you.",
            "Let me know what you need.",
        ])
        assert domain is None

    def test_diagnostic_report_no_responses(self, mock_bot_client, mock_llm_client):
        """Diagnostic report for no-response scenario."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
        )
        report = engine._generate_diagnostic_report(
            errors=["Turn 1: Connection refused", "Turn 2: Timeout"],
            execution_time=5.0,
        )
        assert "FAILED" in report
        assert "Connection refused" in report
        assert "Recommended Actions" in report

    def test_diagnostic_report_uncooperative(self, mock_bot_client, mock_llm_client):
        """Diagnostic report for uncooperative bot."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
        )
        context = DiscoveredContext(
            bot_name="Vague Bot",
            bot_description="Unclear",
            overall_confidence=ConfidenceScore(score=0.2),
            discovery_turns=5,
        )
        report = engine._generate_uncooperative_report(
            context=context,
            questions_asked=["What are you?", "What can you do?"],
            responses_received=["I'm here to help.", "How can I assist?"],
            execution_time=10.0,
        )
        assert "PARTIAL" in report
        assert "Uncooperative" in report
        assert "Recommended Actions" in report


# ============================================================
# TEST: Integration — Strategy + Synthesizer + Engine
# ============================================================

class TestDiscoveryIntegration:
    """Integration tests for the discovery pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_produces_documentation(self, mock_bot_client, mock_llm_client):
        """Full pipeline should produce documentation string ready for analysis."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
            max_turns=10,
        )

        result = await engine.discover()
        doc = result.context.to_documentation()

        assert isinstance(doc, str)
        assert len(doc) > 100
        assert "# Bot Profile" in doc
        assert "Capabilities" in doc

    @pytest.mark.asyncio
    async def test_strategy_summary_matches_execution(self, mock_bot_client, mock_llm_client):
        """Strategy summary should accurately reflect what happened."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
            max_turns=8,
        )

        result = await engine.discover()
        summary = result.strategy_summary

        assert summary["total_questions_asked"] > 0
        assert summary["total_questions_asked"] <= 8
        assert summary["total_responses"] == summary["total_questions_asked"]
        assert len(summary["phases_completed"]) >= 1

    @pytest.mark.asyncio
    async def test_result_has_all_fields(self, mock_bot_client, mock_llm_client):
        """DiscoveryResult should have all expected fields populated."""
        engine = BotDiscoveryEngine(
            bot_client=mock_bot_client,
            llm_client=mock_llm_client,
            max_turns=5,
        )

        result = await engine.discover()

        assert hasattr(result, "context")
        assert hasattr(result, "strategy_summary")
        assert hasattr(result, "execution_time_seconds")
        assert hasattr(result, "success")
        assert hasattr(result, "error_message")
        assert hasattr(result, "diagnostic_report")
        assert result.execution_time_seconds > 0
