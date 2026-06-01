"""
RAG/Tool Evaluation Engine — Phase 5 of P3 #14.

The main orchestrator that:
1. Takes judged conversations from the simulation pipeline
2. Annotates each bot turn (evidence extraction)
3. Runs RAG metrics on turns with RAG evidence
4. Runs Tool metrics on turns with tool evidence
5. Aggregates results per turn → per conversation → overall
6. Produces RAGEvalReport for JSON export and HTML injection

Integrates into the post-simulation pipeline as step 6:
  coverage → versioning → policy → workflow → expansion → RAG/Tool eval
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.rag_eval.models import (
    ConversationRAGResult,
    EvalSpeed,
    EvidenceMode,
    MetricResult,
    RAGEvalConfig,
    ResponseAnnotation,
    TurnRAGResult,
)
from src.rag_eval.annotator import ResponseAnnotator
from src.rag_eval.rag_metrics import RAGMetricEvaluator
from src.rag_eval.tool_metrics import ToolDefinition, ToolMetricEvaluator


# ============================================================================
# Report Model
# ============================================================================

@dataclass
class RAGEvalReport:
    """Complete RAG/Tool evaluation report for an entire simulation run."""

    # Summary
    total_conversations: int = 0
    total_turns_evaluated: int = 0
    overall_rag_score: float = 0.0
    overall_tool_score: float = 0.0
    overall_score: float = 0.0
    evidence_mode: str = "inferred"

    # Speed mode used
    eval_speed: str = "standard"

    # Per-metric averages
    rag_metric_averages: Dict[str, float] = field(default_factory=dict)
    tool_metric_averages: Dict[str, float] = field(default_factory=dict)

    # Per-metric pass rates
    rag_metric_pass_rates: Dict[str, float] = field(default_factory=dict)
    tool_metric_pass_rates: Dict[str, float] = field(default_factory=dict)

    # Conversation-level results
    conversation_results: List[ConversationRAGResult] = field(default_factory=list)

    # Issues summary
    total_rag_issues: int = 0
    total_tool_issues: int = 0
    top_issues: List[str] = field(default_factory=list)

    # CI/CD gate
    gate_threshold: Optional[float] = None
    gate_passed: bool = True

    # Timing
    started_at: str = ""
    completed_at: str = ""
    execution_time_seconds: float = 0.0

    # Errors
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_conversations": self.total_conversations,
            "total_turns_evaluated": self.total_turns_evaluated,
            "overall_rag_score": round(self.overall_rag_score, 3),
            "overall_tool_score": round(self.overall_tool_score, 3),
            "overall_score": round(self.overall_score, 3),
            "evidence_mode": self.evidence_mode,
            "eval_speed": self.eval_speed,
            "rag_metric_averages": {k: round(v, 3) for k, v in self.rag_metric_averages.items()},
            "tool_metric_averages": {k: round(v, 3) for k, v in self.tool_metric_averages.items()},
            "rag_metric_pass_rates": {k: round(v, 3) for k, v in self.rag_metric_pass_rates.items()},
            "tool_metric_pass_rates": {k: round(v, 3) for k, v in self.tool_metric_pass_rates.items()},
            "total_rag_issues": self.total_rag_issues,
            "total_tool_issues": self.total_tool_issues,
            "top_issues": self.top_issues[:10],
            "gate_threshold": self.gate_threshold,
            "gate_passed": self.gate_passed,
            "execution_time_seconds": round(self.execution_time_seconds, 2),
            "conversations": [c.to_dict() for c in self.conversation_results],
            "errors": self.errors[:10],
        }

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact summary for appending to simulation summary.json."""
        return {
            "rag_eval": {
                "overall_score": round(self.overall_score, 3),
                "rag_score": round(self.overall_rag_score, 3),
                "tool_score": round(self.overall_tool_score, 3),
                "turns_evaluated": self.total_turns_evaluated,
                "evidence_mode": self.evidence_mode,
                "eval_speed": self.eval_speed,
                "rag_issues": self.total_rag_issues,
                "tool_issues": self.total_tool_issues,
                "gate_passed": self.gate_passed,
                "top_metrics": {
                    **{k: round(v, 3) for k, v in list(self.rag_metric_averages.items())[:4]},
                    **{k: round(v, 3) for k, v in list(self.tool_metric_averages.items())[:4]},
                },
            }
        }


# ============================================================================
# Engine
# ============================================================================

class RAGEvalEngine:
    """
    Main orchestrator for RAG/Tool evaluation.

    Usage:
        engine = RAGEvalEngine(config, llm_client=llm)
        report = await engine.evaluate_conversations(judged_conversations, context_doc)

    Integration with post-simulation pipeline:
        report = await engine.evaluate_from_simulation(
            report=simulation_report,
            context_document=documentation_text,
        )
    """

    def __init__(
        self,
        config: Optional[RAGEvalConfig] = None,
        llm_client: Optional[Any] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        progress_callback: Optional[Any] = None,
    ):
        self._config = config or RAGEvalConfig()
        self._llm = llm_client
        self._tool_defs = tool_definitions or []
        self._progress = progress_callback

        # Initialize sub-components
        self._annotator = ResponseAnnotator(
            evidence_mode=self._config.evidence_mode,
            llm_client=self._llm,
        )
        self._rag_evaluator = RAGMetricEvaluator(
            config=self._config,
            llm_client=self._llm,
        )
        self._tool_evaluator = ToolMetricEvaluator(
            config=self._config,
            tool_definitions=self._tool_defs,
            llm_client=self._llm,
        )

    # ================================================================
    # Public API
    # ================================================================

    async def evaluate_conversations(
        self,
        conversations: List[Any],
        context_document: str = "",
    ) -> RAGEvalReport:
        """
        Evaluate a list of conversations for RAG/tool quality.

        Args:
            conversations: List of JudgedConversation or similar objects.
                Each must have .conversation.turns[] and .conversation.id
            context_document: Ground-truth documentation text.

        Returns:
            RAGEvalReport with per-turn, per-conversation, and overall scores.
        """
        report = RAGEvalReport(
            started_at=datetime.now(timezone.utc).isoformat(),
            eval_speed=self._config.eval_speed.value,
        )
        start_time = time.time()

        conv_results = []
        all_rag_metrics: Dict[str, List[float]] = {}
        all_tool_metrics: Dict[str, List[float]] = {}
        all_rag_pass: Dict[str, List[bool]] = {}
        all_tool_pass: Dict[str, List[bool]] = {}
        all_issues: List[str] = []

        for conv_idx, jc in enumerate(conversations):
            try:
                conv_result = await self._evaluate_single_conversation(
                    jc, context_document, conv_idx, len(conversations),
                )
                conv_results.append(conv_result)

                # Collect metrics for averaging
                for tr in conv_result.turn_results:
                    for mr in tr.rag_metrics:
                        all_rag_metrics.setdefault(mr.metric_name, []).append(mr.score)
                        all_rag_pass.setdefault(mr.metric_name, []).append(mr.passed)
                        all_issues.extend(mr.issues)
                    for mr in tr.tool_metrics:
                        all_tool_metrics.setdefault(mr.metric_name, []).append(mr.score)
                        all_tool_pass.setdefault(mr.metric_name, []).append(mr.passed)
                        all_issues.extend(mr.issues)

            except Exception as e:
                report.errors.append(f"Conversation {conv_idx}: {str(e)[:200]}")

        # Aggregate
        report.conversation_results = conv_results
        report.total_conversations = len(conv_results)
        report.total_turns_evaluated = sum(len(cr.turn_results) for cr in conv_results)

        # Per-metric averages
        report.rag_metric_averages = {
            k: sum(v) / len(v) for k, v in all_rag_metrics.items() if v
        }
        report.tool_metric_averages = {
            k: sum(v) / len(v) for k, v in all_tool_metrics.items() if v
        }

        # Per-metric pass rates
        report.rag_metric_pass_rates = {
            k: sum(1 for p in v if p) / len(v) for k, v in all_rag_pass.items() if v
        }
        report.tool_metric_pass_rates = {
            k: sum(1 for p in v if p) / len(v) for k, v in all_tool_pass.items() if v
        }

        # Overall scores
        all_rag_scores = [s for scores in all_rag_metrics.values() for s in scores]
        all_tool_scores = [s for scores in all_tool_metrics.values() for s in scores]

        report.overall_rag_score = sum(all_rag_scores) / len(all_rag_scores) if all_rag_scores else 0.0
        report.overall_tool_score = sum(all_tool_scores) / len(all_tool_scores) if all_tool_scores else 0.0

        # Combined score (weighted by presence)
        total_scores = all_rag_scores + all_tool_scores
        report.overall_score = sum(total_scores) / len(total_scores) if total_scores else 0.0

        # Evidence mode (majority)
        mode_counts: Dict[str, int] = {}
        for cr in conv_results:
            m = cr.evidence_mode.value
            mode_counts[m] = mode_counts.get(m, 0) + 1
        report.evidence_mode = max(mode_counts, key=mode_counts.get) if mode_counts else "inferred"

        # Issues
        report.total_rag_issues = sum(cr.total_rag_issues for cr in conv_results)
        report.total_tool_issues = sum(cr.total_tool_issues for cr in conv_results)

        # Deduplicate and rank top issues
        issue_counts: Dict[str, int] = {}
        for issue in all_issues:
            key = issue[:80]
            issue_counts[key] = issue_counts.get(key, 0) + 1
        report.top_issues = [
            f"{issue} (×{count})" if count > 1 else issue
            for issue, count in sorted(issue_counts.items(), key=lambda x: -x[1])[:10]
        ]

        # CI/CD gate
        if self._config.fail_if_below is not None:
            report.gate_threshold = self._config.fail_if_below
            report.gate_passed = report.overall_score >= self._config.fail_if_below

        report.execution_time_seconds = time.time() - start_time
        report.completed_at = datetime.now(timezone.utc).isoformat()

        return report

    async def evaluate_from_simulation(
        self,
        report: Any,
        context_document: str = "",
    ) -> RAGEvalReport:
        """
        Integration point for the post-simulation pipeline.

        Takes a SimulationReport (with .judged_conversations) and runs
        RAG/tool evaluation on all bot turns.
        """
        conversations = []
        if hasattr(report, 'judged_conversations'):
            conversations = report.judged_conversations
        elif isinstance(report, dict):
            conversations = report.get('judged_conversations', [])

        return await self.evaluate_conversations(conversations, context_document)

    # ================================================================
    # Single Conversation Evaluation
    # ================================================================

    async def _evaluate_single_conversation(
        self,
        jc: Any,
        context_document: str,
        conv_idx: int,
        total_convs: int,
    ) -> ConversationRAGResult:
        """Evaluate all bot turns in a single conversation."""

        # Extract conversation and turns
        conv = jc.conversation if hasattr(jc, 'conversation') else jc
        conv_id = conv.id if hasattr(conv, 'id') else f"conv_{conv_idx}"
        turns = conv.turns if hasattr(conv, 'turns') else []

        if self._progress:
            self._progress(f"RAG eval: conversation {conv_idx + 1}/{total_convs}")

        result = ConversationRAGResult(conversation_id=conv_id)
        turn_results = []
        turn_idx = 0

        for i, turn in enumerate(turns):
            speaker = turn.speaker if hasattr(turn, 'speaker') else turn.get('speaker', '')
            if speaker != 'bot':
                continue

            # Apply sampling
            if self._config.sample_rate < 1.0:
                import random
                if random.random() > self._config.sample_rate:
                    continue

            # Cap turns per conversation
            if (self._config.max_turns_per_conversation
                    and turn_idx >= self._config.max_turns_per_conversation):
                break

            # Get bot response text
            response_text = turn.message if hasattr(turn, 'message') else turn.get('message', '')

            # Get user query (previous user turn)
            user_query = self._get_previous_user_message(turns, i)

            # Get metadata from turn
            metadata = turn.metadata if hasattr(turn, 'metadata') else turn.get('metadata', {})
            if not isinstance(metadata, dict):
                metadata = {}

            # Annotate
            annotation = await self._annotator.annotate(
                response_text=response_text,
                user_query=user_query,
                context_document=context_document,
                raw_metadata=metadata,
                trace_spans=metadata.get('trace_spans', []),
            )

            # Evaluate RAG metrics
            rag_metrics = []
            if annotation.has_rag_evidence or context_document:
                rag_metrics = await self._rag_evaluator.evaluate(annotation)
            elif self._config.eval_speed != EvalSpeed.DETERMINISTIC:
                # Even without explicit evidence, run deterministic metrics
                # on the response text (attribution completeness, etc.)
                rag_metrics = await self._rag_evaluator.evaluate(annotation)

            # Evaluate Tool metrics
            tool_metrics = []
            if annotation.has_tool_evidence:
                tool_metrics = await self._tool_evaluator.evaluate(annotation)

            # Compute turn scores
            rag_score = (
                sum(m.score for m in rag_metrics) / len(rag_metrics)
                if rag_metrics else 0.0
            )
            tool_score = (
                sum(m.score for m in tool_metrics) / len(tool_metrics)
                if tool_metrics else 0.0
            )
            all_metrics = rag_metrics + tool_metrics
            overall = (
                sum(m.score for m in all_metrics) / len(all_metrics)
                if all_metrics else 0.0
            )

            tr = TurnRAGResult(
                turn_index=i,
                annotation=annotation,
                rag_metrics=rag_metrics,
                tool_metrics=tool_metrics,
                rag_score=rag_score,
                tool_score=tool_score,
                overall_score=overall,
            )
            turn_results.append(tr)
            turn_idx += 1

        # Aggregate conversation scores
        result.turn_results = turn_results
        if turn_results:
            result.avg_rag_score = (
                sum(tr.rag_score for tr in turn_results) / len(turn_results)
            )
            result.avg_tool_score = (
                sum(tr.tool_score for tr in turn_results) / len(turn_results)
            )
            result.avg_overall_score = (
                sum(tr.overall_score for tr in turn_results) / len(turn_results)
            )

            # Determine dominant evidence mode
            mode_counts: Dict[EvidenceMode, int] = {}
            for tr in turn_results:
                m = tr.annotation.evidence_mode
                mode_counts[m] = mode_counts.get(m, 0) + 1
            result.evidence_mode = max(mode_counts, key=mode_counts.get)

            # Count issues
            result.total_rag_issues = sum(
                len(m.issues) for tr in turn_results for m in tr.rag_metrics
            )
            result.total_tool_issues = sum(
                len(m.issues) for tr in turn_results for m in tr.tool_metrics
            )

        return result

    # ================================================================
    # Helpers
    # ================================================================

    def _get_previous_user_message(self, turns: List[Any], bot_turn_idx: int) -> str:
        """Find the user message that preceded this bot turn."""
        for i in range(bot_turn_idx - 1, -1, -1):
            turn = turns[i]
            speaker = turn.speaker if hasattr(turn, 'speaker') else turn.get('speaker', '')
            if speaker == 'user':
                return turn.message if hasattr(turn, 'message') else turn.get('message', '')
        return ""

    # ================================================================
    # Export
    # ================================================================

    @staticmethod
    def save_report(report: RAGEvalReport, output_dir: str) -> str:
        """Save RAG eval report to JSON file."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        filepath = out_path / "rag_eval_report.json"

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, indent=2, default=str)

        return str(filepath)

    @staticmethod
    def append_to_summary(report: RAGEvalReport, summary_path: str) -> None:
        """Append RAG eval summary to existing simulation summary.json."""
        path = Path(summary_path)
        if not path.exists():
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                summary = json.load(f)

            summary.update(report.to_summary_dict())

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, default=str)
        except Exception:
            pass  # Non-critical — don't fail pipeline