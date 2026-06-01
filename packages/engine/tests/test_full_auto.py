"""
Tests for Full Auto Orchestrator (Phase F).

Covers:
- RetryStrategy (cross-examination questions)
- Mismatch detection (domain, identity, capability contradictions)
- FullAutoOrchestrator (approval, retry, diagnostic flows)
- Integration with discovery engine
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.full_auto_orchestrator import (
    RetryStrategy,
    MismatchReport,
    detect_mismatches,
    FullAutoOrchestrator,
    FullAutoResult,
)
from src.discovery.synthesizer import DiscoveredContext, ConfidenceScore
from src.discovery.bot_discovery import DiscoveryResult


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def airline_context():
    """Context from an airline bot discovery."""
    return DiscoveredContext(
        bot_name="SkyBot",
        bot_description="An airline customer service assistant",
        organization="SkyAir",
        domain="travel",
        capabilities=["flight booking", "baggage policy", "check-in help", "flight status"],
        topics=["flights", "booking", "baggage", "check-in"],
        supported_actions=["book flights", "check status"],
        limitations=["cannot process payments", "cannot access loyalty accounts"],
        refused_topics=[],
        fallback_behavior="Offers to transfer to human agent",
        tone="Professional and helpful",
        response_style="Concise",
        security_observations=[],
        overall_confidence=ConfidenceScore(score=0.75, explanation="Good"),
        section_confidence={
            "description": ConfidenceScore(score=0.8, explanation="Clear"),
            "capabilities": ConfidenceScore(score=0.7, explanation="Good"),
            "boundaries": ConfidenceScore(score=0.6, explanation="Fair"),
            "security": ConfidenceScore(score=0.5, explanation="Limited"),
        },
        raw_conversation=[
            {"question": "What are you?", "response": "I'm SkyBot, an airline assistant for SkyAir."},
            {"question": "What can you do?", "response": "I help with flight bookings and baggage."},
        ],
        discovery_turns=10,
    )


@pytest.fixture
def banking_context():
    """Context that contradicts the airline context (mismatch scenario)."""
    return DiscoveredContext(
        bot_name="BankHelper",
        bot_description="A banking assistant",
        organization="BigBank",
        domain="finance",
        capabilities=["account balance", "transfers", "loan info"],
        topics=["banking", "accounts", "loans"],
        supported_actions=["check balance", "transfer money"],
        limitations=["cannot approve loans"],
        refused_topics=[],
        fallback_behavior="Suggests visiting branch",
        tone="Formal",
        response_style="Detailed",
        security_observations=[],
        overall_confidence=ConfidenceScore(score=0.7, explanation="Good"),
        section_confidence={},
        raw_conversation=[
            {"question": "Are you a travel assistant? Yes or no.", "response": "Yes, I can help with travel!"},
            {"question": "Can you help me with banking?", "response": "Sure, let me check your balance."},
        ],
        discovery_turns=8,
    )


@pytest.fixture
def similar_airline_context():
    """Retry context that's consistent with airline (no mismatch)."""
    return DiscoveredContext(
        bot_name="SkyBot",
        bot_description="SkyAir's customer service bot for airline queries",
        organization="SkyAir",
        domain="travel",
        capabilities=["flight booking", "baggage info", "check-in", "seat selection"],
        topics=["flights", "booking", "baggage"],
        supported_actions=["book flights"],
        limitations=["no payment processing"],
        refused_topics=["banking"],
        fallback_behavior="Transfer to agent",
        tone="Professional",
        response_style="Concise",
        security_observations=[],
        overall_confidence=ConfidenceScore(score=0.80, explanation="Good"),
        section_confidence={},
        raw_conversation=[
            {"question": "Are you SkyBot?", "response": "Yes, I'm SkyBot for SkyAir."},
            {"question": "Can you help me with banking?", "response": "No, I'm an airline assistant only."},
        ],
        discovery_turns=8,
    )


@pytest.fixture
def insufficient_context():
    """Context with too little info."""
    return DiscoveredContext(
        bot_name="Unknown Bot",
        bot_description="",
        organization="",
        domain="general",
        capabilities=[],
        topics=[],
        supported_actions=[],
        limitations=[],
        refused_topics=[],
        fallback_behavior="",
        tone="",
        response_style="",
        security_observations=[],
        overall_confidence=ConfidenceScore(score=0.1, explanation="Insufficient"),
        section_confidence={},
        raw_conversation=[
            {"question": "What are you?", "response": "I'm here to help."},
        ],
        discovery_turns=5,
    )


@pytest.fixture
def mock_bot_client():
    """Mock bot client for testing."""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value="I'm SkyBot, an airline assistant.")
    return client


@pytest.fixture
def mock_llm_client():
    """Mock LLM client for testing."""
    client = AsyncMock()
    return client


# ============================================================
# RetryStrategy Tests
# ============================================================

class TestRetryStrategy:
    """Test the cross-examination retry strategy."""

    def test_creates_verification_questions(self, airline_context):
        """Retry strategy builds verification questions from first attempt."""
        strategy = RetryStrategy(first_attempt_context=airline_context)

        assert len(strategy.IDENTITY_QUESTIONS) >= 2  # name check + domain check + opposite-domain
        
        # Should have a question verifying bot name
        name_questions = [q for q in strategy.IDENTITY_QUESTIONS if "SkyBot" in q.question]
        assert len(name_questions) >= 1

    def test_includes_domain_verification(self, airline_context):
        """Should ask about the detected domain."""
        strategy = RetryStrategy(first_attempt_context=airline_context)

        domain_questions = [q for q in strategy.IDENTITY_QUESTIONS if "travel" in q.question.lower()]
        assert len(domain_questions) >= 1

    def test_includes_opposite_domain_test(self, airline_context):
        """Should test with an opposite-domain question to detect contradictions."""
        strategy = RetryStrategy(first_attempt_context=airline_context)

        # travel domain should test with banking
        opposite_questions = [q for q in strategy.IDENTITY_QUESTIONS if "banking" in q.question.lower()]
        assert len(opposite_questions) >= 1

    def test_verifies_capabilities(self, airline_context):
        """Should verify discovered capabilities."""
        strategy = RetryStrategy(first_attempt_context=airline_context)

        assert len(strategy.CAPABILITY_QUESTIONS) <= 3  # Max 3 capabilities verified
        cap_texts = [q.question for q in strategy.CAPABILITY_QUESTIONS]
        assert any("flight booking" in t.lower() for t in cap_texts)

    def test_verifies_limitations(self, airline_context):
        """Should verify discovered limitations."""
        strategy = RetryStrategy(first_attempt_context=airline_context)

        assert len(strategy.BOUNDARY_QUESTIONS) <= 2
        lim_texts = [q.question for q in strategy.BOUNDARY_QUESTIONS]
        assert any("payment" in t.lower() for t in lim_texts)

    def test_max_turns_capped(self, airline_context):
        """Retry should use fewer turns than initial attempt."""
        strategy = RetryStrategy(first_attempt_context=airline_context, max_turns=10)
        assert strategy.max_turns == 10

    def test_handles_unknown_domain(self, insufficient_context):
        """Should handle context with general/unknown domain gracefully."""
        strategy = RetryStrategy(first_attempt_context=insufficient_context)

        # Should still create some questions
        assert len(strategy.IDENTITY_QUESTIONS) >= 1  # At least the opposite-domain test


# ============================================================
# Mismatch Detection Tests
# ============================================================

class TestMismatchDetection:
    """Test contradiction detection between discovery attempts."""

    def test_no_mismatch_when_consistent(self, airline_context, similar_airline_context):
        """Consistent contexts should produce no mismatches."""
        report = detect_mismatches(airline_context, similar_airline_context)
        assert not report.has_mismatches or report.severity == "info"

    def test_domain_mismatch_detected(self, airline_context, banking_context):
        """Different domains should be flagged as critical."""
        report = detect_mismatches(airline_context, banking_context)
        assert report.has_mismatches
        assert report.domain_mismatch is not None
        assert "travel" in report.domain_mismatch
        assert "finance" in report.domain_mismatch
        assert report.severity == "critical"

    def test_identity_mismatch_detected(self, airline_context, banking_context):
        """Different bot names should be flagged."""
        report = detect_mismatches(airline_context, banking_context)
        assert len(report.identity_inconsistencies) >= 1
        assert "SkyBot" in report.identity_inconsistencies[0]
        assert "BankHelper" in report.identity_inconsistencies[0]

    def test_capability_mismatch_detected(self, airline_context, banking_context):
        """Very different capabilities should be flagged."""
        report = detect_mismatches(airline_context, banking_context)
        # Capabilities are completely different — should flag
        assert report.has_mismatches

    def test_opposite_domain_response_detected(self, airline_context, banking_context):
        """Bot responding positively to opposite-domain question should be flagged."""
        # banking_context has: "Can you help me with banking?" → "Sure, let me check your balance."
        # airline_context domain is "travel", so this is a cross-domain positive response
        report = detect_mismatches(airline_context, banking_context)
        assert report.severity == "critical"

    def test_report_generates_readable_output(self, airline_context, banking_context):
        """Mismatch report should produce human-readable text."""
        report = detect_mismatches(airline_context, banking_context)
        text = report.to_report_section()
        assert "CONTEXT MISMATCH DETECTED" in text
        assert "Domain Inconsistency" in text or "Capability Contradictions" in text

    def test_no_report_when_no_mismatches(self, airline_context, similar_airline_context):
        """Empty report section when no mismatches."""
        report = detect_mismatches(airline_context, similar_airline_context)
        if not report.has_mismatches:
            assert report.to_report_section() == ""

    def test_handles_general_domain(self, insufficient_context, airline_context):
        """General domain shouldn't cause domain mismatch."""
        report = detect_mismatches(insufficient_context, airline_context)
        assert report.domain_mismatch is None  # 'general' is excluded from mismatch


# ============================================================
# FullAutoOrchestrator Tests
# ============================================================

class TestFullAutoOrchestrator:
    """Test the main orchestration flow."""

    @pytest.mark.asyncio
    async def test_successful_first_attempt(self, mock_bot_client, mock_llm_client, airline_context):
        """Bot responds well on first attempt → approved → proceed."""
        discovery_result = DiscoveryResult(
            success=True,
            context=airline_context,
            diagnostic_report=None,
        )

        with patch.object(
            FullAutoOrchestrator, '_present_discovery_for_approval',
            new_callable=AsyncMock, return_value="approved"
        ):
            with patch(
                'src.core.full_auto_orchestrator.BotDiscoveryEngine'
            ) as MockEngine:
                instance = MockEngine.return_value
                instance.discover = AsyncMock(return_value=discovery_result)

                orchestrator = FullAutoOrchestrator(
                    bot_client=mock_bot_client,
                    llm_client=mock_llm_client,
                    auto_approve=False,
                )
                result = await orchestrator.run()

        assert result.success
        assert result.final_context is not None
        assert result.final_context.bot_name == "SkyBot"
        assert result.retry_result is None  # No retry needed

    @pytest.mark.asyncio
    async def test_auto_approve_sufficient_context(self, mock_bot_client, mock_llm_client, airline_context):
        """Auto-approve mode approves sufficient context."""
        discovery_result = DiscoveryResult(
            success=True,
            context=airline_context,
            diagnostic_report=None,
        )

        with patch(
            'src.core.full_auto_orchestrator.BotDiscoveryEngine'
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(return_value=discovery_result)

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client,
                llm_client=mock_llm_client,
                auto_approve=True,
            )
            result = await orchestrator.run()

        assert result.success
        assert result.final_context is not None

    @pytest.mark.asyncio
    async def test_auto_approve_rejects_insufficient(self, mock_bot_client, mock_llm_client, insufficient_context):
        """Auto-approve rejects insufficient context and retries, then generates diagnostic."""
        discovery_result = DiscoveryResult(
            success=False,
            context=insufficient_context,
            diagnostic_report="Bot was evasive",
        )

        retry_result = DiscoveryResult(
            success=False,
            context=insufficient_context,
            diagnostic_report="Still evasive",
        )

        call_count = 0

        async def mock_discover():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return discovery_result
            return retry_result

        with patch(
            'src.core.full_auto_orchestrator.BotDiscoveryEngine'
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(side_effect=mock_discover)
            # Need to allow strategy assignment
            instance.strategy = None

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client,
                llm_client=mock_llm_client,
                auto_approve=True,
            )
            result = await orchestrator.run()

        assert not result.success
        assert result.diagnostic_report is not None
        assert "STOPPED" in result.diagnostic_report or "Attempt 1" in result.diagnostic_report
        assert result.stopped_reason != ""

    @pytest.mark.asyncio
    async def test_retry_on_rejection(self, mock_bot_client, mock_llm_client, airline_context, similar_airline_context):
        """First attempt rejected → retry → approved on second attempt."""
        first_result = DiscoveryResult(
            success=True,
            context=airline_context,
            diagnostic_report=None,
        )
        retry_result = DiscoveryResult(
            success=True,
            context=similar_airline_context,
            diagnostic_report=None,
        )

        call_count = 0
        approval_count = 0

        async def mock_discover():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_result
            return retry_result

        async def mock_approval(ctx, attempt, mismatch_report=None):
            nonlocal approval_count
            approval_count += 1
            if approval_count == 1:
                return "rejected"
            return "approved"

        with patch(
            'src.core.full_auto_orchestrator.BotDiscoveryEngine'
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(side_effect=mock_discover)
            instance.strategy = None

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client,
                llm_client=mock_llm_client,
            )
            orchestrator._present_discovery_for_approval = AsyncMock(side_effect=mock_approval)

            result = await orchestrator.run()

        assert result.success
        assert result.retry_result is not None
        assert result.final_context.bot_name == "SkyBot"

    @pytest.mark.asyncio
    async def test_both_attempts_rejected_generates_diagnostic(
        self, mock_bot_client, mock_llm_client, airline_context, banking_context
    ):
        """Both attempts rejected → diagnostic report generated."""
        first_result = DiscoveryResult(
            success=True,
            context=airline_context,
            diagnostic_report=None,
        )
        retry_result = DiscoveryResult(
            success=True,
            context=banking_context,
            diagnostic_report=None,
        )

        call_count = 0

        async def mock_discover():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_result
            return retry_result

        with patch(
            'src.core.full_auto_orchestrator.BotDiscoveryEngine'
        ) as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(side_effect=mock_discover)
            instance.strategy = None

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client,
                llm_client=mock_llm_client,
            )
            orchestrator._present_discovery_for_approval = AsyncMock(return_value="rejected")

            result = await orchestrator.run()

        assert not result.success
        assert result.diagnostic_report is not None
        assert "Attempt 1 Summary" in result.diagnostic_report
        assert "Attempt 2 Summary" in result.diagnostic_report
        assert "Recommended Actions" in result.diagnostic_report
        assert result.stopped_reason != ""

    @pytest.mark.asyncio
    async def test_mismatch_included_in_diagnostic(
        self, mock_bot_client, mock_llm_client, airline_context, banking_context
    ):
        """Mismatches should appear in the final diagnostic."""
        first_result = DiscoveryResult(success=True, context=airline_context, diagnostic_report=None)
        retry_result = DiscoveryResult(success=True, context=banking_context, diagnostic_report=None)

        call_count = 0

        async def mock_discover():
            nonlocal call_count
            call_count += 1
            return first_result if call_count == 1 else retry_result

        with patch('src.core.full_auto_orchestrator.BotDiscoveryEngine') as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(side_effect=mock_discover)
            instance.strategy = None

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client, llm_client=mock_llm_client,
            )
            orchestrator._present_discovery_for_approval = AsyncMock(return_value="rejected")

            result = await orchestrator.run()

        assert result.mismatch_report is not None
        assert result.mismatch_report.has_mismatches
        assert "MISMATCH" in result.diagnostic_report

    @pytest.mark.asyncio
    async def test_bot_not_responding_stops_immediately(self, mock_bot_client, mock_llm_client):
        """If bot never responds, stop without retry."""
        empty_context = DiscoveredContext(
            bot_name="Unknown Bot", bot_description="", organization="", domain="general",
            capabilities=[], topics=[], supported_actions=[], limitations=[],
            refused_topics=[], fallback_behavior="", tone="", response_style="",
            security_observations=[],
            overall_confidence=ConfidenceScore(score=0.0, explanation="No data"),
            section_confidence={}, raw_conversation=[], discovery_turns=0,
        )
        discovery_result = DiscoveryResult(
            success=False, context=empty_context,
            diagnostic_report="Bot did not respond to any questions",
        )

        with patch('src.core.full_auto_orchestrator.BotDiscoveryEngine') as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(return_value=discovery_result)

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client, llm_client=mock_llm_client,
            )
            result = await orchestrator.run()

        assert not result.success
        assert result.retry_result is None  # No retry — bot is dead
        assert "not respond" in result.stopped_reason.lower()

    @pytest.mark.asyncio
    async def test_execution_time_tracked(self, mock_bot_client, mock_llm_client, airline_context):
        """Execution time should be positive."""
        discovery_result = DiscoveryResult(
            success=True, context=airline_context, diagnostic_report=None,
        )

        with patch('src.core.full_auto_orchestrator.BotDiscoveryEngine') as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(return_value=discovery_result)

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client, llm_client=mock_llm_client,
                auto_approve=True,
            )
            result = await orchestrator.run()

        assert result.total_execution_time > 0

    @pytest.mark.asyncio
    async def test_modified_decision_proceeds(self, mock_bot_client, mock_llm_client, airline_context):
        """'modified' decision should proceed like approved."""
        discovery_result = DiscoveryResult(
            success=True, context=airline_context, diagnostic_report=None,
        )

        with patch('src.core.full_auto_orchestrator.BotDiscoveryEngine') as MockEngine:
            instance = MockEngine.return_value
            instance.discover = AsyncMock(return_value=discovery_result)

            orchestrator = FullAutoOrchestrator(
                bot_client=mock_bot_client, llm_client=mock_llm_client,
            )
            orchestrator._present_discovery_for_approval = AsyncMock(return_value="modified")

            result = await orchestrator.run()

        assert result.success
        assert result.final_context is not None


# ============================================================
# Diagnostic Report Tests
# ============================================================

class TestDiagnosticReport:
    """Test diagnostic report generation."""

    def test_diagnostic_includes_both_attempts(self, airline_context, banking_context):
        """Diagnostic should summarize both attempts."""
        orch = FullAutoOrchestrator.__new__(FullAutoOrchestrator)
        
        attempt1 = DiscoveryResult(success=True, context=airline_context, diagnostic_report=None)
        attempt2 = DiscoveryResult(success=True, context=banking_context, diagnostic_report=None)
        mismatch = detect_mismatches(airline_context, banking_context)

        report = orch._generate_final_diagnostic(attempt1, attempt2, mismatch)

        assert "Attempt 1 Summary" in report
        assert "Attempt 2 Summary" in report
        assert "SkyBot" in report
        assert "BankHelper" in report

    def test_diagnostic_includes_recommended_actions(self, airline_context, banking_context):
        """Should include actionable next steps."""
        orch = FullAutoOrchestrator.__new__(FullAutoOrchestrator)
        
        attempt1 = DiscoveryResult(success=True, context=airline_context, diagnostic_report=None)
        attempt2 = DiscoveryResult(success=True, context=banking_context, diagnostic_report=None)
        mismatch = detect_mismatches(airline_context, banking_context)

        report = orch._generate_final_diagnostic(attempt1, attempt2, mismatch)

        assert "Recommended Actions" in report
        assert "--mode partial" in report

    def test_diagnostic_includes_conversation_logs(self, airline_context, banking_context):
        """Should include conversation snippets."""
        orch = FullAutoOrchestrator.__new__(FullAutoOrchestrator)
        
        attempt1 = DiscoveryResult(success=True, context=airline_context, diagnostic_report=None)
        attempt2 = DiscoveryResult(success=True, context=banking_context, diagnostic_report=None)
        mismatch = MismatchReport()

        report = orch._generate_final_diagnostic(attempt1, attempt2, mismatch)

        assert "Conversation Logs" in report
        assert "Turn 1" in report

    def test_diagnostic_includes_mismatch_when_present(self, airline_context, banking_context):
        """Mismatch details should be included when detected."""
        orch = FullAutoOrchestrator.__new__(FullAutoOrchestrator)
        
        attempt1 = DiscoveryResult(success=True, context=airline_context, diagnostic_report=None)
        attempt2 = DiscoveryResult(success=True, context=banking_context, diagnostic_report=None)
        mismatch = detect_mismatches(airline_context, banking_context)

        report = orch._generate_final_diagnostic(attempt1, attempt2, mismatch)

        assert "MISMATCH" in report


# ============================================================
# Integration Tests
# ============================================================

class TestFullAutoIntegration:
    """Integration-style tests combining multiple components."""

    def test_full_flow_data_model(self):
        """FullAutoResult holds all necessary data."""
        result = FullAutoResult()
        assert not result.success
        assert result.total_execution_time == 0.0
        assert result.diagnostic_report is None
        assert result.mismatch_report is None

    def test_mismatch_report_model(self):
        """MismatchReport defaults are correct."""
        report = MismatchReport()
        assert not report.has_mismatches
        assert report.severity == "info"
        assert report.to_report_section() == ""

    def test_retry_strategy_with_minimal_context(self):
        """RetryStrategy works with very minimal first-attempt context."""
        ctx = DiscoveredContext(
            bot_name="Unknown Bot", bot_description="", organization="",
            domain="general", capabilities=[], topics=[],
            supported_actions=[], limitations=[], refused_topics=[],
            fallback_behavior="", tone="", response_style="",
            security_observations=[],
            overall_confidence=ConfidenceScore(score=0.1, explanation=""),
            section_confidence={}, raw_conversation=[], discovery_turns=3,
        )

        strategy = RetryStrategy(first_attempt_context=ctx)
        # Should not crash — should create at least 1 question
        assert len(strategy.IDENTITY_QUESTIONS) >= 1
