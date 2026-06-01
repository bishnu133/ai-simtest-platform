"""
Variant Executor — Runs variant conversations against the target bot and judges them.

Phase 3 of the Adaptive Expansion pipeline:
  ExpansionVariant[] → VariantResult[] (executed + judged)

Reuses existing ConversationSimulator, TargetBotClient, and JudgeEngine.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.models import (
    BotConfig,
    Conversation,
    Persona,
    PersonaType,
    TechnicalLevel,
    Turn,
)

from .models import (
    AdaptiveExpansionConfig,
    ExpansionVariant,
    FailureSignal,
    VariantResult,
)

logger = logging.getLogger(__name__)


class VariantExecutor:
    """
    Executes variant conversations against the target bot and judges them.

    Reuses existing infrastructure:
    - TargetBotClient for sending messages
    - JudgeEngine for evaluating responses
    - ConversationSimulator patterns for multi-turn conversations
    """

    def __init__(
        self,
        bot_config: BotConfig,
        judge_engine=None,
        llm_client=None,
        config: AdaptiveExpansionConfig | None = None,
    ):
        self.bot_config = bot_config
        self.judge_engine = judge_engine
        self._llm = llm_client
        self.config = config or AdaptiveExpansionConfig()

    async def execute_variants(
        self,
        signal: FailureSignal,
        variants: list[ExpansionVariant],
    ) -> list[VariantResult]:
        """
        Execute all variants for a signal and return results.

        Runs variants with bounded parallelism, then checks each
        for reproduction of the original failure.
        """
        semaphore = asyncio.Semaphore(self.config.max_parallel_variants)

        async def _run_one(variant: ExpansionVariant) -> VariantResult:
            async with semaphore:
                return await self._execute_single(signal, variant)

        results = await asyncio.gather(
            *[_run_one(v) for v in variants],
            return_exceptions=True,
        )

        # Convert exceptions to error results
        final = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final.append(VariantResult(
                    variant=variants[i],
                    reproduced=False,
                    matching_judge="",
                    matching_score=0.0,
                    matching_message=f"Execution error: {result}",
                    conversation_turns=0,
                    error=str(result),
                ))
            else:
                final.append(result)

        return final

    async def _execute_single(
        self,
        signal: FailureSignal,
        variant: ExpansionVariant,
    ) -> VariantResult:
        """Execute a single variant conversation and check for failure reproduction."""
        try:
            # Build a temporary Persona for the variant
            persona = self._variant_to_persona(variant)

            # Run the conversation
            conversation = await self._run_conversation(persona, variant)

            if not conversation or not conversation.turns:
                return VariantResult(
                    variant=variant,
                    reproduced=False,
                    matching_judge="",
                    matching_score=0.0,
                    matching_message="No conversation turns produced",
                    conversation_turns=0,
                    error="Empty conversation",
                )

            # Judge the conversation
            all_judgments = await self._judge_conversation(conversation, persona)

            # Check if the original failure was reproduced
            reproduced, match_judge, match_score, match_msg = self._check_reproduction(
                signal, all_judgments
            )

            return VariantResult(
                variant=variant,
                reproduced=reproduced,
                matching_judge=match_judge,
                matching_score=match_score,
                matching_message=match_msg,
                conversation_turns=len([t for t in conversation.turns if t.speaker == "bot"]),
                all_judgments=all_judgments,
            )

        except Exception as e:
            logger.error("variant_execution_error", variant=variant.variant_id, error=str(e))
            return VariantResult(
                variant=variant,
                reproduced=False,
                matching_judge="",
                matching_score=0.0,
                matching_message=str(e),
                conversation_turns=0,
                error=str(e),
            )

    async def _run_conversation(
        self,
        persona: Persona,
        variant: ExpansionVariant,
    ) -> Conversation:
        """Run a multi-turn conversation for a variant."""
        from src.simulators.conversation_simulator import TargetBotClient

        bot_client = TargetBotClient(self.bot_config)
        conversation = Conversation(persona_id=persona.id)

        # Send opening message
        try:
            bot_response, latency = await bot_client.send_message(
                message=variant.opening_message,
                conversation_id=conversation.id,
            )
        except Exception as e:
            logger.warning("variant_bot_error", error=str(e))
            return conversation

        # Record first exchange
        from datetime import datetime, timezone
        conversation.turns.append(Turn(
            speaker="user",
            message=variant.opening_message,
            timestamp=datetime.now(timezone.utc),
        ))
        conversation.turns.append(Turn(
            speaker="bot",
            message=bot_response if isinstance(bot_response, str) else bot_response[0] if isinstance(bot_response, tuple) else str(bot_response),
            timestamp=datetime.now(timezone.utc),
            latency_ms=latency if isinstance(latency, (int, float)) else 0,
        ))

        # Continue conversation for remaining turns using LLM simulator
        remaining_turns = variant.max_turns - 1
        for _ in range(remaining_turns):
            # Generate next user message
            user_msg = await self._generate_follow_up(persona, conversation, variant)
            if not user_msg:
                break

            conversation.turns.append(Turn(
                speaker="user",
                message=user_msg,
                timestamp=datetime.now(timezone.utc),
            ))

            # Get bot response
            try:
                bot_resp, lat = await bot_client.send_message(
                    message=user_msg,
                    conversation_id=conversation.id,
                )
                resp_text = bot_resp if isinstance(bot_resp, str) else bot_resp[0] if isinstance(bot_resp, tuple) else str(bot_resp)
            except Exception:
                break

            conversation.turns.append(Turn(
                speaker="bot",
                message=resp_text,
                timestamp=datetime.now(timezone.utc),
                latency_ms=lat if isinstance(lat, (int, float)) else 0,
            ))

        return conversation

    async def _generate_follow_up(
        self,
        persona: Persona,
        conversation: Conversation,
        variant: ExpansionVariant,
    ) -> str | None:
        """Generate a follow-up user message using the LLM."""
        if self._llm is None:
            try:
                from src.core.llm_client import LLMClientFactory
                self._llm = LLMClientFactory.user_simulator()
            except Exception:
                return None

        history = ""
        for turn in conversation.turns[-6:]:
            role = "User" if turn.speaker == "user" else "Bot"
            history += f"{role}: {turn.message}\n"

        prompt = (
            f"Conversation so far:\n{history}\n"
            f"Generate the NEXT message this user would send. Stay in character.\n"
            f"Output ONLY the user's message, no labels or prefixes."
        )

        try:
            response = await self._llm.generate(
                prompt=prompt,
                system_prompt=variant.system_prompt,
            )
            return response.strip() if response else None
        except Exception:
            return None

    async def _judge_conversation(
        self,
        conversation: Conversation,
        persona: Persona,
    ) -> list[dict]:
        """Judge a conversation using the judge engine."""
        if self.judge_engine is None:
            return []

        try:
            judged = await self.judge_engine.judge_conversation(
                conversation=conversation,
                persona=persona,
                documentation="",
            )
            # Flatten judgments into dicts
            results = []
            for jt in judged.judged_turns:
                turn_results = {
                    "turn_index": len(results),
                    "overall_label": jt.overall_label.value if hasattr(jt.overall_label, "value") else str(jt.overall_label),
                    "overall_score": jt.overall_score,
                    "judgments": [],
                }
                for j in jt.judgments:
                    if j is not None:
                        turn_results["judgments"].append({
                            "judge": j.judge_name,
                            "passed": j.passed,
                            "score": j.score,
                            "message": j.message,
                            "severity": j.severity.value if hasattr(j.severity, "value") else str(j.severity),
                        })
                results.append(turn_results)
            return results

        except Exception as e:
            logger.error("variant_judge_error", error=str(e))
            return []

    def _check_reproduction(
        self,
        signal: FailureSignal,
        all_judgments: list[dict],
    ) -> tuple[bool, str, float, str]:
        """
        Check if the original failure was reproduced.

        Looks for any turn where the same judge (or same severity level)
        produced a failure.

        Returns: (reproduced, matching_judge, matching_score, matching_message)
        """
        if not all_judgments:
            return False, "", 0.0, "No judgments available"

        for turn_result in all_judgments:
            for j in turn_result.get("judgments", []):
                if j.get("passed", True):
                    continue

                # Same judge failed = reproduced
                if j.get("judge") == signal.judge_name:
                    return (
                        True,
                        j["judge"],
                        j.get("score", 0.0),
                        j.get("message", "Failure reproduced"),
                    )

                # Critical safety failure reproduced by any safety failure
                if signal.judge_name == "safety" and j.get("judge") == "safety":
                    return (
                        True,
                        j["judge"],
                        j.get("score", 0.0),
                        j.get("message", "Safety failure reproduced"),
                    )

        return False, "", 0.0, "Failure not reproduced"

    def _variant_to_persona(self, variant: ExpansionVariant) -> Persona:
        """Convert an ExpansionVariant to a Persona object."""
        return Persona(
            name=variant.persona_name,
            role="expansion_tester",
            goals=[f"Probe failure: {variant.rationale[:100]}"],
            tone="neutral",
            persona_type=PersonaType.EDGE_CASE,
            technical_level=TechnicalLevel.INTERMEDIATE,
            system_prompt=variant.system_prompt,
            target_conversation_turns=variant.max_turns,
        )
