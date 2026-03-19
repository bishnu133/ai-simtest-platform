"""
LLM Evaluator v2 — Soft evaluation with improved prompting and condition scoring.

V2: Stronger evidence-based prompt, bot-only attribution, condition partial status,
evaluation_mode tracking.
"""
from __future__ import annotations
import json, logging
from typing import Any, Dict, List, Optional, Tuple
from .models import (ConditionResult, ConditionStatus, EvaluationMode, StepResult, SuccessCondition,
                     WorkflowDefinition, WorkflowStep, WorkflowStepStatus)

logger = logging.getLogger(__name__)


class LLMWorkflowEvaluator:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.last_evaluation_mode = EvaluationMode.FALLBACK_KEYWORD

    async def evaluate(self, workflow: WorkflowDefinition, conversation_transcript: str
                       ) -> Tuple[List[StepResult], List[ConditionResult], str]:
        if self.llm_client is None:
            self.last_evaluation_mode = EvaluationMode.FALLBACK_KEYWORD
            return self._fallback_evaluate(workflow, conversation_transcript)
        try:
            self.last_evaluation_mode = EvaluationMode.LLM
            return await self._llm_evaluate(workflow, conversation_transcript)
        except Exception as e:
            logger.warning(f"LLM evaluation failed, falling back: {e}")
            self.last_evaluation_mode = EvaluationMode.FALLBACK_KEYWORD
            return self._fallback_evaluate(workflow, conversation_transcript)

    async def _llm_evaluate(self, workflow, transcript) -> Tuple[List[StepResult], List[ConditionResult], str]:
        prompt = self._build_evaluation_prompt(workflow, transcript)
        response = await self.llm_client.generate(prompt)
        return self._parse_llm_response(response, workflow)

    def _build_evaluation_prompt(self, workflow: WorkflowDefinition, transcript: str) -> str:
        steps_text = "\n".join(
            f"  {i+1}. [{s.id}] {s.name}: {s.description}"
            + (f" (Hints: {', '.join(s.detection_hints)})" if s.detection_hints else "")
            + (" [REQUIRED]" if s.required else " [OPTIONAL]")
            for i, s in enumerate(workflow.steps))
        conditions_text = "\n".join(
            f"  {i+1}. [{c.id}] {c.description}" + (" [REQUIRED]" if c.required else " [OPTIONAL]")
            for i, c in enumerate(workflow.success_conditions))
        return f"""You are evaluating whether an AI chatbot correctly executed a business workflow.

WORKFLOW: {workflow.name}
DOMAIN: {workflow.domain}
DESCRIPTION: {workflow.description}

EXPECTED STEPS:
{steps_text}

SUCCESS CONDITIONS:
{conditions_text}

CONVERSATION TRANSCRIPT:
{transcript}

CRITICAL INSTRUCTIONS:
1. Mark a step as "completed" ONLY if the BOT explicitly performed it. Do NOT count user statements.
2. Mark as "partial" if the bot addressed it incompletely.
3. Mark as "missed" if the bot never addressed it at all.
4. Mark as "insufficient_evidence" if the conversation was too short to determine.
5. For conditions, use status "met", "partial", or "unmet".
6. Provide a brief quote or turn reference as evidence for each step.
7. Do NOT assume a step happened unless the bot explicitly or strongly implicitly performed it.

Respond ONLY with valid JSON (no markdown, no backticks):
{{
  "steps": [
    {{"step_id": "id", "status": "completed|missed|partial|insufficient_evidence", "confidence": 0.0-1.0, "evidence": "brief quote from bot"}}
  ],
  "conditions": [
    {{"condition_id": "id", "status": "met|partial|unmet", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}
  ],
  "reasoning": "Overall assessment in 2-3 sentences."
}}"""

    def _parse_llm_response(self, response: str, workflow: WorkflowDefinition
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
            return self._fallback_from_text(response, workflow)

        llm_steps = {s.get("step_id", ""): s for s in data.get("steps", [])}
        step_results = []
        for step in workflow.steps:
            ls = llm_steps.get(step.id, {})
            status_str = ls.get("status", "missed")
            try: status = WorkflowStepStatus(status_str)
            except ValueError: status = WorkflowStepStatus.MISSED
            step_results.append(StepResult(step_id=step.id, step_name=step.name, status=status,
                                           confidence=float(ls.get("confidence", 0.5)),
                                           evidence=ls.get("evidence", ""), required=step.required))

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

    def _fallback_from_text(self, text, workflow) -> Tuple[List[StepResult], List[ConditionResult], str]:
        text_lower = text.lower()
        step_results = []
        for step in workflow.steps:
            found = step.name.lower() in text_lower
            if not found and step.detection_hints:
                found = any(h.lower() in text_lower for h in step.detection_hints)
            step_results.append(StepResult(step_id=step.id, step_name=step.name,
                                           status=WorkflowStepStatus.COMPLETED if found else WorkflowStepStatus.MISSED,
                                           confidence=0.4, evidence="From unstructured LLM response", required=step.required))
        condition_results = [ConditionResult(condition_id=c.id, description=c.description, met=False,
                                              status="unmet", confidence=0.3, reasoning="Could not determine")
                             for c in workflow.success_conditions]
        return step_results, condition_results, "Partial LLM response."

    def _fallback_evaluate(self, workflow, transcript) -> Tuple[List[StepResult], List[ConditionResult], str]:
        transcript_lower = transcript.lower()
        step_results = []
        for step in workflow.steps:
            hints_found = 0
            total_hints = len(step.detection_hints) if step.detection_hints else 0
            if step.detection_hints:
                hints_found = sum(1 for h in step.detection_hints if h.lower() in transcript_lower)
            name_found = step.name.lower() in transcript_lower

            if total_hints > 0 and hints_found > 0:
                if hints_found == total_hints:
                    status, confidence = WorkflowStepStatus.COMPLETED, 0.7
                else:
                    status, confidence = WorkflowStepStatus.PARTIAL, 0.5
            elif name_found:
                status, confidence = WorkflowStepStatus.COMPLETED, 0.5
            else:
                status, confidence = WorkflowStepStatus.MISSED, 0.4

            step_results.append(StepResult(step_id=step.id, step_name=step.name, status=status,
                                           confidence=confidence,
                                           evidence=f"Keyword match: {hints_found}/{total_hints} hints found" if total_hints > 0 else ("Name match" if name_found else "No match"),
                                           required=step.required))

        condition_results = []
        for cond in workflow.success_conditions:
            words = cond.description.lower().split()
            key_words = [w for w in words if len(w) > 4][:5]
            matches = sum(1 for w in key_words if w in transcript_lower)
            ratio = matches / len(key_words) if key_words else 0
            if ratio >= 0.6:
                met, status = True, "met"
            elif ratio >= 0.3:
                met, status = False, "partial"
            else:
                met, status = False, "unmet"
            condition_results.append(ConditionResult(condition_id=cond.id, description=cond.description,
                                                      met=met, status=status, confidence=0.4,
                                                      reasoning=f"Keyword: {matches}/{len(key_words)} key words"))
        return step_results, condition_results, "Evaluated using keyword fallback (no LLM available)."

    def calculate_step_score(self, results: List[StepResult]) -> float:
        if not results: return 1.0
        required = [r for r in results if r.required] or results
        total = sum(1.0 if r.status == WorkflowStepStatus.COMPLETED
                    else 0.5 if r.status == WorkflowStepStatus.PARTIAL else 0.0 for r in required)
        return total / len(required)

    def calculate_condition_score(self, results: List[ConditionResult]) -> float:
        if not results: return 1.0
        return sum(r.score for r in results) / len(results)
