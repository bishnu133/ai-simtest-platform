"""
Tests for P3 #14 RAG/Tool Evaluation Framework — Phase 1 (Models) + Phase 2 (Annotator).

Test classes:
  1. TestEnums                 — All enum values and membership
  2. TestMetricRegistries      — Reliability mappings and speed mode gating
  3. TestEvidenceModels        — RetrievedChunk, ToolCall, Citation, ResponseAnnotation
  4. TestMetricResults         — MetricResult, TurnRAGResult, ConversationRAGResult
  5. TestRAGEvalConfig         — Config thresholds, speed eligibility, defaults
  6. TestAnnotatorAutoDetect   — Evidence mode auto-detection logic
  7. TestAnnotatorTrace        — Trace span extraction (OTel + OpenInference)
  8. TestAnnotatorStructured   — Structured metadata extraction (OpenAI, LangChain, generic)
  9. TestAnnotatorInferred     — Heuristic text extraction (citations, sources, tools, URLs)
  10. TestAnnotatorFallback    — Fallback chain: trace→structured→inferred
  11. TestAnnotatorLLM         — LLM-assisted extraction in inferred mode
  12. TestAnnotatorEdgeCases   — Empty inputs, malformed data, large payloads
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.rag_eval.models import (
    EvidenceMode,
    JudgeReliability,
    EvalSpeed,
    RAGMetricType,
    ToolMetricType,
    RAG_METRIC_RELIABILITY,
    TOOL_METRIC_RELIABILITY,
    SPEED_MODE_INCLUDES,
    RetrievedChunk,
    ToolCall,
    Citation,
    ResponseAnnotation,
    MetricResult,
    TurnRAGResult,
    ConversationRAGResult,
    RAGEvalConfig,
)
from src.rag_eval.annotator import ResponseAnnotator


# ============================================================================
# Helpers
# ============================================================================

def run(coro):
    """Run async coroutine synchronously for tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


def make_retrieval_span(docs=None, name="retrieval"):
    """Create a mock OTel retrieval span."""
    return {
        "name": name,
        "attributes": {
            "retrieval.documents": docs or [
                {"content": "Return policy is 30 days.", "source_id": "doc_1", "score": 0.92},
                {"content": "Refunds take 5-7 business days.", "source_id": "doc_2", "score": 0.85},
            ],
        },
        "start_time_unix_nano": 1000000000,
        "end_time_unix_nano": 1050000000,
    }


def make_tool_span(tool_name="search_kb", args=None, result=None, error=None):
    """Create a mock OTel tool span."""
    span = {
        "name": f"tool.{tool_name}",
        "attributes": {
            "tool.name": tool_name,
            "tool.parameters": json.dumps(args or {"query": "return policy"}),
            "tool.output": result or "Found 3 matching documents.",
        },
        "start_time_unix_nano": 1000000000,
        "end_time_unix_nano": 1100000000,
        "status": {"status_code": "OK"},
    }
    if error:
        span["attributes"]["tool.error"] = error
        span["status"] = {"status_code": "ERROR", "message": error}
    return span


def make_openinference_span(kind="RETRIEVER", docs=None, tool_name=None):
    """Create an OpenInference-style span."""
    attrs = {"openinference.span.kind": kind}
    if kind == "RETRIEVER":
        attrs["retrieval.documents"] = docs or [
            {"content": "FAQ answer about shipping.", "id": "faq_1"}
        ]
    elif kind == "TOOL":
        attrs["tool.name"] = tool_name or "calculate_shipping"
        attrs["tool.parameters"] = json.dumps({"weight": 2.5, "destination": "US"})
        attrs["tool.output"] = "$12.99"
    return {"name": f"openinference_{kind.lower()}", "attributes": attrs}


# ============================================================================
# 1. TestEnums
# ============================================================================

class TestEnums:
    """Verify all enum values and membership."""

    def test_evidence_modes(self):
        assert EvidenceMode.TRACE.value == "trace"
        assert EvidenceMode.STRUCTURED.value == "structured"
        assert EvidenceMode.INFERRED.value == "inferred"
        assert len(EvidenceMode) == 3

    def test_judge_reliability(self):
        assert JudgeReliability.DETERMINISTIC.value == "deterministic"
        assert JudgeReliability.LLM.value == "llm"
        assert JudgeReliability.HYBRID.value == "hybrid"
        assert len(JudgeReliability) == 3

    def test_eval_speed_modes(self):
        assert EvalSpeed.DETERMINISTIC.value == "deterministic"
        assert EvalSpeed.FAST.value == "fast"
        assert EvalSpeed.STANDARD.value == "standard"
        assert EvalSpeed.FULL.value == "full"
        assert len(EvalSpeed) == 4

    def test_rag_metric_types_count(self):
        """12 RAG metrics: 6 core + 6 extended."""
        assert len(RAGMetricType) == 12
        assert RAGMetricType.FAITHFULNESS.value == "faithfulness"
        assert RAGMetricType.HALLUCINATION.value == "hallucination"
        assert RAGMetricType.CONFLICTING_EVIDENCE.value == "conflicting_evidence"

    def test_tool_metric_types_count(self):
        """13 Tool metrics: 7 core + 6 extended."""
        assert len(ToolMetricType) == 13
        assert ToolMetricType.TOOL_SELECTION.value == "tool_selection"
        assert ToolMetricType.SIDE_EFFECT_AWARENESS.value == "side_effect_awareness"

    def test_enum_string_conversion(self):
        """Enums are str enums — usable as dict keys and JSON values."""
        assert str(EvidenceMode.TRACE) == "EvidenceMode.TRACE"
        assert EvidenceMode("trace") == EvidenceMode.TRACE
        assert RAGMetricType("faithfulness") == RAGMetricType.FAITHFULNESS


# ============================================================================
# 2. TestMetricRegistries
# ============================================================================

class TestMetricRegistries:
    """Verify metric-to-reliability mappings and speed mode gating."""

    def test_all_rag_metrics_have_reliability(self):
        for metric in RAGMetricType:
            assert metric in RAG_METRIC_RELIABILITY, f"Missing reliability for {metric}"

    def test_all_tool_metrics_have_reliability(self):
        for metric in ToolMetricType:
            assert metric in TOOL_METRIC_RELIABILITY, f"Missing reliability for {metric}"

    def test_citation_accuracy_is_deterministic(self):
        assert RAG_METRIC_RELIABILITY[RAGMetricType.CITATION_ACCURACY] == JudgeReliability.DETERMINISTIC

    def test_hallucination_is_llm(self):
        assert RAG_METRIC_RELIABILITY[RAGMetricType.HALLUCINATION] == JudgeReliability.LLM

    def test_faithfulness_is_hybrid(self):
        assert RAG_METRIC_RELIABILITY[RAGMetricType.FAITHFULNESS] == JudgeReliability.HYBRID

    def test_parameter_accuracy_is_deterministic(self):
        assert TOOL_METRIC_RELIABILITY[ToolMetricType.PARAMETER_ACCURACY] == JudgeReliability.DETERMINISTIC

    def test_speed_mode_deterministic_only(self):
        """DETERMINISTIC speed mode only includes deterministic checks."""
        includes = SPEED_MODE_INCLUDES[EvalSpeed.DETERMINISTIC]
        assert includes == {JudgeReliability.DETERMINISTIC}

    def test_speed_mode_fast_includes_hybrid(self):
        includes = SPEED_MODE_INCLUDES[EvalSpeed.FAST]
        assert JudgeReliability.DETERMINISTIC in includes
        assert JudgeReliability.HYBRID in includes
        assert JudgeReliability.LLM not in includes

    def test_speed_mode_standard_includes_all(self):
        includes = SPEED_MODE_INCLUDES[EvalSpeed.STANDARD]
        assert len(includes) == 3

    def test_speed_mode_full_includes_all(self):
        includes = SPEED_MODE_INCLUDES[EvalSpeed.FULL]
        assert len(includes) == 3


# ============================================================================
# 3. TestEvidenceModels
# ============================================================================

class TestEvidenceModels:
    """Test evidence data models: RetrievedChunk, ToolCall, Citation, ResponseAnnotation."""

    def test_retrieved_chunk_defaults(self):
        chunk = RetrievedChunk(content="Hello world")
        assert chunk.content == "Hello world"
        assert chunk.source_id == ""
        assert chunk.relevance_score == 0.0
        assert chunk.metadata == {}

    def test_retrieved_chunk_with_all_fields(self):
        chunk = RetrievedChunk(
            content="Policy text",
            source_id="doc_42",
            source_name="Return Policy",
            relevance_score=0.95,
            chunk_index=3,
            metadata={"page": 2},
        )
        assert chunk.source_name == "Return Policy"
        assert chunk.relevance_score == 0.95
        assert chunk.metadata["page"] == 2

    def test_tool_call_defaults(self):
        tc = ToolCall(tool_name="search")
        assert tc.tool_name == "search"
        assert tc.arguments == {}
        assert tc.result is None
        assert tc.error is None
        assert tc.duration_ms is None

    def test_tool_call_with_error(self):
        tc = ToolCall(
            tool_name="get_balance",
            arguments={"account_id": "123"},
            error="Timeout after 30s",
            duration_ms=30000.0,
        )
        assert tc.error == "Timeout after 30s"
        assert tc.result is None

    def test_citation_defaults(self):
        cit = Citation(text="Returns are within 30 days")
        assert cit.source_reference == ""
        assert cit.verified is None
        assert cit.confidence == 1.0

    def test_response_annotation_defaults(self):
        ann = ResponseAnnotation(evidence_mode=EvidenceMode.INFERRED)
        assert ann.overall_confidence == 1.0
        assert ann.retrieved_chunks == []
        assert ann.citations == []
        assert ann.tool_calls == []
        assert ann.has_rag_evidence is False
        assert ann.has_tool_evidence is False
        assert ann.warnings == []

    def test_response_annotation_to_dict(self):
        ann = ResponseAnnotation(
            evidence_mode=EvidenceMode.STRUCTURED,
            overall_confidence=0.85,
            has_rag_evidence=True,
            retrieved_chunks=[RetrievedChunk(content="Test chunk", source_id="s1", relevance_score=0.9)],
            citations=[Citation(text="Claim X", source_reference="Doc A", confidence=0.8)],
            tool_calls=[ToolCall(tool_name="search", arguments={"q": "test"})],
            has_tool_evidence=True,
        )
        d = ann.to_dict()
        assert d["evidence_mode"] == "structured"
        assert d["overall_confidence"] == 0.85
        assert d["has_rag_evidence"] is True
        assert d["has_tool_evidence"] is True
        assert len(d["retrieved_chunks"]) == 1
        assert d["retrieved_chunks"][0]["source_id"] == "s1"
        assert len(d["citations"]) == 1
        assert d["citations"][0]["confidence"] == 0.8
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["tool_name"] == "search"

    def test_response_annotation_to_dict_empty(self):
        ann = ResponseAnnotation(evidence_mode=EvidenceMode.INFERRED)
        d = ann.to_dict()
        assert d["retrieved_chunks"] == []
        assert d["citations"] == []
        assert d["tool_calls"] == []


# ============================================================================
# 4. TestMetricResults
# ============================================================================

class TestMetricResults:
    """Test metric result models and serialization."""

    def test_metric_result_defaults(self):
        mr = MetricResult(metric_name="faithfulness", metric_type="rag")
        assert mr.score == 0.0
        assert mr.passed is True
        assert mr.threshold == 0.7
        assert mr.reliability == "deterministic"

    def test_metric_result_to_dict(self):
        mr = MetricResult(
            metric_name="tool_selection",
            metric_type="tool",
            score=0.85,
            passed=True,
            threshold=0.7,
            reliability="hybrid",
            confidence=0.9,
            message="Correct tool selected",
            issues=[],
        )
        d = mr.to_dict()
        assert d["metric_name"] == "tool_selection"
        assert d["score"] == 0.85
        assert d["reliability"] == "hybrid"

    def test_turn_rag_result_to_dict(self):
        ann = ResponseAnnotation(evidence_mode=EvidenceMode.INFERRED)
        tr = TurnRAGResult(
            turn_index=2,
            annotation=ann,
            rag_score=0.8,
            tool_score=0.0,
            overall_score=0.8,
        )
        d = tr.to_dict()
        assert d["turn_index"] == 2
        assert d["evidence_mode"] == "inferred"
        assert d["rag_score"] == 0.8

    def test_conversation_rag_result_to_dict(self):
        cr = ConversationRAGResult(
            conversation_id="conv_abc",
            avg_rag_score=0.75,
            avg_tool_score=0.6,
            avg_overall_score=0.7,
            evidence_mode=EvidenceMode.STRUCTURED,
            total_rag_issues=2,
            total_tool_issues=1,
        )
        d = cr.to_dict()
        assert d["conversation_id"] == "conv_abc"
        assert d["evidence_mode"] == "structured"
        assert d["total_rag_issues"] == 2


# ============================================================================
# 5. TestRAGEvalConfig
# ============================================================================

class TestRAGEvalConfig:
    """Test configuration, thresholds, and speed eligibility."""

    def test_default_config(self):
        cfg = RAGEvalConfig()
        assert cfg.evidence_mode is None  # auto-detect
        assert cfg.eval_speed == EvalSpeed.STANDARD
        assert cfg.default_rag_threshold == 0.7
        assert cfg.default_tool_threshold == 0.7
        assert cfg.sample_rate == 1.0

    def test_get_threshold_default(self):
        cfg = RAGEvalConfig()
        assert cfg.get_threshold("faithfulness") == 0.7
        assert cfg.get_threshold("tool_selection") == 0.7

    def test_get_threshold_override(self):
        cfg = RAGEvalConfig(metric_thresholds={"faithfulness": 0.9, "tool_selection": 0.5})
        assert cfg.get_threshold("faithfulness") == 0.9
        assert cfg.get_threshold("tool_selection") == 0.5
        assert cfg.get_threshold("hallucination") == 0.7  # falls back to default

    def test_is_metric_eligible_deterministic_mode(self):
        cfg = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        # citation_accuracy is deterministic → eligible
        assert cfg.is_metric_eligible("citation_accuracy") is True
        # hallucination is LLM → not eligible in deterministic mode
        assert cfg.is_metric_eligible("hallucination") is False
        # faithfulness is hybrid → not eligible
        assert cfg.is_metric_eligible("faithfulness") is False

    def test_is_metric_eligible_fast_mode(self):
        cfg = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        assert cfg.is_metric_eligible("citation_accuracy") is True  # deterministic
        assert cfg.is_metric_eligible("faithfulness") is True       # hybrid
        assert cfg.is_metric_eligible("hallucination") is False     # LLM only

    def test_is_metric_eligible_standard_mode(self):
        cfg = RAGEvalConfig(eval_speed=EvalSpeed.STANDARD)
        assert cfg.is_metric_eligible("citation_accuracy") is True
        assert cfg.is_metric_eligible("faithfulness") is True
        assert cfg.is_metric_eligible("hallucination") is True

    def test_is_metric_eligible_unknown_metric(self):
        cfg = RAGEvalConfig()
        assert cfg.is_metric_eligible("nonexistent_metric") is False

    def test_config_with_custom_thresholds(self):
        cfg = RAGEvalConfig(
            default_rag_threshold=0.8,
            default_tool_threshold=0.6,
        )
        assert cfg.get_threshold("faithfulness") == 0.8
        assert cfg.get_threshold("tool_selection") == 0.6

    def test_fail_if_below_gate(self):
        cfg = RAGEvalConfig(fail_if_below=0.75)
        assert cfg.fail_if_below == 0.75


# ============================================================================
# 6. TestAnnotatorAutoDetect
# ============================================================================

class TestAnnotatorAutoDetect:
    """Test evidence mode auto-detection logic."""

    def test_detect_trace_from_retrieval_span(self):
        annotator = ResponseAnnotator()
        spans = [make_retrieval_span()]
        mode = annotator._detect_mode({}, spans, "Some response")
        assert mode == EvidenceMode.TRACE

    def test_detect_trace_from_tool_span(self):
        annotator = ResponseAnnotator()
        spans = [make_tool_span()]
        mode = annotator._detect_mode({}, spans, "Some response")
        assert mode == EvidenceMode.TRACE

    def test_detect_trace_from_openinference(self):
        annotator = ResponseAnnotator()
        spans = [make_openinference_span(kind="RETRIEVER")]
        mode = annotator._detect_mode({}, spans, "Some response")
        assert mode == EvidenceMode.TRACE

    def test_detect_structured_from_sources_key(self):
        annotator = ResponseAnnotator()
        metadata = {"sources": [{"content": "doc text"}]}
        mode = annotator._detect_mode(metadata, [], "Some response")
        assert mode == EvidenceMode.STRUCTURED

    def test_detect_structured_from_tool_calls_key(self):
        annotator = ResponseAnnotator()
        metadata = {"tool_calls": [{"function": {"name": "search"}}]}
        mode = annotator._detect_mode(metadata, [], "Some response")
        assert mode == EvidenceMode.STRUCTURED

    def test_detect_structured_from_citations_key(self):
        annotator = ResponseAnnotator()
        metadata = {"citations": [{"text": "claim", "source": "doc"}]}
        mode = annotator._detect_mode(metadata, [], "Some response")
        assert mode == EvidenceMode.STRUCTURED

    def test_detect_inferred_when_no_metadata(self):
        annotator = ResponseAnnotator()
        mode = annotator._detect_mode({}, [], "The return policy is 30 days.")
        assert mode == EvidenceMode.INFERRED

    def test_detect_inferred_when_empty_spans(self):
        annotator = ResponseAnnotator()
        mode = annotator._detect_mode({}, [], "Based on the FAQ, shipping is free.")
        assert mode == EvidenceMode.INFERRED

    def test_detect_skips_irrelevant_spans(self):
        """Spans without retrieval/tool signals don't trigger TRACE mode."""
        annotator = ResponseAnnotator()
        spans = [{"name": "llm.generate", "attributes": {"model": "gpt-4"}}]
        mode = annotator._detect_mode({}, spans, "Hello")
        assert mode == EvidenceMode.INFERRED

    def test_forced_mode_overrides_detection(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        # Even with trace data, forced mode wins
        result = run(annotator.annotate(
            response_text="Test",
            trace_spans=[make_retrieval_span()],
        ))
        assert result.evidence_mode == EvidenceMode.INFERRED


# ============================================================================
# 7. TestAnnotatorTrace
# ============================================================================

class TestAnnotatorTrace:
    """Test trace span extraction (OTel + OpenInference)."""

    def test_extract_retrieval_chunks(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Based on our docs...",
            trace_spans=[make_retrieval_span()],
        ))
        assert result.has_rag_evidence is True
        assert len(result.retrieved_chunks) == 2
        assert result.retrieved_chunks[0].source_id == "doc_1"
        assert result.retrieved_chunks[0].relevance_score == 0.92
        assert result.retrieved_chunks[1].source_id == "doc_2"

    def test_extract_tool_call_from_span(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="I searched the knowledge base.",
            trace_spans=[make_tool_span("search_kb", {"query": "shipping"}, "3 results found")],
        ))
        assert result.has_tool_evidence is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "search_kb"
        assert result.tool_calls[0].arguments == {"query": "shipping"}
        assert result.tool_calls[0].result == "3 results found"

    def test_extract_tool_with_error(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="I couldn't fetch that info.",
            trace_spans=[make_tool_span("get_balance", error="Connection timeout")],
        ))
        assert result.has_tool_evidence is True
        assert result.tool_calls[0].error == "Connection timeout"

    def test_extract_tool_duration(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Here's the result.",
            trace_spans=[make_tool_span()],
        ))
        tc = result.tool_calls[0]
        # 1100000000 - 1000000000 = 100000000 ns = 100 ms
        assert tc.duration_ms == 100.0

    def test_extract_openinference_retriever(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="According to our FAQ...",
            trace_spans=[make_openinference_span(kind="RETRIEVER")],
        ))
        assert result.has_rag_evidence is True
        assert len(result.retrieved_chunks) == 1
        assert "shipping" in result.retrieved_chunks[0].content.lower() or "faq" in result.retrieved_chunks[0].content.lower()

    def test_extract_openinference_tool(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Shipping costs $12.99.",
            trace_spans=[make_openinference_span(kind="TOOL", tool_name="calculate_shipping")],
        ))
        assert result.has_tool_evidence is True
        assert result.tool_calls[0].tool_name == "calculate_shipping"

    def test_multiple_spans_combined(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Based on docs, shipping is $12.99.",
            trace_spans=[
                make_retrieval_span(),
                make_tool_span("calculate_shipping"),
            ],
        ))
        assert result.has_rag_evidence is True
        assert result.has_tool_evidence is True
        assert len(result.retrieved_chunks) == 2
        assert len(result.tool_calls) == 1

    def test_trace_confidence_is_1(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Test",
            trace_spans=[make_retrieval_span()],
        ))
        assert result.overall_confidence == 1.0

    def test_retrieval_docs_as_json_string(self):
        """Handle case where retrieval.documents is a JSON string."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        spans = [{
            "name": "retrieval",
            "attributes": {
                "retrieval.documents": json.dumps([
                    {"content": "Serialized doc", "source_id": "s1"}
                ]),
            },
        }]
        result = run(annotator.annotate(response_text="Test", trace_spans=spans))
        assert result.has_rag_evidence is True
        assert result.retrieved_chunks[0].content == "Serialized doc"


# ============================================================================
# 8. TestAnnotatorStructured
# ============================================================================

class TestAnnotatorStructured:
    """Test structured metadata extraction."""

    def test_extract_generic_sources(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Our return policy allows...",
            raw_metadata={
                "sources": [
                    {"content": "Return policy: 30 days", "source_id": "doc_1", "title": "Returns FAQ"},
                    {"content": "Refund processing: 5-7 days", "source_id": "doc_2"},
                ],
            },
        ))
        assert result.has_rag_evidence is True
        assert len(result.retrieved_chunks) == 2
        assert result.retrieved_chunks[0].source_name == "Returns FAQ"

    def test_extract_openai_tool_calls(self):
        """OpenAI-style: {tool_calls: [{function: {name, arguments}}]}"""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Your balance is $150.",
            raw_metadata={
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_balance",
                            "arguments": '{"account_id": "123"}',
                        },
                        "result": "$150.00",
                    },
                ],
            },
        ))
        assert result.has_tool_evidence is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "get_balance"
        assert result.tool_calls[0].arguments == {"account_id": "123"}

    def test_extract_langchain_source_documents(self):
        """LangChain-style: source_documents with page_content."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="According to our docs...",
            raw_metadata={
                "source_documents": [
                    {"page_content": "Shipping is free over $50.", "metadata": {"source": "shipping.md"}},
                ],
            },
        ))
        assert result.has_rag_evidence is True
        # source_documents matches via the source_keys list
        assert len(result.retrieved_chunks) == 1

    def test_extract_citations_from_metadata(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="The policy states...",
            raw_metadata={
                "citations": [
                    {"text": "30-day return window", "source": "return_policy.md", "verified": True},
                ],
            },
        ))
        assert len(result.citations) == 1
        assert result.citations[0].verified is True
        assert result.citations[0].confidence == 0.85

    def test_structured_confidence(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Test",
            raw_metadata={"sources": [{"content": "doc"}]},
        ))
        assert result.overall_confidence == 0.85

    def test_structured_no_evidence_confidence(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Test",
            raw_metadata={"unrelated_key": "value"},
        ))
        # No evidence found from structured metadata, but heuristics might run
        assert result.overall_confidence <= 0.7

    def test_tool_calls_as_json_string(self):
        """Handle tool_calls provided as a JSON string."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Result here",
            raw_metadata={
                "tool_calls": json.dumps([{"name": "lookup", "arguments": {"id": 1}}]),
            },
        ))
        assert result.has_tool_evidence is True
        assert result.tool_calls[0].tool_name == "lookup"

    def test_sources_as_string_list(self):
        """Handle sources as list of plain strings."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Test",
            raw_metadata={
                "sources": ["Return policy allows 30 day returns.", "Refunds in 5-7 days."],
            },
        ))
        assert result.has_rag_evidence is True
        assert len(result.retrieved_chunks) == 2
        assert "30 day" in result.retrieved_chunks[0].content


# ============================================================================
# 9. TestAnnotatorInferred
# ============================================================================

class TestAnnotatorInferred:
    """Test heuristic text extraction in inferred mode."""

    def test_numeric_citation_pattern(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="Returns are accepted within 30 days [1]. Refunds take 5-7 business days [2].",
        ))
        assert result.has_rag_evidence is True
        assert len(result.citations) >= 2

    def test_named_citation_pattern(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="As noted in [Company Return Policy], all items can be returned within 30 days.",
        ))
        assert result.has_rag_evidence is True
        # Should find "Company Return Policy" as a named citation
        refs = [c.source_reference for c in result.citations]
        assert any("Return Policy" in r for r in refs)

    def test_according_to_pattern(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="According to our FAQ page, shipping is free on orders over $50.",
        ))
        assert result.has_rag_evidence is True
        refs = [c.source_reference for c in result.citations]
        assert any("FAQ" in r for r in refs)

    def test_based_on_pattern(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="Based on the employee handbook, vacation days reset annually.",
        ))
        assert result.has_rag_evidence is True
        refs = [c.source_reference for c in result.citations]
        assert any("handbook" in r.lower() for r in refs)

    def test_url_detection(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="You can find more details at https://example.com/returns.",
        ))
        assert result.has_rag_evidence is True
        refs = [c.source_reference for c in result.citations]
        assert any("https://example.com/returns" in r for r in refs)

    def test_tool_mention_pattern(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="I used the search tool to find your order. Your tracking number is ABC123.",
        ))
        # Should detect tool mention
        assert result.has_tool_evidence is True
        names = [t.tool_name.lower() for t in result.tool_calls]
        assert any("search" in n for n in names)

    def test_code_style_function_mention(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="I called `get_order_status()` and your order is currently being shipped.",
        ))
        assert result.has_tool_evidence is True
        assert any("get_order_status" in t.tool_name for t in result.tool_calls)

    def test_inferred_confidence_with_evidence(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="According to our FAQ [1], the return window is 30 days.",
        ))
        # Inferred with evidence should have moderate confidence
        assert 0.3 <= result.overall_confidence <= 0.7

    def test_inferred_confidence_no_evidence(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="Hello, how can I help you today?",
        ))
        assert result.overall_confidence == 0.3

    def test_per_the_pattern(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="Per the billing terms document, late fees apply after 30 days.",
        ))
        assert result.has_rag_evidence is True

    def test_no_false_positives_on_plain_text(self):
        """Plain conversational text should not produce spurious evidence."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text="Sure, I can help with that. What would you like to know?",
        ))
        assert result.has_rag_evidence is False
        assert result.has_tool_evidence is False


# ============================================================================
# 10. TestAnnotatorFallback
# ============================================================================

class TestAnnotatorFallback:
    """Test fallback chain: trace→structured→inferred."""

    def test_empty_trace_falls_back_to_structured(self):
        """If trace spans have no relevant data, fall back to structured metadata."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Our return policy is 30 days.",
            trace_spans=[{"name": "llm.chat", "attributes": {"model": "gpt-4"}}],  # No retrieval/tool
            raw_metadata={"sources": [{"content": "Return policy doc"}]},
        ))
        # Should have fallen back to structured
        assert result.has_rag_evidence is True
        assert "fell back to structured" in result.warnings[0].lower()

    def test_empty_structured_falls_back_to_heuristics(self):
        """If structured metadata has no evidence, fall back to text heuristics."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="According to our FAQ, returns are within 30 days.",
            raw_metadata={"model": "gpt-4", "latency": 200},  # No evidence keys
        ))
        assert result.has_rag_evidence is True
        assert any("fell back to text" in w.lower() for w in result.warnings)

    def test_trace_with_data_no_fallback(self):
        """If trace has data, no fallback should occur."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(
            response_text="Test",
            trace_spans=[make_retrieval_span()],
        ))
        assert result.has_rag_evidence is True
        assert len(result.warnings) == 0

    def test_auto_detect_with_both_trace_and_structured(self):
        """When both trace and structured are available, trace wins."""
        annotator = ResponseAnnotator()  # Auto-detect
        result = run(annotator.annotate(
            response_text="Test",
            trace_spans=[make_retrieval_span()],
            raw_metadata={"sources": [{"content": "Also here"}]},
        ))
        assert result.evidence_mode == EvidenceMode.TRACE


# ============================================================================
# 11. TestAnnotatorLLM
# ============================================================================

class TestAnnotatorLLM:
    """Test LLM-assisted extraction in inferred mode."""

    def test_llm_extraction_adds_citations(self):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = json.dumps({
            "citations": [
                {"text": "Return window is 30 days", "source": "Return Policy v2"},
            ],
            "tool_calls": [],
            "sources": [],
            "has_rag": True,
            "has_tools": False,
        })

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="Based on our policy, the return window is 30 days.",
            user_query="What is the return policy?",
        ))
        assert result.has_rag_evidence is True
        # Should have both heuristic and LLM citations
        assert len(result.citations) >= 1
        assert result.extraction_metadata.get("llm_extraction") is True

    def test_llm_extraction_adds_tool_calls(self):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = json.dumps({
            "citations": [],
            "tool_calls": [{"name": "order_lookup", "arguments": {"order_id": "ORD-456"}}],
            "sources": [],
            "has_rag": False,
            "has_tools": True,
        })

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="I looked up your order using our system. It's being shipped.",
            user_query="Where is my order?",
        ))
        assert result.has_tool_evidence is True
        tool_names = [t.tool_name for t in result.tool_calls]
        assert "order_lookup" in tool_names

    def test_llm_failure_graceful(self):
        """LLM failure should not crash — just log a warning."""
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = Exception("API timeout")

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="According to our docs, shipping is free.",
            user_query="Is shipping free?",
        ))
        # Should still have heuristic results
        assert result.has_rag_evidence is True
        assert any("LLM extraction failed" in w for w in result.warnings)

    def test_llm_returns_malformed_json(self):
        """LLM returns non-JSON — should parse gracefully."""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = "Sure, here's the analysis: {invalid json"

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="Based on the FAQ, returns are easy.",
            user_query="How do returns work?",
        ))
        # Should not crash
        assert isinstance(result, ResponseAnnotation)

    def test_llm_not_called_without_signals(self):
        """LLM should not be called if text has no extraction signals."""
        mock_llm = AsyncMock()

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="Hello! How can I help you today?",
            user_query="Hi",
        ))
        # LLM should NOT have been called
        mock_llm.generate.assert_not_called()

    def test_llm_called_with_signals(self):
        """LLM should be called when text has RAG/tool signals."""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = '{"citations": [], "tool_calls": [], "sources": []}'

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="According to our documentation, the answer is yes.",
            user_query="Is feature X available?",
        ))
        mock_llm.generate.assert_called_once()

    def test_llm_confidence_boost(self):
        """LLM extraction should boost confidence by 0.15."""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = json.dumps({
            "citations": [{"text": "claim", "source": "doc"}],
            "tool_calls": [],
            "sources": [],
        })

        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED, llm_client=mock_llm)
        result = run(annotator.annotate(
            response_text="According to our FAQ, yes that's correct.",
            user_query="Is shipping free?",
        ))
        # Base inferred with rag evidence = 0.4, + LLM boost 0.15 = 0.55
        assert result.overall_confidence >= 0.5


# ============================================================================
# 12. TestAnnotatorEdgeCases
# ============================================================================

class TestAnnotatorEdgeCases:
    """Edge cases: empty inputs, malformed data, large payloads."""

    def test_empty_response_text(self):
        annotator = ResponseAnnotator()
        result = run(annotator.annotate(response_text=""))
        assert isinstance(result, ResponseAnnotation)
        assert result.has_rag_evidence is False
        assert result.has_tool_evidence is False

    def test_none_metadata(self):
        annotator = ResponseAnnotator()
        result = run(annotator.annotate(response_text="Hello", raw_metadata=None))
        assert isinstance(result, ResponseAnnotation)

    def test_empty_spans_list(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        result = run(annotator.annotate(response_text="Hello", trace_spans=[]))
        assert isinstance(result, ResponseAnnotation)

    def test_malformed_span_attributes(self):
        """Span with missing/wrong attribute types should not crash."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.TRACE)
        spans = [{
            "name": "retrieval",
            "attributes": {
                "retrieval.documents": "not a list and not json",
            },
        }]
        result = run(annotator.annotate(response_text="Test", trace_spans=spans))
        # Should handle gracefully — wraps string as single doc
        assert isinstance(result, ResponseAnnotation)

    def test_malformed_tool_call_arguments(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Result",
            raw_metadata={
                "tool_calls": [{"name": "search", "arguments": "not json {{{"}],
            },
        ))
        assert result.has_tool_evidence is True
        # Arguments should be wrapped as {"raw": "not json {{{"}
        assert result.tool_calls[0].arguments.get("raw") is not None

    def test_sync_annotation(self):
        """Test synchronous annotation (no LLM calls)."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = annotator.annotate_sync(
            response_text="Test",
            raw_metadata={"sources": [{"content": "Doc text", "title": "FAQ"}]},
        )
        assert isinstance(result, ResponseAnnotation)
        assert result.has_rag_evidence is True

    def test_sync_annotation_inferred(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = annotator.annotate_sync(
            response_text="According to our FAQ, yes.",
        )
        assert result.has_rag_evidence is True

    def test_large_response_text(self):
        """Large response text should not crash or take excessive time."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        large_text = "According to the document, " + "word " * 5000 + "[1]."
        result = run(annotator.annotate(response_text=large_text))
        assert isinstance(result, ResponseAnnotation)

    def test_special_characters_in_citations(self):
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        result = run(annotator.annotate(
            response_text='As stated in [Terms & Conditions (v2.1)], cancellations are free.',
        ))
        assert isinstance(result, ResponseAnnotation)

    def test_mixed_evidence_from_structured(self):
        """Metadata with both sources and tool_calls."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Your balance is $150 based on policy docs.",
            raw_metadata={
                "sources": [{"content": "Account policy doc", "source_id": "s1"}],
                "tool_calls": [{"name": "get_balance", "arguments": {"id": "123"}, "result": "$150"}],
            },
        ))
        assert result.has_rag_evidence is True
        assert result.has_tool_evidence is True
        assert len(result.retrieved_chunks) == 1
        assert len(result.tool_calls) == 1

    def test_tool_call_empty_result_and_error(self):
        """Empty string result/error should be normalized to None."""
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.STRUCTURED)
        result = run(annotator.annotate(
            response_text="Test",
            raw_metadata={
                "tool_calls": [{"name": "ping", "result": "", "error": ""}],
            },
        ))
        assert result.tool_calls[0].result is None
        assert result.tool_calls[0].error is None


# ============================================================================
# Run configuration
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
