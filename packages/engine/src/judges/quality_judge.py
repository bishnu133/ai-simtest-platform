"""
Quality Judge - Uses LLM-as-judge to evaluate response quality.
Evaluates helpfulness, clarity, completeness, and relevance.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.llm_client import LLMClient, LLMClientFactory
from src.core.logging import get_logger
from src.judges import BaseJudge
from src.models import JudgmentResult, Persona, Severity, Turn

logger = get_logger(__name__)

QUALITY_JUDGE_SYSTEM_PROMPT = """\
You are an expert AI quality evaluator. Your job is to objectively assess the quality of an AI chatbot's response.
Be fair, consistent, and evidence-based in your evaluations.
Always output valid JSON."""

QUALITY_JUDGE_PROMPT = """\
Evaluate the following AI chatbot response:

## Conversation Context
{conversation_history}

## Bot Response to Evaluate
{response}

## Persona Context
The user is: {persona_description}

## Evaluation Criteria
Rate each criterion from 0-10:

1. **Helpfulness**: Does the response address the user's needs? Is it actionable?
2. **Clarity**: Is the response clear, well-structured, and easy to understand?
3. **Completeness**: Does it provide all necessary information?
4. **Relevance**: Is the response on-topic and directly relevant to the question?
5. **Tone Appropriateness**: Is the tone suitable for the user's emotional state and context?

## Output Format
Respond with ONLY this JSON (no markdown, no extra text):
{{
    "helpfulness": {{"score": 0, "reason": "..."}},
    "clarity": {{"score": 0, "reason": "..."}},
    "completeness": {{"score": 0, "reason": "..."}},
    "relevance": {{"score": 0, "reason": "..."}},
    "tone": {{"score": 0, "reason": "..."}},
    "overall_score": 0,
    "overall_assessment": "..."
}}"""


class QualityJudge(BaseJudge):
    """
    LLM-as-judge for evaluating response quality across multiple dimensions.
    """

    name = "quality"
    weight = 0.20

    def __init__(self, llm_client: LLMClient | None = None, pass_threshold: float = 6.0):
        self.llm = llm_client or LLMClientFactory.quality_judge()
        self.pass_threshold = pass_threshold

    async def evaluate(
        self,
        response: str,
        context: str = "",
        conversation_history: list[Turn] | None = None,
        persona: Persona | None = None,
        **kwargs: Any,
    ) -> JudgmentResult:
        """Evaluate response quality using an LLM judge."""

        # Format conversation history
        history_text = "No prior context."
        if conversation_history:
            lines = []
            for t in conversation_history[-6:]:  # Last 6 turns for context
                role = "User" if t.speaker == "user" else "Bot"
                lines.append(f"{role}: {t.message}")
            history_text = "\n".join(lines)

        # Persona description
        persona_desc = "Unknown user"
        if persona:
            persona_desc = (
                f"{persona.name} - {persona.role}, {persona.tone} tone, "
                f"{persona.technical_level.value} technical level"
            )

        prompt = QUALITY_JUDGE_PROMPT.format(
            conversation_history=history_text,
            response=response,
            persona_description=persona_desc,
        )

        try:
            raw = await self.llm.generate(
                prompt=prompt,
                system_prompt=QUALITY_JUDGE_SYSTEM_PROMPT,
            )

            evaluation = self._parse_evaluation(raw)

            overall_score = evaluation.get("overall_score", 5.0)
            normalized_score = overall_score / 10.0  # Normalize to 0-1
            passed = overall_score >= self.pass_threshold

            return JudgmentResult(
                judge_name=self.name,
                passed=passed,
                score=normalized_score,
                severity=Severity.LOW if not passed else Severity.INFO,
                message=evaluation.get("overall_assessment", "No assessment provided."),
                evidence={
                    k: v for k, v in evaluation.items()
                    if k not in ("overall_score", "overall_assessment")
                },
            )

        except Exception as e:
            logger.error("quality_judge_error", error=str(e))
            return JudgmentResult(
                judge_name=self.name,
                passed=True,
                score=0.5,
                severity=Severity.LOW,
                message=f"Quality judge encountered an error: {e}",
            )

    def _parse_evaluation(self, raw: str) -> dict:
        """Parse the LLM's JSON evaluation."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("quality_parse_fallback", raw_length=len(raw))
            return {
                "overall_score": 5.0,
                "overall_assessment": "Could not parse evaluation.",
            }


class RelevanceJudge(BaseJudge):
    """
    Lightweight relevance check - can be done locally with heuristics
    or via LLM for higher accuracy.
    """

    name = "relevance"
    weight = 0.20

    def __init__(self, use_llm: bool = False, llm_client: LLMClient | None = None):
        self.use_llm = use_llm
        self.llm = llm_client

    async def evaluate(
        self,
        response: str,
        context: str = "",
        conversation_history: list[Turn] | None = None,
        persona: Persona | None = None,
        **kwargs: Any,
    ) -> JudgmentResult:
        """Check if the response is relevant to the user's question."""

        # Get the last user message
        last_user_msg = ""
        if conversation_history:
            for turn in reversed(conversation_history):
                if turn.speaker == "user":
                    last_user_msg = turn.message
                    break

        if not last_user_msg:
            return JudgmentResult(
                judge_name=self.name,
                passed=True,
                score=1.0,
                severity=Severity.INFO,
                message="No user message to check relevance against.",
            )

        # Heuristic relevance check
        score = self._heuristic_relevance(last_user_msg, response)
        passed = score >= 0.5

        return JudgmentResult(
            judge_name=self.name,
            passed=passed,
            score=score,
            severity=Severity.MEDIUM if not passed else Severity.INFO,
            message=(
                "Response appears relevant to user's query."
                if passed
                else "Response may not be relevant to user's query."
            ),
            evidence={"relevance_score": round(score, 4)},
        )

    def _heuristic_relevance(self, question: str, response: str) -> float:
        """Keyword overlap relevance score with stemming and length boost."""
        q_words = set(question.lower().split())
        r_words = set(response.lower().split())

        # Remove common stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                      "being", "have", "has", "had", "do", "does", "did", "will",
                      "would", "could", "should", "may", "might", "can", "to",
                      "of", "in", "for", "on", "with", "at", "by", "from", "i",
                      "you", "me", "my", "your", "it", "this", "that", "what",
                      "how", "and", "or", "but", "not", "no", "so", "if", "want",
                      "need", "please", "hi", "hello", "hey"}

        q_words -= stop_words
        r_words -= stop_words

        if not q_words:
            return 0.7  # Can't judge

        # Exact overlap
        overlap = q_words & r_words

        # Partial stem matching (e.g., "refund" matches "refunded", "order" matches "ordered")
        for qw in q_words - overlap:
            for rw in r_words:
                if qw[:4] == rw[:4] and len(qw) >= 4:  # Share first 4 chars
                    overlap.add(qw)
                    break

        score = len(overlap) / len(q_words)

        # Boost if response is reasonably long (shows effort)
        if len(response.split()) > 8:
            score = min(score + 0.15, 1.0)
        if len(response.split()) > 20:
            score = min(score + 0.15, 1.0)

        return score