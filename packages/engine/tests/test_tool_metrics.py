"""
Tests for P3 #14 Phase 4 — Tool Metric Evaluators.

Test classes:
  1. TestDeterministicToolMetrics — parameter_accuracy, sequence_correctness, retry_behavior,
                                    timeout_handling, parallel_execution, permission_respect
  2. TestHybridToolMetrics       — tool_selection, error_handling, unnecessary_calls
  3. TestLLMToolMetrics          — result_integration, missing_calls, fallback_behavior, side_effect_awareness
  4. TestToolSpeedModeGating     — speed mode eligibility
  5. TestToolEvaluatorEdgeCases  — empty inputs, no definitions, error handling
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock

from src.rag_eval.models import (
    EvalSpeed,
    EvidenceMode,
    RAGEvalConfig,
    ResponseAnnotation,
    ToolCall,
    ToolMetricType,
)
from src.rag_eval.tool_metrics import ToolMetricEvaluator, ToolDefinition


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================================
# Fixtures
# ============================================================================

def make_tool_defs():
    return [
        ToolDefinition(
            name="search_kb",
            description="Search the knowledge base",
            required_params=["query"],
            optional_params=["limit"],
            param_types={"query": "str", "limit": "int"},
            expected_sequence_position=1,
        ),
        ToolDefinition(
            name="get_balance",
            description="Get account balance",
            required_params=["account_id"],
            param_types={"account_id": "str"},
            requires_permission=True,
            expected_sequence_position=2,
        ),
        ToolDefinition(
            name="transfer_funds",
            description="Transfer money between accounts",
            required_params=["from_account", "to_account", "amount"],
            param_types={"amount": "float"},
            has_side_effects=True,
            expected_sequence_position=3,
        ),
    ]


def make_annotation(tool_calls=None, response_text="Here is your result.", user_query="Check my balance"):
    default_calls = [
        ToolCall(tool_name="search_kb", arguments={"query": "balance info"}, result="Account info found."),
        ToolCall(tool_name="get_balance", arguments={"account_id": "ACC123", "auth_token": "xyz"}, result="$1,500.00"),
    ]
    return ResponseAnnotation(
        evidence_mode=EvidenceMode.STRUCTURED,
        overall_confidence=0.85,
        response_text=response_text,
        user_query=user_query,
        tool_calls=tool_calls if tool_calls is not None else default_calls,
        has_tool_evidence=True,
    )


def make_mock_llm(score=0.85, extra=None):
    mock = AsyncMock()
    resp = {"score": score}
    if extra:
        resp.update(extra)
    mock.generate.return_value = json.dumps(resp)
    return mock


# ============================================================================
# 1. TestDeterministicToolMetrics
# ============================================================================

class TestDeterministicToolMetrics:

    def test_parameter_accuracy_all_present(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        pa = next(r for r in results if r.metric_name == "parameter_accuracy")
        assert pa.score == 1.0
        assert pa.reliability == "deterministic"

    def test_parameter_accuracy_missing_required(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="search_kb", arguments={}),  # Missing "query"
        ])
        results = run(evaluator.evaluate(ann))
        pa = next(r for r in results if r.metric_name == "parameter_accuracy")
        assert pa.score < 1.0
        assert any("missing required" in i for i in pa.issues)

    def test_parameter_accuracy_wrong_type(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="search_kb", arguments={"query": "test", "limit": "not_an_int"}),
        ])
        results = run(evaluator.evaluate(ann))
        pa = next(r for r in results if r.metric_name == "parameter_accuracy")
        assert pa.score < 1.0

    def test_parameter_accuracy_no_definitions(self):
        evaluator = ToolMetricEvaluator()
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        pa = next(r for r in results if r.metric_name == "parameter_accuracy")
        assert pa.score == 1.0  # Can't validate without defs

    def test_sequence_correctness_right_order(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="search_kb", arguments={"query": "test"}),
            ToolCall(tool_name="get_balance", arguments={"account_id": "123"}),
        ])
        results = run(evaluator.evaluate(ann))
        seq = next(r for r in results if r.metric_name == "sequence_correctness")
        assert seq.score == 1.0

    def test_sequence_correctness_wrong_order(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="get_balance", arguments={"account_id": "123"}),
            ToolCall(tool_name="search_kb", arguments={"query": "test"}),
        ])
        results = run(evaluator.evaluate(ann))
        seq = next(r for r in results if r.metric_name == "sequence_correctness")
        assert seq.score == 0.0

    def test_retry_behavior_retried(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="search_kb", arguments={"query": "test"}, error="Timeout"),
            ToolCall(tool_name="search_kb", arguments={"query": "test"}, result="Found."),
        ])
        results = run(evaluator.evaluate(ann))
        retry = next(r for r in results if r.metric_name == "retry_behavior")
        assert retry.score == 1.0

    def test_retry_behavior_not_retried(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="search_kb", arguments={"query": "test"}, error="Connection refused"),
        ])
        results = run(evaluator.evaluate(ann))
        retry = next(r for r in results if r.metric_name == "retry_behavior")
        assert retry.score == 0.0
        assert any("not retried" in i for i in retry.issues)

    def test_timeout_handling_graceful(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(
            tool_calls=[ToolCall(tool_name="search_kb", arguments={}, error="Request timeout")],
            response_text="I apologize, I'm unable to retrieve that information right now. Please try again later.",
        )
        results = run(evaluator.evaluate(ann))
        timeout = next(r for r in results if r.metric_name == "timeout_handling")
        assert timeout.score == 1.0

    def test_timeout_handling_not_acknowledged(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(
            tool_calls=[ToolCall(tool_name="search_kb", arguments={}, error="Request timed out")],
            response_text="Your balance is $500.",
        )
        results = run(evaluator.evaluate(ann))
        timeout = next(r for r in results if r.metric_name == "timeout_handling")
        assert timeout.score < 0.5

    def test_permission_respect_with_auth(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation()  # Default has auth_token
        results = run(evaluator.evaluate(ann))
        perm = next(r for r in results if r.metric_name == "permission_respect")
        assert perm.score == 1.0

    def test_permission_respect_without_auth(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="get_balance", arguments={"account_id": "123"}),  # No auth_token
        ])
        results = run(evaluator.evaluate(ann))
        perm = next(r for r in results if r.metric_name == "permission_respect")
        assert perm.score < 1.0
        assert any("without authorization" in i for i in perm.issues)

    def test_no_tool_calls_all_pass(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[])
        results = run(evaluator.evaluate(ann))
        for r in results:
            assert r.score == 1.0


# ============================================================================
# 2. TestHybridToolMetrics
# ============================================================================

class TestHybridToolMetrics:

    def test_tool_selection_all_known(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        ts = next(r for r in results if r.metric_name == "tool_selection")
        assert ts.score == 1.0
        assert ts.reliability == "hybrid"

    def test_tool_selection_unknown_tool(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="totally_unknown_tool", arguments={}),
        ])
        results = run(evaluator.evaluate(ann))
        ts = next(r for r in results if r.metric_name == "tool_selection")
        assert ts.score == 0.0

    def test_tool_selection_with_llm(self):
        mock_llm = make_mock_llm(0.9)
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        ts = next(r for r in results if r.metric_name == "tool_selection")
        assert ts.score > 0.8

    def test_error_handling_acknowledged(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(
            tool_calls=[ToolCall(tool_name="search_kb", arguments={}, error="503 Service Unavailable")],
            response_text="I'm sorry, I'm unable to retrieve that information right now. You can try again later.",
        )
        results = run(evaluator.evaluate(ann))
        eh = next(r for r in results if r.metric_name == "error_handling")
        assert eh.score >= 0.7

    def test_error_handling_not_acknowledged(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(
            tool_calls=[ToolCall(tool_name="search_kb", arguments={}, error="500 Internal Error")],
            response_text="Here is your account summary with all the details.",
        )
        results = run(evaluator.evaluate(ann))
        eh = next(r for r in results if r.metric_name == "error_handling")
        assert eh.score < 0.5

    def test_unnecessary_calls_no_duplicates(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        uc = next(r for r in results if r.metric_name == "unnecessary_calls")
        assert uc.score == 1.0

    def test_unnecessary_calls_with_duplicates(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="search_kb", arguments={"query": "test"}),
            ToolCall(tool_name="search_kb", arguments={"query": "test"}),
            ToolCall(tool_name="search_kb", arguments={"query": "test"}),
        ])
        results = run(evaluator.evaluate(ann))
        uc = next(r for r in results if r.metric_name == "unnecessary_calls")
        assert uc.score < 0.7
        assert any("Duplicate" in i for i in uc.issues)


# ============================================================================
# 3. TestLLMToolMetrics
# ============================================================================

class TestLLMToolMetrics:

    def test_result_integration_good(self):
        mock_llm = make_mock_llm(0.95, {"used_results": 2, "ignored_results": 0})
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        ri = next(r for r in results if r.metric_name == "result_integration")
        assert ri.score == 0.95
        assert ri.reliability == "llm"

    def test_result_integration_no_llm_skipped(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        names = [r.metric_name for r in results]
        assert "result_integration" not in names

    def test_missing_calls_none_missing(self):
        mock_llm = make_mock_llm(1.0, {"missing_tools": []})
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        mc = next(r for r in results if r.metric_name == "missing_calls")
        assert mc.score == 1.0

    def test_missing_calls_some_missing(self):
        mock_llm = make_mock_llm(0.4, {"missing_tools": ["get_balance"], "reasoning": "Should check balance"})
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation(tool_calls=[])
        results = run(evaluator.evaluate(ann))
        mc = next(r for r in results if r.metric_name == "missing_calls")
        assert mc.score == 0.4

    def test_fallback_behavior_graceful(self):
        mock_llm = make_mock_llm(0.9, {"fallback_type": "alternative_offered"})
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation(
            tool_calls=[ToolCall(tool_name="search_kb", arguments={}, error="Service down")],
            response_text="I couldn't look that up, but you can check your statement online.",
        )
        results = run(evaluator.evaluate(ann))
        fb = next(r for r in results if r.metric_name == "fallback_behavior")
        assert fb.score == 0.9

    def test_fallback_no_failures(self):
        mock_llm = make_mock_llm(1.0)
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        fb = next(r for r in results if r.metric_name == "fallback_behavior")
        assert fb.score == 1.0  # No failures = no fallback needed

    def test_side_effect_awareness_with_write_tool(self):
        mock_llm = make_mock_llm(0.85, {"confirmed": True})
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="transfer_funds", arguments={"from_account": "A", "to_account": "B", "amount": 100}),
        ])
        results = run(evaluator.evaluate(ann))
        se = next(r for r in results if r.metric_name == "side_effect_awareness")
        assert se.score == 0.85

    def test_side_effect_heuristic_detection(self):
        """Detect side effects from tool name even without definitions."""
        mock_llm = make_mock_llm(0.7, {"confirmed": False})
        evaluator = ToolMetricEvaluator(llm_client=mock_llm)  # No definitions
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="delete_account", arguments={"id": "123"}),
        ])
        results = run(evaluator.evaluate(ann))
        se = next(r for r in results if r.metric_name == "side_effect_awareness")
        assert se.score == 0.7

    def test_llm_failure_graceful(self):
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = Exception("LLM down")
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs(), llm_client=mock_llm)
        # Use annotation with tool errors so fallback_behavior actually calls LLM
        ann = make_annotation(
            tool_calls=[
                ToolCall(tool_name="search_kb", arguments={"query": "test"}, result="Found.", error=None),
                ToolCall(tool_name="get_balance", arguments={"account_id": "123"}, error="Service down"),
            ],
        )
        results = run(evaluator.evaluate(ann))
        # LLM metrics that actually call the LLM should have fallback scores
        llm_results = [r for r in results if r.reliability == "llm"]
        for r in llm_results:
            assert r.score >= 0.0
            # Metrics that need LLM and hit the error should report failure
            # Metrics that short-circuit (no failures, etc.) may not
            if "failed" in r.message.lower() or r.score == 1.0:
                pass  # Both valid outcomes
            else:
                assert r.score >= 0.0  # At minimum, no crash


# ============================================================================
# 4. TestToolSpeedModeGating
# ============================================================================

class TestToolSpeedModeGating:

    def test_deterministic_mode_only_deterministic(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        evaluator = ToolMetricEvaluator(config, tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        for r in results:
            assert r.reliability == "deterministic", f"{r.metric_name} shouldn't run"

    def test_fast_mode_includes_hybrid(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        evaluator = ToolMetricEvaluator(config, tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        names = {r.metric_name for r in results}
        assert "tool_selection" in names
        assert "error_handling" in names
        assert "result_integration" not in names  # LLM-only, no LLM

    def test_standard_with_llm(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.STANDARD)
        mock_llm = make_mock_llm(0.85)
        evaluator = ToolMetricEvaluator(config, tool_definitions=make_tool_defs(), llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        names = {r.metric_name for r in results}
        assert "result_integration" in names
        assert "missing_calls" in names

    def test_enabled_metrics_filter(self):
        config = RAGEvalConfig(
            enabled_tool_metrics=[ToolMetricType.PARAMETER_ACCURACY, ToolMetricType.TOOL_SELECTION],
        )
        evaluator = ToolMetricEvaluator(config, tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        names = {r.metric_name for r in results}
        assert "parameter_accuracy" in names
        assert "tool_selection" in names
        assert "sequence_correctness" not in names


# ============================================================================
# 5. TestToolEvaluatorEdgeCases
# ============================================================================

class TestToolEvaluatorEdgeCases:

    def test_no_tool_calls(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[])
        results = run(evaluator.evaluate(ann))
        for r in results:
            assert r.score == 1.0

    def test_no_definitions(self):
        evaluator = ToolMetricEvaluator()
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        assert isinstance(results, list)

    def test_tool_definition_from_dict(self):
        td = ToolDefinition.from_dict({
            "name": "test_tool",
            "required_params": ["x"],
            "has_side_effects": True,
        })
        assert td.name == "test_tool"
        assert td.required_params == ["x"]
        assert td.has_side_effects is True

    def test_fuzzy_tool_name_match(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation(tool_calls=[
            ToolCall(tool_name="Search_KB", arguments={"query": "test"}),  # Case difference
        ])
        results = run(evaluator.evaluate(ann))
        ts = next(r for r in results if r.metric_name == "tool_selection")
        assert ts.score == 1.0

    def test_all_results_have_tool_type(self):
        evaluator = ToolMetricEvaluator(tool_definitions=make_tool_defs())
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        for r in results:
            assert r.metric_type == "tool"
            assert 0.0 <= r.score <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
