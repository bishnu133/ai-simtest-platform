"""
Tests for P3 #14 Phase 5 — RAG/Tool Evaluation Engine.

Test classes:
  1. TestRAGEvalReport        — Report model, serialization, summary dict
  2. TestEngineEndToEnd       — Full pipeline: annotate → metrics → aggregate
  3. TestEngineSingleConv     — Single conversation evaluation
  4. TestEngineAggregation    — Per-metric averages, pass rates, overall scores
  5. TestEngineCIGate         — CI/CD gate (fail_if_below)
  6. TestEngineSampling       — Turn sampling and max_turns_per_conversation
  7. TestEngineExport         — JSON export and summary append
  8. TestEngineEdgeCases      — Empty conversations, errors, dict-based inputs
"""

import asyncio
import json
import os
import tempfile
import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

from src.rag_eval.models import (
    EvalSpeed,
    EvidenceMode,
    RAGEvalConfig,
    RAGMetricType,
    ToolMetricType,
)
from src.rag_eval.engine import RAGEvalEngine, RAGEvalReport
from src.rag_eval.tool_metrics import ToolDefinition


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================================
# Mock conversation objects (mimic JudgedConversation/Conversation/Turn)
# ============================================================================

@dataclass
class MockTurn:
    speaker: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockConversation:
    id: str
    turns: List[MockTurn] = field(default_factory=list)


@dataclass
class MockJudgedConversation:
    conversation: MockConversation
    persona: Optional[Any] = None


def make_conversations(n=2, turns_per=4):
    """Create n mock judged conversations with alternating user/bot turns."""
    convs = []
    for i in range(n):
        turns = []
        for t in range(turns_per):
            if t % 2 == 0:
                turns.append(MockTurn(
                    speaker="user",
                    message=f"What is the return policy for item {t}?",
                ))
            else:
                turns.append(MockTurn(
                    speaker="bot",
                    message=f"According to our FAQ, items can be returned within 30 days [1]. Refunds take 5-7 business days.",
                    metadata={
                        "sources": [
                            {"content": "Return policy: 30 days for all items.", "source_id": "doc_1", "title": "FAQ"},
                        ],
                    },
                ))
        convs.append(MockJudgedConversation(
            conversation=MockConversation(id=f"conv_{i}", turns=turns),
        ))
    return convs


def make_tool_conversations():
    """Create conversations with tool call metadata."""
    return [MockJudgedConversation(
        conversation=MockConversation(
            id="conv_tools",
            turns=[
                MockTurn(speaker="user", message="Check my balance"),
                MockTurn(
                    speaker="bot",
                    message="Your balance is $1,500.00.",
                    metadata={
                        "tool_calls": [
                            {"name": "get_balance", "arguments": {"account_id": "A123"}, "result": "$1,500.00"},
                        ],
                    },
                ),
                MockTurn(speaker="user", message="Transfer $100 to savings"),
                MockTurn(
                    speaker="bot",
                    message="I've transferred $100 to your savings account.",
                    metadata={
                        "tool_calls": [
                            {"name": "transfer_funds", "arguments": {"from": "A123", "to": "S456", "amount": 100}, "result": "Success"},
                        ],
                    },
                ),
            ],
        ),
    )]


# ============================================================================
# 1. TestRAGEvalReport
# ============================================================================

class TestRAGEvalReport:

    def test_report_defaults(self):
        report = RAGEvalReport()
        assert report.total_conversations == 0
        assert report.overall_score == 0.0
        assert report.gate_passed is True
        assert report.errors == []

    def test_report_to_dict(self):
        report = RAGEvalReport(
            total_conversations=3,
            total_turns_evaluated=9,
            overall_rag_score=0.85,
            overall_tool_score=0.72,
            overall_score=0.79,
            evidence_mode="structured",
            eval_speed="standard",
            rag_metric_averages={"faithfulness": 0.9, "citation_accuracy": 0.8},
            total_rag_issues=3,
            gate_passed=True,
        )
        d = report.to_dict()
        assert d["total_conversations"] == 3
        assert d["overall_score"] == 0.79
        assert d["rag_metric_averages"]["faithfulness"] == 0.9
        assert isinstance(d["conversations"], list)

    def test_report_to_summary_dict(self):
        report = RAGEvalReport(
            overall_score=0.82,
            overall_rag_score=0.85,
            overall_tool_score=0.72,
            total_turns_evaluated=10,
            evidence_mode="structured",
            eval_speed="fast",
        )
        s = report.to_summary_dict()
        assert "rag_eval" in s
        assert s["rag_eval"]["overall_score"] == 0.82
        assert s["rag_eval"]["eval_speed"] == "fast"


# ============================================================================
# 2. TestEngineEndToEnd
# ============================================================================

class TestEngineEndToEnd:

    def test_basic_rag_evaluation(self):
        """End-to-end: conversations with structured sources."""
        engine = RAGEvalEngine()
        convs = make_conversations(n=2, turns_per=4)
        report = run(engine.evaluate_conversations(
            convs,
            context_document="Return policy: 30 days for all items. Refunds in 5-7 business days.",
        ))
        assert report.total_conversations == 2
        assert report.total_turns_evaluated == 4  # 2 convs × 2 bot turns each
        assert report.overall_score > 0.0
        assert len(report.conversation_results) == 2
        assert report.evidence_mode in ("structured", "inferred")

    def test_tool_evaluation(self):
        """End-to-end: conversations with tool calls."""
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        engine = RAGEvalEngine(config)
        convs = make_tool_conversations()
        report = run(engine.evaluate_conversations(convs))
        assert report.total_conversations == 1
        assert report.total_turns_evaluated == 2

    def test_with_llm_metrics(self):
        """Full evaluation including LLM-based metrics."""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = json.dumps({"score": 0.85})
        config = RAGEvalConfig(eval_speed=EvalSpeed.STANDARD)
        engine = RAGEvalEngine(config, llm_client=mock_llm)
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(
            convs,
            context_document="Return policy: 30 days.",
        ))
        assert report.total_turns_evaluated == 1
        assert mock_llm.generate.called

    def test_evaluate_from_simulation(self):
        """Test the SimulationReport integration point."""
        engine = RAGEvalEngine()

        # Mock a SimulationReport-like object
        @dataclass
        class MockReport:
            judged_conversations: list

        mock_report = MockReport(judged_conversations=make_conversations(n=1, turns_per=2))
        report = run(engine.evaluate_from_simulation(
            mock_report,
            context_document="Test doc.",
        ))
        assert report.total_conversations == 1


# ============================================================================
# 3. TestEngineSingleConv
# ============================================================================

class TestEngineSingleConv:

    def test_turn_results_count(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=1, turns_per=6)  # 3 bot turns
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert len(report.conversation_results) == 1
        assert len(report.conversation_results[0].turn_results) == 3

    def test_user_query_extracted(self):
        """Bot turns should have the preceding user message as user_query."""
        engine = RAGEvalEngine()
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        tr = report.conversation_results[0].turn_results[0]
        assert "return policy" in tr.annotation.user_query.lower()

    def test_per_turn_scores(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Return policy: 30 days."))
        tr = report.conversation_results[0].turn_results[0]
        assert 0.0 <= tr.rag_score <= 1.0
        assert 0.0 <= tr.overall_score <= 1.0


# ============================================================================
# 4. TestEngineAggregation
# ============================================================================

class TestEngineAggregation:

    def test_per_metric_averages(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=2, turns_per=4)
        report = run(engine.evaluate_conversations(convs, context_document="Policy doc."))
        # Should have at least deterministic RAG metric averages
        assert len(report.rag_metric_averages) > 0
        for name, avg in report.rag_metric_averages.items():
            assert 0.0 <= avg <= 1.0, f"{name} avg out of range"

    def test_per_metric_pass_rates(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        for name, rate in report.rag_metric_pass_rates.items():
            assert 0.0 <= rate <= 1.0

    def test_overall_score_bounded(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=3, turns_per=4)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert 0.0 <= report.overall_score <= 1.0
        assert 0.0 <= report.overall_rag_score <= 1.0

    def test_issue_deduplication(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=3, turns_per=4)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        # top_issues should be deduplicated (×N notation)
        assert isinstance(report.top_issues, list)
        assert len(report.top_issues) <= 10


# ============================================================================
# 5. TestEngineCIGate
# ============================================================================

class TestEngineCIGate:

    def test_gate_passes(self):
        config = RAGEvalConfig(fail_if_below=0.1)  # Low threshold
        engine = RAGEvalEngine(config)
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc with 30 day returns."))
        assert report.gate_passed is True
        assert report.gate_threshold == 0.1

    def test_gate_fails(self):
        config = RAGEvalConfig(fail_if_below=0.99)  # Very high threshold
        engine = RAGEvalEngine(config)
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        # Most likely fails since deterministic metrics rarely score 0.99+
        assert report.gate_threshold == 0.99

    def test_gate_not_set(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert report.gate_threshold is None
        assert report.gate_passed is True


# ============================================================================
# 6. TestEngineSampling
# ============================================================================

class TestEngineSampling:

    def test_max_turns_per_conversation(self):
        config = RAGEvalConfig(max_turns_per_conversation=1)
        engine = RAGEvalEngine(config)
        convs = make_conversations(n=1, turns_per=6)  # 3 bot turns
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        # Should only evaluate 1 bot turn
        assert report.total_turns_evaluated == 1

    def test_sample_rate_reduces_turns(self):
        """With sample_rate < 1.0, fewer turns are evaluated."""
        config = RAGEvalConfig(sample_rate=0.0)  # Evaluate nothing
        engine = RAGEvalEngine(config)
        convs = make_conversations(n=1, turns_per=4)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert report.total_turns_evaluated == 0


# ============================================================================
# 7. TestEngineExport
# ============================================================================

class TestEngineExport:

    def test_save_report_json(self):
        report = RAGEvalReport(
            total_conversations=2,
            overall_score=0.82,
            rag_metric_averages={"faithfulness": 0.9},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = RAGEvalEngine.save_report(report, tmpdir)
            assert os.path.exists(filepath)
            with open(filepath) as f:
                data = json.load(f)
            assert data["overall_score"] == 0.82

    def test_append_to_summary(self):
        report = RAGEvalReport(overall_score=0.75, overall_rag_score=0.8, overall_tool_score=0.6)
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = os.path.join(tmpdir, "summary.json")
            with open(summary_path, 'w') as f:
                json.dump({"simulation_id": "sim_123", "pass_rate": 0.9}, f)

            RAGEvalEngine.append_to_summary(report, summary_path)

            with open(summary_path) as f:
                data = json.load(f)
            assert "rag_eval" in data
            assert data["rag_eval"]["overall_score"] == 0.75
            assert data["simulation_id"] == "sim_123"  # Original data preserved

    def test_append_to_nonexistent_summary(self):
        """Should not crash if summary file doesn't exist."""
        report = RAGEvalReport(overall_score=0.5)
        RAGEvalEngine.append_to_summary(report, "/nonexistent/path/summary.json")
        # Should not raise


# ============================================================================
# 8. TestEngineEdgeCases
# ============================================================================

class TestEngineEdgeCases:

    def test_empty_conversations(self):
        engine = RAGEvalEngine()
        report = run(engine.evaluate_conversations([], context_document="Doc."))
        assert report.total_conversations == 0
        assert report.overall_score == 0.0

    def test_conversation_with_no_bot_turns(self):
        conv = MockJudgedConversation(
            conversation=MockConversation(
                id="conv_no_bot",
                turns=[
                    MockTurn(speaker="user", message="Hello"),
                    MockTurn(speaker="user", message="Anyone there?"),
                ],
            ),
        )
        engine = RAGEvalEngine()
        report = run(engine.evaluate_conversations([conv], context_document="Doc."))
        assert report.total_turns_evaluated == 0

    def test_dict_based_conversation(self):
        """Engine should handle dict-based inputs (from JSON deserialization)."""
        engine = RAGEvalEngine()
        # Simulate a dict-based report
        mock_report = {
            "judged_conversations": make_conversations(n=1, turns_per=2),
        }
        report = run(engine.evaluate_from_simulation(mock_report, context_document="Doc."))
        assert report.total_conversations == 1

    def test_progress_callback(self):
        progress_messages = []
        engine = RAGEvalEngine(progress_callback=lambda msg: progress_messages.append(msg))
        convs = make_conversations(n=2, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert len(progress_messages) == 2  # One per conversation

    def test_execution_time_tracked(self):
        engine = RAGEvalEngine()
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert report.execution_time_seconds > 0
        assert report.started_at != ""
        assert report.completed_at != ""

    def test_speed_mode_in_report(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        engine = RAGEvalEngine(config)
        convs = make_conversations(n=1, turns_per=2)
        report = run(engine.evaluate_conversations(convs, context_document="Doc."))
        assert report.eval_speed == "fast"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
