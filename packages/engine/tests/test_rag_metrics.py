"""
Tests for P3 #14 Phase 3 — RAG Metric Evaluators.

Test classes:
  1. TestDeterministicMetrics  — citation_accuracy, source_coverage, context_utilization, attribution_completeness
  2. TestHybridMetrics         — faithfulness, answer_relevance, context_relevance, temporal_awareness
  3. TestLLMMetrics            — hallucination, noise_robustness, multi_hop_reasoning, conflicting_evidence
  4. TestSpeedModeGating       — deterministic/fast/standard/full mode eligibility
  5. TestEvaluatorEdgeCases    — empty inputs, error handling, sync mode
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock

from ai_simtest_engine.rag_eval.models import (
    EvalSpeed,
    RAGEvalConfig,
    RAGMetricType,
    ResponseAnnotation,
    RetrievedChunk,
    Citation,
    EvidenceMode,
)
from ai_simtest_engine.rag_eval.rag_metrics import RAGMetricEvaluator


# ============================================================================
# Helpers
# ============================================================================

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def make_annotation(
    response_text="The return policy allows 30-day returns for all items.",
    user_query="What is the return policy?",
    context_document="Return policy: All items may be returned within 30 days of purchase. Refunds processed in 5-7 business days.",
    chunks=None,
    citations=None,
    evidence_mode=EvidenceMode.STRUCTURED,
    has_rag=True,
):
    """Create a test ResponseAnnotation with realistic data."""
    default_chunks = [
        RetrievedChunk(
            content="Return policy: All items may be returned within 30 days of purchase.",
            source_id="doc_1",
            source_name="Return Policy FAQ",
            relevance_score=0.92,
        ),
        RetrievedChunk(
            content="Refunds are processed within 5-7 business days after receiving the item.",
            source_id="doc_2",
            source_name="Refund Guidelines",
            relevance_score=0.85,
        ),
    ]
    default_citations = [
        Citation(text="30-day returns", source_reference="Return Policy FAQ", confidence=0.85),
    ]

    return ResponseAnnotation(
        evidence_mode=evidence_mode,
        overall_confidence=0.85,
        response_text=response_text,
        user_query=user_query,
        context_document=context_document,
        retrieved_chunks=chunks if chunks is not None else default_chunks,
        citations=citations if citations is not None else default_citations,
        has_rag_evidence=has_rag,
    )


def make_mock_llm(score=0.85, extra_fields=None):
    """Create a mock LLM that returns consistent JSON scores."""
    mock = AsyncMock()
    response = {"score": score}
    if extra_fields:
        response.update(extra_fields)
    mock.generate.return_value = json.dumps(response)
    return mock


# ============================================================================
# 1. TestDeterministicMetrics
# ============================================================================

class TestDeterministicMetrics:
    """Test the 4 deterministic RAG metrics."""

    def test_citation_accuracy_all_verified(self):
        ann = make_annotation()
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cit_result = next(r for r in results if r.metric_name == "citation_accuracy")
        assert cit_result.score == 1.0
        assert cit_result.passed is True
        assert cit_result.reliability == "deterministic"

    def test_citation_accuracy_unverified(self):
        ann = make_annotation(
            citations=[
                Citation(text="claim", source_reference="Unknown Source XYZ", confidence=0.5),
            ],
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cit_result = next(r for r in results if r.metric_name == "citation_accuracy")
        assert cit_result.score == 0.0
        assert "Unverified citation" in cit_result.issues[0]

    def test_citation_accuracy_no_citations(self):
        ann = make_annotation(citations=[])
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cit_result = next(r for r in results if r.metric_name == "citation_accuracy")
        assert cit_result.score == 1.0  # Vacuously correct

    def test_citation_accuracy_partial(self):
        ann = make_annotation(
            citations=[
                Citation(text="good claim", source_reference="Return Policy FAQ"),
                Citation(text="bad claim", source_reference="Nonexistent Doc"),
            ],
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cit_result = next(r for r in results if r.metric_name == "citation_accuracy")
        assert cit_result.score == 0.5

    def test_source_coverage_all_referenced(self):
        ann = make_annotation(
            response_text="The return policy allows 30 days for returns. Refunds are processed within 5-7 business days after receiving the item.",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cov_result = next(r for r in results if r.metric_name == "source_coverage")
        assert cov_result.score > 0.5

    def test_source_coverage_none_referenced(self):
        ann = make_annotation(
            response_text="Hello, how can I help you today?",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cov_result = next(r for r in results if r.metric_name == "source_coverage")
        assert cov_result.score < 1.0

    def test_source_coverage_no_chunks(self):
        ann = make_annotation(chunks=[])
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        cov_result = next(r for r in results if r.metric_name == "source_coverage")
        assert cov_result.score == 1.0

    def test_context_utilization_high(self):
        ann = make_annotation(
            response_text="Return policy allows 30 days returns. Refunds processed 5-7 business days after receiving item purchase.",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        util_result = next(r for r in results if r.metric_name == "context_utilization")
        assert util_result.score > 0.3

    def test_context_utilization_no_chunks(self):
        ann = make_annotation(chunks=[])
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        util_result = next(r for r in results if r.metric_name == "context_utilization")
        assert util_result.score == 1.0

    def test_attribution_completeness_with_numbers(self):
        ann = make_annotation(
            response_text="Returns are accepted within 30 days [1]. Processing takes 5-7 business days.",
            citations=[Citation(text="30 days [1]", source_reference="1")],
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        attr_result = next(r for r in results if r.metric_name == "attribution_completeness")
        # First sentence has [1], second doesn't — should be partial
        assert attr_result.reliability == "deterministic"

    def test_attribution_completeness_no_factual_claims(self):
        ann = make_annotation(
            response_text="Hello! How can I help you today?",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        attr_result = next(r for r in results if r.metric_name == "attribution_completeness")
        assert attr_result.score == 1.0

    def test_attribution_completeness_empty_response(self):
        ann = make_annotation(response_text="")
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        attr_result = next(r for r in results if r.metric_name == "attribution_completeness")
        assert attr_result.score == 1.0


# ============================================================================
# 2. TestHybridMetrics
# ============================================================================

class TestHybridMetrics:
    """Test the 4 hybrid RAG metrics (deterministic + optional LLM)."""

    def test_faithfulness_without_llm(self):
        ann = make_annotation()
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        faith_result = next(r for r in results if r.metric_name == "faithfulness")
        assert faith_result.reliability == "hybrid"
        assert 0.0 <= faith_result.score <= 1.0

    def test_faithfulness_with_llm(self):
        mock_llm = make_mock_llm(0.9)
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        faith_result = next(r for r in results if r.metric_name == "faithfulness")
        assert faith_result.score > 0.5
        mock_llm.generate.assert_called()

    def test_faithfulness_no_context(self):
        ann = make_annotation(context_document="", chunks=[])
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        faith_result = next(r for r in results if r.metric_name == "faithfulness")
        assert faith_result.score == 1.0  # No context to compare

    def test_answer_relevance_good(self):
        ann = make_annotation(
            user_query="What is the return policy?",
            response_text="The return policy allows 30-day returns for all items purchased.",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        rel_result = next(r for r in results if r.metric_name == "answer_relevance")
        assert rel_result.score > 0.3

    def test_answer_relevance_no_query(self):
        ann = make_annotation(user_query="")
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        rel_result = next(r for r in results if r.metric_name == "answer_relevance")
        assert rel_result.score == 1.0

    def test_context_relevance_high_scores(self):
        ann = make_annotation(
            user_query="return policy",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        ctx_result = next(r for r in results if r.metric_name == "context_relevance")
        assert ctx_result.score > 0.5  # Chunks are relevant to query

    def test_context_relevance_no_chunks(self):
        ann = make_annotation(chunks=[], user_query="return policy")
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        ctx_result = next(r for r in results if r.metric_name == "context_relevance")
        assert ctx_result.score == 1.0

    def test_temporal_awareness_with_dates(self):
        ann = make_annotation(
            response_text="As of 2024, the return window is 30 days. This policy was updated in January 2024.",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        temp_result = next(r for r in results if r.metric_name == "temporal_awareness")
        assert temp_result.score >= 0.5  # Has dates

    def test_temporal_awareness_no_dates(self):
        ann = make_annotation(
            response_text="You can return items within the allowed window.",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        temp_result = next(r for r in results if r.metric_name == "temporal_awareness")
        assert temp_result.score == 1.0  # No temporal content

    def test_temporal_awareness_with_qualifier(self):
        ann = make_annotation(
            response_text="As of 2024, subject to change, the return window is 30 days.",
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        temp_result = next(r for r in results if r.metric_name == "temporal_awareness")
        assert temp_result.score >= 0.8  # Has qualifier


# ============================================================================
# 3. TestLLMMetrics
# ============================================================================

class TestLLMMetrics:
    """Test the 4 LLM-only RAG metrics."""

    def test_hallucination_with_llm(self):
        mock_llm = make_mock_llm(0.9, {"hallucinated_claims": [], "reasoning": "All claims supported"})
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        hall_result = next(r for r in results if r.metric_name == "hallucination")
        assert hall_result.score == 0.9
        assert hall_result.reliability == "llm"

    def test_hallucination_without_llm_skipped(self):
        evaluator = RAGMetricEvaluator()
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        hall_names = [r.metric_name for r in results]
        # LLM metrics should be skipped when no LLM is available
        assert "hallucination" not in hall_names

    def test_hallucination_llm_failure(self):
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = Exception("API error")
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        hall_result = next(r for r in results if r.metric_name == "hallucination")
        assert hall_result.score == 0.5  # Fallback score
        assert "failed" in hall_result.message.lower()

    def test_noise_robustness_no_noise(self):
        mock_llm = make_mock_llm(0.95, {"noise_influence": "none"})
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        # All chunks have high relevance — no noise
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        noise_result = next(r for r in results if r.metric_name == "noise_robustness")
        assert noise_result.score == 1.0  # No low-relevance chunks

    def test_noise_robustness_with_noisy_chunks(self):
        mock_llm = make_mock_llm(0.7, {"noise_influence": "minor"})
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation(
            chunks=[
                RetrievedChunk(content="Relevant doc", source_id="d1", relevance_score=0.9),
                RetrievedChunk(content="Totally unrelated weather info", source_id="d2", relevance_score=0.2),
            ],
        )
        results = run(evaluator.evaluate(ann))
        noise_result = next(r for r in results if r.metric_name == "noise_robustness")
        assert noise_result.score == 0.7

    def test_multi_hop_with_multiple_sources(self):
        mock_llm = make_mock_llm(0.8, {"sources_used": 2, "reasoning": "Combined info"})
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation()  # Has 2 chunks by default
        results = run(evaluator.evaluate(ann))
        mh_result = next(r for r in results if r.metric_name == "multi_hop_reasoning")
        assert mh_result.score == 0.8

    def test_multi_hop_single_source_skipped(self):
        mock_llm = make_mock_llm(0.8)
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation(
            chunks=[RetrievedChunk(content="Single source", source_id="d1")],
        )
        results = run(evaluator.evaluate(ann))
        mh_result = next(r for r in results if r.metric_name == "multi_hop_reasoning")
        assert mh_result.score == 1.0  # N/A with single source

    def test_conflicting_evidence_no_conflicts(self):
        mock_llm = make_mock_llm(0.95, {"conflicts_found": False, "reasoning": "Consistent"})
        evaluator = RAGMetricEvaluator(llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        conf_result = next(r for r in results if r.metric_name == "conflicting_evidence")
        assert conf_result.score == 0.95


# ============================================================================
# 4. TestSpeedModeGating
# ============================================================================

class TestSpeedModeGating:
    """Test that speed modes correctly filter which metrics run."""

    def test_deterministic_mode_only_deterministic(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        evaluator = RAGMetricEvaluator(config)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        for r in results:
            assert r.reliability == "deterministic", f"{r.metric_name} should not run in deterministic mode"

    def test_deterministic_mode_metrics(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        evaluator = RAGMetricEvaluator(config)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        metric_names = {r.metric_name for r in results}
        assert "citation_accuracy" in metric_names
        assert "source_coverage" in metric_names
        assert "context_utilization" in metric_names
        assert "attribution_completeness" in metric_names
        assert "hallucination" not in metric_names
        assert "faithfulness" not in metric_names

    def test_fast_mode_includes_hybrid(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        evaluator = RAGMetricEvaluator(config)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        metric_names = {r.metric_name for r in results}
        assert "faithfulness" in metric_names
        assert "answer_relevance" in metric_names
        assert "hallucination" not in metric_names  # LLM-only, no LLM

    def test_standard_mode_all_with_llm(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.STANDARD)
        mock_llm = make_mock_llm(0.85)
        evaluator = RAGMetricEvaluator(config, llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        metric_names = {r.metric_name for r in results}
        assert "hallucination" in metric_names
        assert "faithfulness" in metric_names
        assert "citation_accuracy" in metric_names

    def test_enabled_metrics_filter(self):
        config = RAGEvalConfig(
            enabled_rag_metrics=[RAGMetricType.CITATION_ACCURACY, RAGMetricType.FAITHFULNESS],
        )
        evaluator = RAGMetricEvaluator(config)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        metric_names = {r.metric_name for r in results}
        assert "citation_accuracy" in metric_names
        assert "faithfulness" in metric_names
        assert "source_coverage" not in metric_names

    def test_custom_threshold_applied(self):
        config = RAGEvalConfig(
            metric_thresholds={"citation_accuracy": 0.95},
            eval_speed=EvalSpeed.DETERMINISTIC,
        )
        evaluator = RAGMetricEvaluator(config)
        ann = make_annotation(
            citations=[
                Citation(text="good", source_reference="Return Policy FAQ"),
                Citation(text="bad", source_reference="Unknown"),
            ],
        )
        results = run(evaluator.evaluate(ann))
        cit_result = next(r for r in results if r.metric_name == "citation_accuracy")
        assert cit_result.threshold == 0.95
        assert cit_result.score == 0.5
        assert cit_result.passed is False  # 0.5 < 0.95


# ============================================================================
# 5. TestEvaluatorEdgeCases
# ============================================================================

class TestEvaluatorEdgeCases:
    """Edge cases: empty annotations, errors, sync mode."""

    def test_empty_annotation(self):
        ann = ResponseAnnotation(evidence_mode=EvidenceMode.INFERRED)
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        # Should produce results without crashing
        assert isinstance(results, list)
        for r in results:
            assert r.score >= 0.0

    def test_sync_evaluation(self):
        ann = make_annotation()
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        evaluator = RAGMetricEvaluator(config)
        results = evaluator.evaluate_sync(ann)
        assert len(results) >= 1
        for r in results:
            assert r.reliability == "deterministic"

    def test_evaluator_error_handling(self):
        """If one metric fails, others still run."""
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = Exception("Permanent failure")
        config = RAGEvalConfig(eval_speed=EvalSpeed.STANDARD)
        evaluator = RAGMetricEvaluator(config, llm_client=mock_llm)
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        # Deterministic metrics should still succeed
        det_results = [r for r in results if r.reliability == "deterministic"]
        assert len(det_results) >= 4
        for r in det_results:
            assert "error" not in r.message.lower()

    def test_very_long_response(self):
        ann = make_annotation(
            response_text="Return policy allows 30 days. " * 500,
        )
        evaluator = RAGMetricEvaluator()
        results = run(evaluator.evaluate(ann))
        assert isinstance(results, list)
        assert len(results) >= 4

    def test_results_have_correct_structure(self):
        evaluator = RAGMetricEvaluator()
        ann = make_annotation()
        results = run(evaluator.evaluate(ann))
        for r in results:
            assert r.metric_type == "rag"
            assert 0.0 <= r.score <= 1.0
            assert isinstance(r.passed, bool)
            assert r.reliability in ("deterministic", "hybrid", "llm")
            assert isinstance(r.issues, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
