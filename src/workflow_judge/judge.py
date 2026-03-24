"""
Functional Judge v3 — Production-grade hybrid workflow evaluation.

V3 changes over v2:
- Applicability enforcement with skip mode
- Turn-grounded order scoring (uses first_detected_turn, not list position)
- ScoreBreakdown object (full mathematical trace)
- WorkflowStatus enum (PASSED/FAILED/SKIPPED/NEEDS_REVIEW)
- Failure taxonomy classification
- Needs-review threshold based on confidence
- Per-component confidence tracking
- Efficiency score
- Semantic topic evaluation support (delegates to LLM evaluator)
- BotTurn indexing throughout evaluation chain
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from .llm_evaluator import LLMWorkflowEvaluator
from .models import (BotTurn, EvaluationMode, FailureCategory, HardRuleResult,
                     HardRuleType, OrderMode, ScoreBreakdown, StepEvidence,
                     TopicEvalMode, WorkflowDefinition, WorkflowResult,
                     WorkflowStatus, WorkflowStepStatus)
from .rule_engine import RuleEngine

logger = logging.getLogger(__name__)


class FunctionalJudge:
    def __init__(self, workflow: WorkflowDefinition, llm_client=None):
        self.workflow = workflow
        self.llm_client = llm_client
        self.rule_engine = RuleEngine(workflow.hard_rules, llm_evaluator=None)
        self.llm_evaluator = LLMWorkflowEvaluator(llm_client)
        # V3: Wire LLM evaluator into rule engine for semantic topic checks
        self.rule_engine.llm_evaluator = self.llm_evaluator

    async def evaluate(self, turns: List[Dict[str, Any]], conversation_id: str = "",
                       persona_name: str = "") -> WorkflowResult:
        # ── Build indexed bot turns + transcripts ─────────────────────────
        bot_turns: List[BotTurn] = []
        bot_messages: List[str] = []
        all_messages: List[str] = []

        for i, turn in enumerate(turns):
            speaker = turn.get("speaker", "") if isinstance(turn, dict) else getattr(turn, "speaker", "")
            message = turn.get("message", "") if isinstance(turn, dict) else getattr(turn, "message", "")
            all_messages.append(message)
            if speaker == "bot":
                bot_turns.append(BotTurn(turn_index=i, message=message))
                bot_messages.append(message)

        turn_count = len(turns)
        full_transcript = self._build_transcript(turns)
        bot_transcript = "\n".join(f"Bot: {bt.message}" for bt in bot_turns)

        # ── V3: Applicability check with enforcement ──────────────────────
        applicable, app_confidence, app_reason, matched_hints = self.workflow.applicability_detail(full_transcript)

        if not applicable and self.workflow.skip_if_not_applicable:
            return WorkflowResult(
                workflow_id=self.workflow.id, workflow_name=self.workflow.name,
                domain=self.workflow.domain,
                passed=False, status=WorkflowStatus.SKIPPED_NOT_APPLICABLE.value,
                score=0.0, severity="info",
                reasoning=f"Workflow skipped: {app_reason}",
                workflow_applicable=False,
                applicability_confidence=app_confidence,
                applicability_reason=app_reason,
                conversation_id=conversation_id, persona_name=persona_name,
                total_turns=turn_count,
                evaluation_mode="skipped",
            )

        # ── 1. Hard rules (deterministic) ─────────────────────────────────
        rule_results = await self.rule_engine.evaluate_async(
            bot_messages, all_messages, turn_count)
        rule_score = self.rule_engine.calculate_score(rule_results)

        # ── 2. LLM/keyword evaluation (soft) ─────────────────────────────
        step_results, condition_results, reasoning = await self.llm_evaluator.evaluate(
            workflow=self.workflow,
            bot_turns=bot_turns,
            full_transcript=full_transcript,
            bot_transcript=bot_transcript)
        step_score = self.llm_evaluator.calculate_step_score(step_results)
        condition_score = self.llm_evaluator.calculate_condition_score(condition_results)

        # ── V3: Turn-grounded order validation ────────────────────────────
        order_score = self._calculate_order_score(step_results)

        # ── 3. Weighted final score (auto-normalized) ─────────────────────
        sw, rw, cw = self.workflow.normalized_weights()
        weighted_score = step_score * sw + rule_score * rw + condition_score * cw

        # ── V3: Build score breakdown ─────────────────────────────────────
        breakdown = ScoreBreakdown(
            raw_step_score=round(step_score, 4),
            raw_rule_score=round(rule_score, 4),
            raw_condition_score=round(condition_score, 4),
            normalized_weights=[round(sw, 4), round(rw, 4), round(cw, 4)],
            weighted_score=round(weighted_score, 4),
            order_score=round(order_score, 4),
        )

        final_score = weighted_score

        # Order penalty
        order_penalty = 0.0
        if self.workflow.order_mode_enum == OrderMode.SOFT and order_score < 1.0:
            multiplier = 0.5 + 0.5 * order_score
            order_penalty = 1.0 - multiplier
            final_score *= multiplier
        elif self.workflow.order_mode_enum == OrderMode.STRICT and order_score < 1.0:
            order_penalty = 0.7
            final_score *= 0.3

        breakdown.order_penalty_applied = round(order_penalty, 4)
        breakdown.post_order_score = round(final_score, 4)

        # Critical failure handling
        critical_violations = [r for r in rule_results if not r.passed and r.severity == "critical"]
        high_violations = [r for r in rule_results if not r.passed and r.severity == "high"]
        critical_failure = len(critical_violations) > 0
        if critical_failure:
            breakdown.critical_cap_applied = True
            breakdown.critical_cap_score = round(min(final_score, 0.3), 4)
            final_score = min(final_score, 0.3)

        breakdown.final_score = round(final_score, 4)
        breakdown.pass_threshold = self.workflow.pass_threshold
        breakdown.threshold_met = final_score >= self.workflow.pass_threshold and not critical_failure

        passed = breakdown.threshold_met

        # ── Collect step/violation summaries ───────────────────────────────
        completed_steps = [r.step_name for r in step_results if r.status == WorkflowStepStatus.COMPLETED]
        missed_steps = [r.step_name for r in step_results if r.status == WorkflowStepStatus.MISSED and r.required]
        violations = [f"{r.rule_name}: {r.evidence}" for r in rule_results if not r.passed]

        # ── V3: Per-component confidence ──────────────────────────────────
        step_confidence = sum(r.confidence for r in step_results) / len(step_results) if step_results else 0.0
        condition_confidence = sum(r.confidence for r in condition_results) / len(condition_results) if condition_results else 0.0
        avg_confidence = (step_confidence + 1.0 + condition_confidence) / 3.0  # rule confidence always 1.0

        # ── V3: Failure taxonomy ──────────────────────────────────────────
        failure_categories = self._classify_failures(
            step_results, rule_results, condition_results, order_score, turn_count)

        # ── V3: Efficiency score ──────────────────────────────────────────
        completed_required = sum(1 for r in step_results
                                 if r.required and r.status == WorkflowStepStatus.COMPLETED)
        efficiency = completed_required / turn_count if turn_count > 0 else 0.0

        # ── Severity assignment ───────────────────────────────────────────
        if critical_failure: severity = "critical"
        elif not passed: severity = "high"
        elif final_score < 0.85: severity = "medium"
        else: severity = "low"

        # ── V3: Status determination (replaces bare boolean) ──────────────
        if passed:
            status = WorkflowStatus.PASSED.value
        elif avg_confidence < self.workflow.needs_review_threshold:
            status = WorkflowStatus.NEEDS_REVIEW.value
        else:
            status = WorkflowStatus.FAILED.value

        return WorkflowResult(
            workflow_id=self.workflow.id, workflow_name=self.workflow.name,
            domain=self.workflow.domain,
            passed=passed, status=status,
            score=round(final_score, 4), severity=severity,
            step_score=round(step_score, 4), rule_score=round(rule_score, 4),
            condition_score=round(condition_score, 4), order_score=round(order_score, 4),
            step_results=step_results, rule_results=rule_results,
            condition_results=condition_results,
            completed_steps=completed_steps, missed_steps=missed_steps,
            violations=violations, reasoning=reasoning,
            evaluation_mode=self.llm_evaluator.last_evaluation_mode.value,
            critical_failure=critical_failure,
            critical_failures_count=len(critical_violations),
            high_failures_count=len(high_violations),
            workflow_applicable=applicable,
            confidence_overall=round(avg_confidence, 4),
            conversation_id=conversation_id, persona_name=persona_name,
            total_turns=turn_count,
            # V3 fields
            score_breakdown=breakdown,
            applicability_confidence=app_confidence,
            applicability_reason=app_reason,
            step_confidence=round(step_confidence, 4),
            rule_confidence=1.0,
            condition_confidence=round(condition_confidence, 4),
            failure_categories=[fc for fc in failure_categories],
            efficiency_score=round(efficiency, 4),
        )

    # ── V3: Turn-grounded order scoring ───────────────────────────────────

    def _calculate_order_score(self, step_results) -> float:
        """
        V3: Check step execution order using first_detected_turn from StepEvidence.
        Uses actual conversation turn indices, not result list position.
        """
        if self.workflow.order_mode_enum == OrderMode.NONE:
            return 1.0

        ordered_steps = [(s.order, s.id) for s in self.workflow.steps if s.order is not None]
        if not ordered_steps:
            return 1.0
        ordered_steps.sort(key=lambda x: x[0])

        # V3: Build mapping from step_id → first_detected_turn
        turn_map: Dict[str, int] = {}
        for r in step_results:
            if r.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.PARTIAL):
                turn_idx = -1
                if r.evidence_detail and r.evidence_detail.first_detected_turn >= 0:
                    turn_idx = r.evidence_detail.first_detected_turn
                if turn_idx >= 0:
                    turn_map[r.step_id] = turn_idx

        # Filter to ordered steps that were detected with valid turn indices
        detected_ordered = [(order, sid) for order, sid in ordered_steps if sid in turn_map]
        if len(detected_ordered) < 2:
            return 1.0  # Can't assess order with 0 or 1 detected steps

        # Expected: sorted by definition order
        expected_order = [sid for _, sid in detected_ordered]
        # Actual: sorted by first_detected_turn
        actual_order = sorted([sid for _, sid in detected_ordered], key=lambda sid: turn_map[sid])

        if expected_order == actual_order:
            return 1.0

        # Count inversions
        inversions = 0
        for i in range(len(actual_order)):
            for j in range(i + 1, len(actual_order)):
                ei = expected_order.index(actual_order[i])
                ej = expected_order.index(actual_order[j])
                if ei > ej:
                    inversions += 1

        max_inversions = len(actual_order) * (len(actual_order) - 1) / 2
        if max_inversions == 0:
            return 1.0
        return round(1.0 - (inversions / max_inversions), 4)

    # ── V3: Failure taxonomy classifier ───────────────────────────────────

    def _classify_failures(self, step_results, rule_results, condition_results,
                           order_score, turn_count) -> List[str]:
        """Classify why a workflow failed into actionable categories."""
        categories = []

        # Missed required steps
        missed_required = [r for r in step_results
                          if r.required and r.status == WorkflowStepStatus.MISSED]
        if missed_required:
            categories.append(FailureCategory.MISSED_STEP.value)

        # Wrong order
        if order_score < 1.0:
            categories.append(FailureCategory.WRONG_ORDER.value)

        # Rule violations by type
        for r in rule_results:
            if not r.passed:
                if r.rule_type in (HardRuleType.FORBIDDEN_PHRASE.value, HardRuleType.FORBIDDEN_TOPIC.value):
                    # Check if it's a data safety rule
                    safety_keywords = ["password", "ssn", "card number", "cvv", "pin",
                                       "credit card", "social security"]
                    if any(kw in r.rule_name.lower() or kw in r.evidence.lower()
                           for kw in safety_keywords):
                        if FailureCategory.UNSAFE_DATA_COLLECTION.value not in categories:
                            categories.append(FailureCategory.UNSAFE_DATA_COLLECTION.value)
                    else:
                        if FailureCategory.RULE_VIOLATION.value not in categories:
                            categories.append(FailureCategory.RULE_VIOLATION.value)
                elif r.rule_type == HardRuleType.MUST_ESCALATE.value:
                    if FailureCategory.ESCALATION_FAILURE.value not in categories:
                        categories.append(FailureCategory.ESCALATION_FAILURE.value)
                elif r.rule_type == HardRuleType.MAX_TURNS_TO_RESOLVE.value:
                    # Too many turns could indicate incomplete guidance
                    if FailureCategory.INCOMPLETE_GUIDANCE.value not in categories:
                        categories.append(FailureCategory.INCOMPLETE_GUIDANCE.value)
                else:
                    if FailureCategory.RULE_VIOLATION.value not in categories:
                        categories.append(FailureCategory.RULE_VIOLATION.value)

        # Premature resolution: conversation too short + missed steps
        completed_count = sum(1 for r in step_results
                             if r.status == WorkflowStepStatus.COMPLETED)
        if turn_count <= 3 and missed_required and completed_count > 0:
            if FailureCategory.PREMATURE_RESOLUTION.value not in categories:
                categories.append(FailureCategory.PREMATURE_RESOLUTION.value)

        # Vague handoff: escalation offered but critical steps missed
        escalation_offered = any(not r.passed and r.rule_type == HardRuleType.MUST_NOT_ESCALATE.value
                                for r in rule_results)
        if escalation_offered and missed_required:
            if FailureCategory.VAGUE_HANDOFF.value not in categories:
                categories.append(FailureCategory.VAGUE_HANDOFF.value)

        return categories

    # ── Transcript builder ────────────────────────────────────────────────

    def _build_transcript(self, turns) -> str:
        lines = []
        for turn in turns:
            speaker = turn.get("speaker", "?") if isinstance(turn, dict) else getattr(turn, "speaker", "?")
            message = turn.get("message", "") if isinstance(turn, dict) else getattr(turn, "message", "")
            lines.append(f"{'User' if speaker == 'user' else 'Bot'}: {message}")
        return "\n".join(lines)

    # ── Sync wrapper ──────────────────────────────────────────────────────

    def evaluate_sync(self, turns, conversation_id="", persona_name="") -> WorkflowResult:
        """Synchronous wrapper — works even inside a running asyncio event loop."""
        import asyncio
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.evaluate(turns, conversation_id, persona_name)
                )
                return future.result(timeout=30)
        except RuntimeError:
            return asyncio.run(self.evaluate(turns, conversation_id, persona_name))
