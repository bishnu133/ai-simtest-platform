"""
LLM Evaluator v3 — Bot-only step detection, turn-grounded evidence, semantic topic support.

V3 changes over v2:
- Accepts bot_turns (List[BotTurn]) and full_transcript separately
- Fallback keyword evaluation operates on bot-only messages (no user false positives)
- LLM prompt requests turn_index for step detection
- StepEvidence attached to each StepResult (first_detected_turn, matched_by)
- Semantic topic checking via LLM for topic rules
- Condition evaluation also separates bot/full context
"""
from __future__ import annotations
import json, logging
from typing import Any, Dict, List, Optional, Tuple
from .models import (BotTurn, ConditionResult, ConditionStatus, EvaluationMode,
                     StepEvidence, StepMatchMethod, StepResult, SuccessCondition,
                     WorkflowDefinition, WorkflowStep, WorkflowStepStatus)

logger = logging.getLogger(__name__)


class LLMWorkflowEvaluator:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.last_evaluation_mode = EvaluationMode.FALLBACK_KEYWORD

    async def evaluate(self, workflow: WorkflowDefinition,
                       bot_turns: List[BotTurn],
                       full_transcript: str,
                       bot_transcript: str = ""
                       ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        """
        V3: Accepts separated bot_turns + transcripts.
        - bot_turns: indexed bot messages for step detection
        - full_transcript: complete conversation for LLM context
        - bot_transcript: bot-only text for keyword fallback
        """
        if not bot_transcript:
            bot_transcript = "\n".join(f"Bot: {bt.message}" for bt in bot_turns)

        if self.llm_client is None:
            self.last_evaluation_mode = EvaluationMode.FALLBACK_KEYWORD
            return self._fallback_evaluate(workflow, bot_turns, bot_transcript)
        try:
            self.last_evaluation_mode = EvaluationMode.LLM
            return await self._llm_evaluate(workflow, bot_turns, full_transcript)
        except Exception as e:
            logger.warning(f"LLM evaluation failed, falling back: {e}")
            self.last_evaluation_mode = EvaluationMode.FALLBACK_KEYWORD
            return self._fallback_evaluate(workflow, bot_turns, bot_transcript)

    # ── Backward compatibility wrapper ─────────────────────────────────────
    async def evaluate_legacy(self, workflow: WorkflowDefinition,
                              conversation_transcript: str
                              ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        """V2-compatible entry point — builds BotTurn list from transcript."""
        bot_turns = []
        for i, line in enumerate(conversation_transcript.split("\n")):
            if line.startswith("Bot: "):
                bot_turns.append(BotTurn(turn_index=i, message=line[5:]))
        return await self.evaluate(workflow, bot_turns, conversation_transcript)

    # ── LLM evaluation ─────────────────────────────────────────────────────

    async def _llm_evaluate(self, workflow, bot_turns, full_transcript
                            ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        prompt = self._build_evaluation_prompt(workflow, full_transcript)
        response = await self.llm_client.generate(prompt)
        return self._parse_llm_response(response, workflow, bot_turns)

    def _build_evaluation_prompt(self, workflow: WorkflowDefinition, transcript: str) -> str:
        steps_text = "\n".join(
            f"  {i+1}. [{s.id}] {s.name}: {s.description}"
            + (f" (Hints: {', '.join(s.detection_hints)})" if s.detection_hints else "")
            + (" [REQUIRED]" if s.required else " [OPTIONAL]")
            for i, s in enumerate(workflow.steps))
        conditions_text = "\n".join(
            f"  {i+1}. [{c.id}] {c.description}" + (" [REQUIRED]" if c.required else " [OPTIONAL]")
            for i, c in enumerate(workflow.success_conditions))
        # V3: Updated prompt requesting turn_index
        return f"""You are evaluating whether an AI chatbot correctly executed a business workflow.

WORKFLOW: {workflow.name}
DOMAIN: {workflow.domain}
DESCRIPTION: {workflow.description}

EXPECTED STEPS:
{steps_text}

SUCCESS CONDITIONS:
{conditions_text}

CONVERSATION TRANSCRIPT (lines are numbered for reference):
{self._number_transcript(transcript)}

CRITICAL INSTRUCTIONS:
1. Mark a step as "completed" ONLY if the BOT explicitly performed it. Do NOT count user statements as step completion.
2. Mark as "partial" if the bot addressed it incompletely.
3. Mark as "missed" if the bot never addressed it at all.
4. Mark as "insufficient_evidence" if the conversation was too short to determine.
5. For conditions, use status "met", "partial", or "unmet".
6. Provide the turn_index (line number from transcript) where the bot FIRST performed each step.
7. Provide a brief quote from the bot's message as evidence.
8. Do NOT assume a step happened unless the bot explicitly or strongly implicitly performed it.
9. Do NOT credit the bot for something the user said or requested.

Respond ONLY with valid JSON (no markdown, no backticks):
{{
  "steps": [
    {{"step_id": "id", "status": "completed|missed|partial|insufficient_evidence", "confidence": 0.0-1.0, "turn_index": -1, "evidence": "brief quote from bot"}}
  ],
  "conditions": [
    {{"condition_id": "id", "status": "met|partial|unmet", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}
  ],
  "reasoning": "Overall assessment in 2-3 sentences."
}}"""

    def _number_transcript(self, transcript: str) -> str:
        """Add line numbers to transcript for LLM turn reference."""
        lines = transcript.split("\n")
        return "\n".join(f"[{i}] {line}" for i, line in enumerate(lines))

    def _parse_llm_response(self, response: str, workflow: WorkflowDefinition,
                            bot_turns: List[BotTurn]
                            ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON, using text fallback")
            self.last_evaluation_mode = EvaluationMode.FALLBACK_TEXT
            return self._fallback_from_text(response, workflow, bot_turns)

        llm_steps = {s.get("step_id", ""): s for s in data.get("steps", [])}
        step_results = []
        for step in workflow.steps:
            ls = llm_steps.get(step.id, {})
            status_str = ls.get("status", "missed")
            try: status = WorkflowStepStatus(status_str)
            except ValueError: status = WorkflowStepStatus.MISSED

            # V3: Extract turn_index from LLM response
            turn_index = int(ls.get("turn_index", -1))
            evidence_text = ls.get("evidence", "")
            evidence_detail = StepEvidence(
                first_detected_turn=turn_index,
                matched_by=StepMatchMethod.LLM.value if status != WorkflowStepStatus.MISSED else StepMatchMethod.UNMATCHED.value,
                matched_evidence=evidence_text[:300]
            )

            step_results.append(StepResult(step_id=step.id, step_name=step.name, status=status,
                                           confidence=float(ls.get("confidence", 0.5)),
                                           evidence=evidence_text, required=step.required,
                                           evidence_detail=evidence_detail))

        llm_conds = {c.get("condition_id", ""): c for c in data.get("conditions", [])}
        condition_results = []
        for cond in workflow.success_conditions:
            lc = llm_conds.get(cond.id, {})
            status = lc.get("status", "unmet")
            met = status == "met"
            condition_results.append(ConditionResult(condition_id=cond.id, description=cond.description,
                                                      met=met, status=status,
                                                      confidence=float(lc.get("confidence", 0.5)),
                                                      reasoning=lc.get("reasoning", "")))
        return step_results, condition_results, data.get("reasoning", "LLM evaluation completed.")

    def _fallback_from_text(self, text, workflow, bot_turns
                            ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        """Fallback when LLM returns non-JSON text."""
        text_lower = text.lower()
        step_results = []
        for step in workflow.steps:
            found = step.name.lower() in text_lower
            if not found and step.detection_hints:
                found = any(h.lower() in text_lower for h in step.detection_hints)
            evidence_detail = StepEvidence(
                first_detected_turn=-1,
                matched_by=StepMatchMethod.KEYWORD.value if found else StepMatchMethod.UNMATCHED.value,
                matched_evidence="From unstructured LLM response"
            )
            step_results.append(StepResult(step_id=step.id, step_name=step.name,
                                           status=WorkflowStepStatus.COMPLETED if found else WorkflowStepStatus.MISSED,
                                           confidence=0.4, evidence="From unstructured LLM response",
                                           required=step.required, evidence_detail=evidence_detail))
        condition_results = [ConditionResult(condition_id=c.id, description=c.description, met=False,
                                              status="unmet", confidence=0.3, reasoning="Could not determine")
                             for c in workflow.success_conditions]
        return step_results, condition_results, "Partial LLM response."

    # ── V3: Bot-only keyword fallback ──────────────────────────────────────

    def _fallback_evaluate(self, workflow: WorkflowDefinition,
                           bot_turns: List[BotTurn],
                           bot_transcript: str
                           ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        """
        V3: Keyword fallback operates on BOT-ONLY messages.
        Each step is checked against individual bot turns to find first occurrence.
        """
        bot_text_lower = bot_transcript.lower()
        step_results = []

        for step in workflow.steps:
            best_turn_index = -1
            hints_found = 0
            total_hints = len(step.detection_hints) if step.detection_hints else 0

            # V3: Check each bot turn individually to find first occurrence
            # Then count unique hints found across ALL bot messages (aggregate)
            if step.detection_hints:
                for bt in bot_turns:
                    msg_lower = bt.message.lower()
                    turn_hits = sum(1 for h in step.detection_hints if h.lower() in msg_lower)
                    if turn_hits > 0 and best_turn_index == -1:
                        best_turn_index = bt.turn_index
                # Aggregate: count unique hints found across all bot messages combined
                hints_found = sum(1 for h in step.detection_hints if h.lower() in bot_text_lower)

            name_found = False
            if not step.detection_hints or hints_found == 0:
                for bt in bot_turns:
                    if step.name.lower() in bt.message.lower():
                        name_found = True
                        if best_turn_index == -1:
                            best_turn_index = bt.turn_index
                        break

            if total_hints > 0 and hints_found > 0:
                ratio = hints_found / total_hints
                if ratio >= 0.5:  # Majority of hints found → completed
                    status, confidence = WorkflowStepStatus.COMPLETED, min(0.5 + ratio * 0.3, 0.8)
                else:
                    status, confidence = WorkflowStepStatus.PARTIAL, 0.5
            elif name_found:
                status, confidence = WorkflowStepStatus.COMPLETED, 0.5
            else:
                status, confidence = WorkflowStepStatus.MISSED, 0.4

            # V3: Build evidence detail
            matched_text = ""
            if best_turn_index >= 0:
                for bt in bot_turns:
                    if bt.turn_index == best_turn_index:
                        matched_text = bt.message[:200]
                        break

            evidence_detail = StepEvidence(
                first_detected_turn=best_turn_index,
                matched_by=StepMatchMethod.KEYWORD.value if status != WorkflowStepStatus.MISSED else StepMatchMethod.UNMATCHED.value,
                matched_evidence=matched_text
            )

            evidence_str = (f"Keyword match: {hints_found}/{total_hints} hints found (first at turn {best_turn_index})"
                           if total_hints > 0
                           else ("Name match" + (f" at turn {best_turn_index}" if best_turn_index >= 0 else "") if name_found else "No match in bot messages"))

            step_results.append(StepResult(step_id=step.id, step_name=step.name, status=status,
                                           confidence=confidence, evidence=evidence_str,
                                           required=step.required, evidence_detail=evidence_detail))

        # Conditions: still use bot-only transcript for keyword matching
        condition_results = []
        for cond in workflow.success_conditions:
            words = cond.description.lower().split()
            key_words = [w for w in words if len(w) > 4][:5]
            matches = sum(1 for w in key_words if w in bot_text_lower)
            ratio = matches / len(key_words) if key_words else 0
            if ratio >= 0.6:
                met, status = True, "met"
            elif ratio >= 0.3:
                met, status = False, "partial"
            else:
                met, status = False, "unmet"
            condition_results.append(ConditionResult(condition_id=cond.id, description=cond.description,
                                                      met=met, status=status, confidence=0.4,
                                                      reasoning=f"Keyword: {matches}/{len(key_words)} key words (bot-only)"))
        return step_results, condition_results, "Evaluated using keyword fallback on bot-only messages (no LLM available)."

    # ── Scoring helpers ────────────────────────────────────────────────────

    def calculate_step_score(self, results: List[StepResult]) -> float:
        if not results: return 1.0
        required = [r for r in results if r.required] or results
        total = sum(1.0 if r.status == WorkflowStepStatus.COMPLETED
                    else 0.5 if r.status == WorkflowStepStatus.PARTIAL else 0.0 for r in required)
        return total / len(required)

    def calculate_condition_score(self, results: List[ConditionResult]) -> float:
        if not results: return 1.0
        return sum(r.score for r in results) / len(results)

    # ── V3: Semantic topic evaluation (used by RuleEngine) ─────────────────

    async def evaluate_topic_semantic(self, text: str, topic_phrases: List[str],
                                      is_forbidden: bool = False) -> Tuple[bool, float, str]:
        """
        Use LLM to semantically evaluate whether text discusses a topic.
        Returns (detected: bool, confidence: float, reasoning: str).
        """
        if self.llm_client is None:
            return False, 0.0, "No LLM available for semantic evaluation"

        topic_desc = ", ".join(topic_phrases)
        direction = "forbidden" if is_forbidden else "required"
        prompt = f"""Determine whether the following text discusses the topic: {topic_desc}
This topic is {direction} in the conversation.

TEXT:
{text[:2000]}

Respond ONLY with valid JSON:
{{"detected": true/false, "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""

        try:
            response = await self.llm_client.generate(prompt)
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            data = json.loads(cleaned)
            return bool(data.get("detected", False)), float(data.get("confidence", 0.5)), data.get("reasoning", "")
        except Exception as e:
            logger.warning(f"Semantic topic evaluation failed: {e}")
            return False, 0.0, f"Semantic evaluation failed: {e}"
