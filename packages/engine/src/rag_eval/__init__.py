"""
RAG/Tool Evaluation Framework for AI SimTest.

Evaluates bots that use Retrieval-Augmented Generation (RAG) and/or
external tool/function calling. Supports three evidence modes:
  - trace: OTel/OpenInference spans
  - structured: metadata in bot response JSON
  - inferred: LLM+heuristic extraction from plain text
"""

from src.rag_eval.models import (
    # Enums
    EvidenceMode,
    JudgeReliability,
    EvalSpeed,
    RAGMetricType,
    ToolMetricType,
    # Evidence models
    RetrievedChunk,
    ToolCall,
    Citation,
    ResponseAnnotation,
    # Result models
    MetricResult,
    TurnRAGResult,
    ConversationRAGResult,
    # Config
    RAGEvalConfig,
    # Registries
    RAG_METRIC_RELIABILITY,
    TOOL_METRIC_RELIABILITY,
    SPEED_MODE_INCLUDES,
)

from src.rag_eval.annotator import ResponseAnnotator

__all__ = [
    "EvidenceMode",
    "JudgeReliability",
    "EvalSpeed",
    "RAGMetricType",
    "ToolMetricType",
    "RetrievedChunk",
    "ToolCall",
    "Citation",
    "ResponseAnnotation",
    "MetricResult",
    "TurnRAGResult",
    "ConversationRAGResult",
    "RAGEvalConfig",
    "RAG_METRIC_RELIABILITY",
    "TOOL_METRIC_RELIABILITY",
    "SPEED_MODE_INCLUDES",
    "ResponseAnnotator",
]
