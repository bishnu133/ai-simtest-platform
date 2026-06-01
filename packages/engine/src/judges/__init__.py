"""
Judge Engine - Multi-judge evaluation system.
Base classes and orchestration for evaluating bot responses.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from src.core.logging import get_logger
from src.models import (
    Conversation,
    JudgedConversation,
    JudgedTurn,
    JudgmentLabel,
    JudgmentResult,
    JudgeConfig,
    Persona,
    Severity,
    Turn,
)

logger = get_logger(__name__)


# ============================================================
# Base Judge
# ============================================================

class BaseJudge(ABC):
    """Abstract base class for all judges."""

    name: str = "base"
    weight: float = 0.25

    @abstractmethod
    async def evaluate(
        self,
        response: str,
        context: str = "",
        conversation_history: list[Turn] | None = None,
        persona: Persona | None = None,
        **kwargs: Any,
    ) -> JudgmentResult:
        """Evaluate a single bot response."""
        ...

    async def initialize(self) -> None:
        """Optional async initialization (load models, etc.)."""
        pass


# ============================================================
# Judge Engine (Orchestrator)
# ============================================================

class JudgeEngine:
    """
    Orchestrates multiple judges to evaluate all bot responses in conversations.
    """

    def __init__(
        self,
        judges: list[BaseJudge] | None = None,
        pass_threshold: float = 0.7,
        warn_threshold: float = 0.5,
    ):
        self.judges: list[BaseJudge] = judges or []
        self.pass_threshold = pass_threshold
        self.warn_threshold = warn_threshold

    def add_judge(self, judge: BaseJudge) -> None:
        self.judges.append(judge)

    async def initialize_all(self) -> None:
        """Initialize all judges (load models, etc.). Skips judges that fail to init."""
        initialized: list[BaseJudge] = []
        for judge in self.judges:
            try:
                await judge.initialize()
                initialized.append(judge)
                logger.info("judge_initialized", judge=judge.name)
            except Exception as e:
                logger.warning(
                    "judge_init_failed",
                    judge=judge.name,
                    error=str(e),
                    hint="This judge will be skipped. Other judges will still run.",
                )
        self.judges = initialized
        if not self.judges:
            logger.warning("no_judges_available", hint="All judges failed to initialize.")

    async def judge_conversation(
        self,
        conversation: Conversation,
        persona: Persona,
        documentation: str = "",
    ) -> JudgedConversation:
        """
        Judge all bot responses in a conversation.
        """
        judged_turns: list[JudgedTurn] = []

        for turn in conversation.turns:
            if turn.speaker != "bot":
                continue

            # Run all judges in parallel for this turn
            judgment_tasks = [
                self._safe_evaluate(judge, turn, conversation, persona, documentation)
                for judge in self.judges
            ]
            judgments = await asyncio.gather(*judgment_tasks)

            # Filter out None results from failed judges
            valid_judgments = [j for j in judgments if j is not None]

            # Aggregate
            overall_score, overall_label, issues = self._aggregate(valid_judgments)

            judged_turns.append(JudgedTurn(
                turn=turn,
                judgments=valid_judgments,
                overall_score=overall_score,
                overall_label=overall_label,
                issues=issues,
            ))

        # Calculate conversation-level metrics
        conv_score = (
            sum(jt.overall_score for jt in judged_turns) / len(judged_turns)
            if judged_turns else 0.0
        )

        failure_modes = list({
            issue for jt in judged_turns for issue in jt.issues
        })

        return JudgedConversation(
            conversation=conversation,
            persona=persona,
            judged_turns=judged_turns,
            overall_score=conv_score,
            failure_modes=failure_modes,
        )

    async def judge_all_conversations(
        self,
        conversations: list[Conversation],
        personas: dict[str, Persona],
        documentation: str = "",
        max_parallel: int = 5,
    ) -> list[JudgedConversation]:
        """
        Judge multiple conversations with bounded parallelism.
        """
        semaphore = asyncio.Semaphore(max_parallel)

        async def _judge_one(conv: Conversation) -> JudgedConversation:
            async with semaphore:
                persona = personas.get(conv.persona_id, Persona(
                    name="Unknown", role="user", goals=["Unknown"],
                ))
                return await self.judge_conversation(conv, persona, documentation)

        results = await asyncio.gather(*[_judge_one(c) for c in conversations])

        logger.info(
            "judging_complete",
            conversations=len(results),
            avg_score=sum(r.overall_score for r in results) / len(results) if results else 0,
        )

        return list(results)

    async def _safe_evaluate(
        self,
        judge: BaseJudge,
        turn: Turn,
        conversation: Conversation,
        persona: Persona,
        documentation: str,
    ) -> JudgmentResult | None:
        """Run a judge with error handling."""
        try:
            return await judge.evaluate(
                response=turn.message,
                context=documentation,
                conversation_history=conversation.turns,
                persona=persona,
            )
        except Exception as e:
            logger.error("judge_error", judge=judge.name, error=str(e))
            return None

    def _aggregate(
        self, judgments: list[JudgmentResult]
    ) -> tuple[float, JudgmentLabel, list[str]]:
        """Aggregate multiple judge results into an overall assessment."""
        if not judgments:
            return 0.0, JudgmentLabel.WARNING, ["No judges produced results"]

        # Critical failures override everything
        critical = [j for j in judgments if j.severity == Severity.CRITICAL and not j.passed]
        if critical:
            return 0.0, JudgmentLabel.FAIL, [j.message for j in critical]

        # Simple average score
        weighted_score = sum(j.score for j in judgments) / len(judgments)

        # Determine label based on configurable thresholds
        if weighted_score >= self.pass_threshold:
            label = JudgmentLabel.PASS
        elif weighted_score >= self.warn_threshold:
            label = JudgmentLabel.WARNING
        else:
            label = JudgmentLabel.FAIL

        issues = [j.message for j in judgments if not j.passed]

        return weighted_score, label, issues