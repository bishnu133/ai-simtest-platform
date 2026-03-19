"""
Functional Judge v2 — Hybrid workflow evaluation with order validation,
weight normalization, applicability check, and enhanced result metadata.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List
from .llm_evaluator import LLMWorkflowEvaluator
from .models import (EvaluationMode, HardRuleResult, OrderMode, WorkflowDefinition,
                     WorkflowResult, WorkflowStepStatus)
from .rule_engine import RuleEngine

logger = logging.getLogger(__name__)


class FunctionalJudge:
    def __init__(self, workflow: WorkflowDefinition, llm_client=None):
        self.workflow = workflow
        self.rule_engine = RuleEngine(workflow.hard_rules)
        self.llm_evaluator = LLMWorkflowEvaluator(llm_client)

    async def evaluate(self, turns: List[Dict[str, Any]], conversation_id: str = "",
                       persona_name: str = "") -> WorkflowResult:
        bot_messages, all_messages = [], []
        for turn in turns:
            speaker = turn.get("speaker", "") if isinstance(turn, dict) else getattr(turn, "speaker", "")
            message = turn.get("message", "") if isinstance(turn, dict) else getattr(turn, "message", "")
            all_messages.append(message)
            if speaker == "bot": bot_messages.append(message)
        turn_count = len(turns)
        transcript = self._build_transcript(turns)

        # V2: Applicability check
        applicable = self.workflow.is_applicable(transcript)

        # 1. Hard rules (deterministic)
        rule_results = self.rule_engine.evaluate(bot_messages, all_messages, turn_count)
        rule_score = self.rule_engine.calculate_score(rule_results)

        # 2. LLM evaluation (soft)
        step_results, condition_results, reasoning = await self.llm_evaluator.evaluate(
            workflow=self.workflow, conversation_transcript=transcript)
        step_score = self.llm_evaluator.calculate_step_score(step_results)
        condition_score = self.llm_evaluator.calculate_condition_score(condition_results)

        # V2: Order validation
        order_score = self._calculate_order_score(step_results)

        # 3. Weighted final score (V2: auto-normalized weights)
        sw, rw, cw = self.workflow.normalized_weights()
        final_score = step_score * sw + rule_score * rw + condition_score * cw

        # V2: Apply order penalty for soft mode
        if self.workflow.order_mode_enum == OrderMode.SOFT and order_score < 1.0:
            final_score *= (0.5 + 0.5 * order_score)  # Up to 50% penalty
        elif self.workflow.order_mode_enum == OrderMode.STRICT and order_score < 1.0:
            final_score *= 0.3  # Major penalty

        # Critical failure handling
        critical_violations = [r for r in rule_results if not r.passed and r.severity == "critical"]
        high_violations = [r for r in rule_results if not r.passed and r.severity == "high"]
        critical_failure = len(critical_violations) > 0
        if critical_failure:
            final_score = min(final_score, 0.3)

        passed = final_score >= self.workflow.pass_threshold and not critical_failure

        completed_steps = [r.step_name for r in step_results if r.status == WorkflowStepStatus.COMPLETED]
        missed_steps = [r.step_name for r in step_results if r.status == WorkflowStepStatus.MISSED and r.required]
        violations = [f"{r.rule_name}: {r.evidence}" for r in rule_results if not r.passed]

        if critical_failure: severity = "critical"
        elif not passed: severity = "high"
        elif final_score < 0.85: severity = "medium"
        else: severity = "low"

        # V2: Overall confidence from step confidences
        avg_confidence = sum(r.confidence for r in step_results) / len(step_results) if step_results else 0.0

        return WorkflowResult(
            workflow_id=self.workflow.id, workflow_name=self.workflow.name, domain=self.workflow.domain,
            passed=passed, score=round(final_score, 4), severity=severity,
            step_score=round(step_score, 4), rule_score=round(rule_score, 4),
            condition_score=round(condition_score, 4), order_score=round(order_score, 4),
            step_results=step_results, rule_results=rule_results, condition_results=condition_results,
            completed_steps=completed_steps, missed_steps=missed_steps, violations=violations,
            reasoning=reasoning,
            evaluation_mode=self.llm_evaluator.last_evaluation_mode.value,
            critical_failure=critical_failure, critical_failures_count=len(critical_violations),
            high_failures_count=len(high_violations), workflow_applicable=applicable,
            confidence_overall=round(avg_confidence, 4),
            conversation_id=conversation_id, persona_name=persona_name, total_turns=turn_count,
        )

    def _calculate_order_score(self, step_results) -> float:
        """Check if completed steps follow the expected order."""
        if self.workflow.order_mode_enum == OrderMode.NONE:
            return 1.0
        ordered_steps = [(s.order, s.id) for s in self.workflow.steps if s.order is not None]
        if not ordered_steps:
            return 1.0
        ordered_steps.sort(key=lambda x: x[0])
        completed_ids = [r.step_id for r in step_results
                         if r.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.PARTIAL)]
        expected_order = [sid for _, sid in ordered_steps if sid in completed_ids]
        actual_order = [sid for sid in completed_ids if sid in [s for _, s in ordered_steps]]
        if not actual_order or not expected_order:
            return 1.0
        # Count inversions (how out-of-order)
        inversions = 0
        for i in range(len(actual_order)):
            for j in range(i + 1, len(actual_order)):
                if actual_order[i] in expected_order and actual_order[j] in expected_order:
                    if expected_order.index(actual_order[i]) > expected_order.index(actual_order[j]):
                        inversions += 1
        max_inversions = len(actual_order) * (len(actual_order) - 1) / 2
        if max_inversions == 0: return 1.0
        return 1.0 - (inversions / max_inversions)

    def _build_transcript(self, turns) -> str:
        lines = []
        for turn in turns:
            speaker = turn.get("speaker", "?") if isinstance(turn, dict) else getattr(turn, "speaker", "?")
            message = turn.get("message", "") if isinstance(turn, dict) else getattr(turn, "message", "")
            lines.append(f"{'User' if speaker == 'user' else 'Bot'}: {message}")
        return "\n".join(lines)

    def evaluate_sync(self, turns, conversation_id="", persona_name="") -> WorkflowResult:
        """Synchronous wrapper — works even inside a running asyncio event loop."""
        import asyncio
        try:
            asyncio.get_running_loop()
            # Already inside an event loop — use thread pool to avoid nesting
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.evaluate(turns, conversation_id, persona_name)
                )
                return future.result(timeout=30)
        except RuntimeError:
            # No running loop — safe to use asyncio.run directly
            return asyncio.run(self.evaluate(turns, conversation_id, persona_name))