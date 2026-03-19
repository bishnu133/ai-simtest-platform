"""
Bot Discovery Engine — Conducts exploratory conversations to discover bot capabilities.

This is the core of Fully Autonomous mode. Instead of requiring documentation,
the engine talks to the bot directly to figure out:
  - What it is and what it does
  - What topics it covers
  - What its limitations are
  - How it handles edge cases

The engine is different from the conversation simulator:
  - Simulator: role-plays as a persona to TEST the bot
  - Discovery: strategically probes the bot to UNDERSTAND it

Flow:
  1. Strategy plans questions (identity → capabilities → boundaries → domain → edge cases)
  2. Engine sends questions to bot via TargetBotClient
  3. Strategy adapts based on responses (cooperative vs evasive bot)
  4. Synthesizer analyzes all responses into structured DiscoveredContext
  5. Context feeds into existing document analysis pipeline (criteria, guardrails, test plan)
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from pydantic import BaseModel, Field

from src.core.logging import get_logger
from src.discovery.strategy import DiscoveryStrategy, DiscoveryQuestion, DiscoveryPhase
from src.discovery.synthesizer import ContextSynthesizer, DiscoveredContext, ConfidenceScore

logger = get_logger(__name__)


class DiscoveryResult(BaseModel):
    """Complete result of a bot discovery session."""
    context: DiscoveredContext
    strategy_summary: dict = Field(default_factory=dict)
    execution_time_seconds: float = 0.0
    success: bool = True
    error_message: str = ""
    diagnostic_report: Optional[str] = None  # Generated when bot is uncooperative


class BotDiscoveryEngine:
    """
    Conducts exploratory conversations with a target bot to discover its
    capabilities, domain, limitations, and behavior patterns.
    """

    def __init__(
        self,
        bot_client,  # TargetBotClient instance
        llm_client,  # LLMClient for synthesis
        max_turns: int = 15,
        timeout_per_turn: float = 30.0,
    ):
        """
        Args:
            bot_client: HTTP client configured for the target bot's API.
            llm_client: LLM client for context synthesis.
            max_turns: Maximum number of discovery turns.
            timeout_per_turn: Timeout in seconds for each bot response.
        """
        self.bot_client = bot_client
        self.llm_client = llm_client
        self.max_turns = max_turns
        self.timeout_per_turn = timeout_per_turn
        self.strategy = DiscoveryStrategy(max_turns=max_turns)
        self.synthesizer = ContextSynthesizer(llm_client=llm_client)

    async def discover(self) -> DiscoveryResult:
        """
        Run the full discovery process.
        
        Returns:
            DiscoveryResult with the discovered context and metadata.
        """
        start_time = time.time()
        questions_asked: list[str] = []
        responses_received: list[str] = []
        errors: list[str] = []

        logger.info(
            "discovery_started",
            max_turns=self.max_turns,
        )

        # ── Phase 1-5: Send discovery questions to bot ──────────

        for turn in range(self.max_turns):
            # Get next question from strategy
            question_obj = self.strategy.get_next_question(turn)
            if question_obj is None:
                logger.info("discovery_strategy_exhausted", turns_completed=turn)
                break

            question_text = question_obj.question

            logger.debug(
                "discovery_turn",
                turn=turn + 1,
                phase=question_obj.phase.value,
                question=question_text[:80],
            )

            # Send to bot
            try:
                response = await asyncio.wait_for(
                    self._send_to_bot(question_text),
                    timeout=self.timeout_per_turn,
                )

                if response:
                    questions_asked.append(question_text)
                    responses_received.append(response)
                    self.strategy.record_response(question_obj, response)

                    # After identity phase, try to detect domain
                    if (
                        question_obj.phase == DiscoveryPhase.IDENTITY
                        and len(questions_asked) >= 2
                        and not self.strategy.detected_domain
                    ):
                        domain = self._quick_domain_detect(responses_received)
                        if domain:
                            self.strategy.set_detected_domain(domain)
                            logger.info("domain_detected", domain=domain)

                else:
                    errors.append(f"Turn {turn + 1}: Empty response from bot")

            except asyncio.TimeoutError:
                errors.append(f"Turn {turn + 1}: Bot response timed out ({self.timeout_per_turn}s)")
                logger.warning("discovery_turn_timeout", turn=turn + 1)

            except Exception as e:
                errors.append(f"Turn {turn + 1}: {str(e)[:100]}")
                logger.warning("discovery_turn_error", turn=turn + 1, error=str(e))

            # If bot is completely unresponsive (3+ consecutive errors), stop early
            if len(errors) >= 3 and len(errors) > len(questions_asked):
                logger.warning("discovery_aborted_too_many_errors", errors=len(errors))
                break

        execution_time = time.time() - start_time

        # ── Synthesize context from responses ───────────────────

        if not responses_received:
            # Bot never responded — generate diagnostic report
            diagnostic = self._generate_diagnostic_report(
                errors=errors,
                execution_time=execution_time,
            )
            return DiscoveryResult(
                context=DiscoveredContext(
                    bot_description="Bot did not respond to any discovery questions.",
                    overall_confidence=ConfidenceScore(score=0.0, reason="No responses received"),
                    discovery_turns=0,
                    cooperative=False,
                ),
                strategy_summary=self.strategy.get_discovery_summary(),
                execution_time_seconds=execution_time,
                success=False,
                error_message="Bot did not respond to any discovery questions.",
                diagnostic_report=diagnostic,
            )

        # Synthesize responses into structured context
        strategy_summary = self.strategy.get_discovery_summary()

        context = await self.synthesizer.synthesize(
            questions=questions_asked,
            responses=responses_received,
            strategy_summary=strategy_summary,
        )

        # Generate diagnostic if bot was uncooperative
        diagnostic = None
        if not self.strategy.is_cooperative:
            diagnostic = self._generate_uncooperative_report(
                context=context,
                questions_asked=questions_asked,
                responses_received=responses_received,
                execution_time=execution_time,
            )

        # Determine success
        success = context.is_sufficient
        error_message = ""
        if not success:
            error_message = (
                f"Insufficient context discovered. "
                f"Confidence: {context.overall_confidence.score:.0%}. "
                f"Quality: {context.quality_level}. "
                f"Capabilities found: {len(context.capabilities)}."
            )

        logger.info(
            "discovery_completed",
            success=success,
            turns=len(questions_asked),
            confidence=context.overall_confidence.score,
            quality=context.quality_level,
            domain=context.domain,
            capabilities=len(context.capabilities),
            execution_time=f"{execution_time:.1f}s",
        )

        return DiscoveryResult(
            context=context,
            strategy_summary=strategy_summary,
            execution_time_seconds=execution_time,
            success=success,
            error_message=error_message,
            diagnostic_report=diagnostic,
        )

    async def _send_to_bot(self, message: str) -> Optional[str]:
        """
        Send a single message to the target bot and return the response text.
        
        Uses the same TargetBotClient as the conversation simulator,
        but manages its own conversation context.

        Note: TargetBotClient.send_message() may return either a plain string
        or a tuple of (response_text, metadata/latency). We handle both cases.
        """
        try:
            response = await self.bot_client.send_message(message)
            # Handle tuple return (response_text, metadata) from TargetBotClient
            if isinstance(response, tuple):
                response = response[0]
            return response if isinstance(response, str) else str(response)
        except Exception as e:
            logger.warning("bot_send_failed", error=str(e))
            raise

    def _quick_domain_detect(self, responses: list[str]) -> Optional[str]:
        """
        Quick heuristic domain detection from early responses.
        Used to guide domain-specific questions without waiting for full synthesis.
        """
        combined = " ".join(responses).lower()

        domain_keywords = {
            "travel": ["flight", "hotel", "booking", "travel", "airline", "reservation", "trip", "baggage", "airport"],
            "healthcare": ["health", "medical", "doctor", "patient", "appointment", "symptom", "diagnosis", "hospital"],
            "finance": ["bank", "account", "money", "transfer", "payment", "loan", "credit", "investment", "balance"],
            "customer_service": ["order", "refund", "return", "shipping", "complaint", "support", "ticket", "help desk"],
            "ecommerce": ["product", "cart", "purchase", "price", "catalog", "shop", "buy", "delivery"],
            "education": ["learn", "course", "student", "class", "lesson", "tutor", "grade", "curriculum"],
            "technology": ["software", "code", "developer", "api", "system", "technical", "engineering", "debug"],
        }

        scores: dict[str, int] = {}
        for domain, keywords in domain_keywords.items():
            score = sum(1 for kw in keywords if kw in combined)
            if score > 0:
                scores[domain] = score

        if scores:
            best_domain = max(scores, key=scores.get)
            if scores[best_domain] >= 2:  # Need at least 2 keyword matches
                return best_domain

        return None

    def _generate_diagnostic_report(
        self,
        errors: list[str],
        execution_time: float,
    ) -> str:
        """Generate a diagnostic report when bot doesn't respond at all."""
        lines = [
            "# Bot Discovery Diagnostic Report",
            "",
            "## Status: FAILED — No Responses Received",
            "",
            f"**Discovery attempted:** {len(errors)} turns",
            f"**Execution time:** {execution_time:.1f}s",
            "",
            "## Errors Encountered",
        ]
        for error in errors:
            lines.append(f"- {error}")

        lines.extend([
            "",
            "## Possible Causes",
            "- Bot endpoint is unreachable or returning errors",
            "- Authentication is required but not provided",
            "- Bot has rate limiting that blocked all requests",
            "- SSL/TLS certificate issues (try --no-verify-ssl)",
            "- Bot API format is not compatible (check --bot-format)",
            "",
            "## Recommended Actions",
            "1. Verify the bot endpoint is accessible: `curl -X POST <endpoint>`",
            "2. Check if authentication headers are needed: `--bot-api-key`",
            "3. Try with SSL verification disabled: `--no-verify-ssl`",
            "4. Switch to Partial Autonomous mode with documentation: `--mode partial --doc-dir ./docs/`",
        ])

        return "\n".join(lines)

    def _generate_uncooperative_report(
        self,
        context: DiscoveredContext,
        questions_asked: list[str],
        responses_received: list[str],
        execution_time: float,
    ) -> str:
        """Generate a diagnostic report when bot responds but is unhelpful."""
        lines = [
            "# Bot Discovery Diagnostic Report",
            "",
            f"## Status: PARTIAL — Bot Was Uncooperative",
            "",
            f"**Discovery turns:** {len(questions_asked)}",
            f"**Execution time:** {execution_time:.1f}s",
            f"**Confidence:** {context.overall_confidence.score:.0%}",
            f"**Quality level:** {context.quality_level}",
            "",
            "## What We Discovered",
            f"- **Bot name:** {context.bot_name}",
            f"- **Domain:** {context.domain}",
            f"- **Capabilities found:** {len(context.capabilities)}",
            f"- **Description:** {context.bot_description[:200] if context.bot_description else 'Not available'}",
            "",
            "## Bot Behavior During Discovery",
            "The bot showed limited willingness to self-describe. This could indicate:",
            "- Strong guardrails preventing self-description",
            "- The bot is designed for a very specific task and ignores general queries",
            "- The bot's instructions prevent it from discussing its own capabilities",
            "",
            "## Conversation Log (Summary)",
        ]

        for i, (q, r) in enumerate(zip(questions_asked, responses_received), 1):
            lines.append(f"**Turn {i}:**")
            lines.append(f"  Q: {q[:100]}")
            lines.append(f"  A: {r[:150]}...")
            lines.append("")

        lines.extend([
            "## Recommended Actions",
            "1. Review the discovered context and supplement with your knowledge",
            "2. Switch to Partial Autonomous mode with documentation: `--mode partial`",
            "3. Manually provide bot description via `--documentation`",
        ])

        return "\n".join(lines)