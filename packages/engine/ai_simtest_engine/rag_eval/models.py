"""
RAG/Tool Evaluation Framework — Data Models (Phase 1)

Three evidence modes:
  - TRACE: OTel/OpenInference spans with retrieval + tool call details
  - STRUCTURED: Bot response contains structured metadata (sources, tool_calls)
  - INFERRED: LLM + heuristic extraction from plain text (lower confidence)

Metric types split into RAG (retrieval quality) and Tool (function calling correctness).
Each metric is classified as deterministic, LLM-based, or hybrid for speed mode gating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ============================================================================
# Evidence Modes
# ============================================================================

class EvidenceMode(str, Enum):
    """How RAG/tool evidence was obtained."""
    TRACE = "trace"           # OTel / OpenInference spans
    STRUCTURED = "structured" # Metadata in bot response JSON
    INFERRED = "inferred"     # Extracted from plain text via LLM/heuristics


# ============================================================================
# Metric Classification
# ============================================================================

class JudgeReliability(str, Enum):
    """How a metric is computed — determines speed-mode eligibility."""
    DETERMINISTIC = "deterministic"  # Rule-based, always same result
    LLM = "llm"                      # Requires LLM call
    HYBRID = "hybrid"                # Rules first, LLM for ambiguous cases


class EvalSpeed(str, Enum):
    """Evaluation speed modes — controls which metrics run."""
    DETERMINISTIC = "deterministic"  # Only deterministic checks (~0 cost)
    FAST = "fast"                    # Deterministic + cheap heuristics
    STANDARD = "standard"            # All except expensive deep checks
    FULL = "full"                    # Everything including LLM-heavy metrics


# ============================================================================
# RAG Metric Types
# ============================================================================

class RAGMetricType(str, Enum):
    """RAG-specific evaluation metrics."""
    # Core 6 (from approved design)
    FAITHFULNESS = "faithfulness"            # Does response stay true to retrieved context?
    ANSWER_RELEVANCE = "answer_relevance"    # Does answer address the user question?
    CONTEXT_RELEVANCE = "context_relevance"  # Were the right docs retrieved?
    CITATION_ACCURACY = "citation_accuracy"  # Are citations correct and verifiable?
    SOURCE_COVERAGE = "source_coverage"      # Does response use all relevant sources?
    HALLUCINATION = "hallucination"          # Does response contain unsupported claims?

    # Extended 6 (from approved design)
    CONTEXT_UTILIZATION = "context_utilization"      # How much of retrieved context was used?
    NOISE_ROBUSTNESS = "noise_robustness"            # Does irrelevant context cause errors?
    MULTI_HOP_REASONING = "multi_hop_reasoning"      # Can it combine info from multiple sources?
    TEMPORAL_AWARENESS = "temporal_awareness"         # Does it handle dated info correctly?
    ATTRIBUTION_COMPLETENESS = "attribution_completeness"  # Are all claims attributed?
    CONFLICTING_EVIDENCE = "conflicting_evidence"    # How does it handle contradictions?


class ToolMetricType(str, Enum):
    """Tool/function-calling evaluation metrics."""
    # Core 7 (from approved design)
    TOOL_SELECTION = "tool_selection"            # Right tool chosen for the task?
    PARAMETER_ACCURACY = "parameter_accuracy"    # Correct params passed?
    SEQUENCE_CORRECTNESS = "sequence_correctness"  # Tools called in right order?
    ERROR_HANDLING = "error_handling"            # Does bot handle tool failures?
    RESULT_INTEGRATION = "result_integration"    # Is tool output used correctly in response?
    UNNECESSARY_CALLS = "unnecessary_calls"      # Were there redundant tool invocations?
    MISSING_CALLS = "missing_calls"              # Should a tool have been called but wasn't?

    # Extended 6 (from approved design)
    RETRY_BEHAVIOR = "retry_behavior"            # Appropriate retry on transient failures?
    TIMEOUT_HANDLING = "timeout_handling"         # Does bot handle slow tool responses?
    PARALLEL_EXECUTION = "parallel_execution"    # Independent tools called in parallel?
    FALLBACK_BEHAVIOR = "fallback_behavior"      # Does bot degrade gracefully?
    PERMISSION_RESPECT = "permission_respect"    # Does it respect tool access controls?
    SIDE_EFFECT_AWARENESS = "side_effect_awareness"  # Aware of write/delete effects?


# ============================================================================
# Metric Registry — maps each metric to its reliability class
# ============================================================================

RAG_METRIC_RELIABILITY: Dict[RAGMetricType, JudgeReliability] = {
    RAGMetricType.FAITHFULNESS: JudgeReliability.HYBRID,
    RAGMetricType.ANSWER_RELEVANCE: JudgeReliability.HYBRID,
    RAGMetricType.CONTEXT_RELEVANCE: JudgeReliability.HYBRID,
    RAGMetricType.CITATION_ACCURACY: JudgeReliability.DETERMINISTIC,
    RAGMetricType.SOURCE_COVERAGE: JudgeReliability.DETERMINISTIC,
    RAGMetricType.HALLUCINATION: JudgeReliability.LLM,
    RAGMetricType.CONTEXT_UTILIZATION: JudgeReliability.DETERMINISTIC,
    RAGMetricType.NOISE_ROBUSTNESS: JudgeReliability.LLM,
    RAGMetricType.MULTI_HOP_REASONING: JudgeReliability.LLM,
    RAGMetricType.TEMPORAL_AWARENESS: JudgeReliability.HYBRID,
    RAGMetricType.ATTRIBUTION_COMPLETENESS: JudgeReliability.DETERMINISTIC,
    RAGMetricType.CONFLICTING_EVIDENCE: JudgeReliability.LLM,
}

TOOL_METRIC_RELIABILITY: Dict[ToolMetricType, JudgeReliability] = {
    ToolMetricType.TOOL_SELECTION: JudgeReliability.HYBRID,
    ToolMetricType.PARAMETER_ACCURACY: JudgeReliability.DETERMINISTIC,
    ToolMetricType.SEQUENCE_CORRECTNESS: JudgeReliability.DETERMINISTIC,
    ToolMetricType.ERROR_HANDLING: JudgeReliability.HYBRID,
    ToolMetricType.RESULT_INTEGRATION: JudgeReliability.LLM,
    ToolMetricType.UNNECESSARY_CALLS: JudgeReliability.HYBRID,
    ToolMetricType.MISSING_CALLS: JudgeReliability.LLM,
    ToolMetricType.RETRY_BEHAVIOR: JudgeReliability.DETERMINISTIC,
    ToolMetricType.TIMEOUT_HANDLING: JudgeReliability.DETERMINISTIC,
    ToolMetricType.PARALLEL_EXECUTION: JudgeReliability.DETERMINISTIC,
    ToolMetricType.FALLBACK_BEHAVIOR: JudgeReliability.LLM,
    ToolMetricType.PERMISSION_RESPECT: JudgeReliability.DETERMINISTIC,
    ToolMetricType.SIDE_EFFECT_AWARENESS: JudgeReliability.LLM,
}

# Speed mode eligibility — which reliability levels run at each speed
SPEED_MODE_INCLUDES: Dict[EvalSpeed, set[JudgeReliability]] = {
    EvalSpeed.DETERMINISTIC: {JudgeReliability.DETERMINISTIC},
    EvalSpeed.FAST: {JudgeReliability.DETERMINISTIC, JudgeReliability.HYBRID},
    EvalSpeed.STANDARD: {JudgeReliability.DETERMINISTIC, JudgeReliability.HYBRID, JudgeReliability.LLM},
    EvalSpeed.FULL: {JudgeReliability.DETERMINISTIC, JudgeReliability.HYBRID, JudgeReliability.LLM},
}


# ============================================================================
# Evidence Extraction Models
# ============================================================================

@dataclass
class RetrievedChunk:
    """A single document chunk retrieved by the RAG pipeline."""
    content: str
    source_id: str = ""
    source_name: str = ""
    relevance_score: float = 0.0
    chunk_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A single tool/function invocation."""
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    timestamp: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Citation:
    """A citation or attribution found in a bot response."""
    text: str                          # The cited text or claim
    source_reference: str = ""         # What source is referenced
    source_id: str = ""                # Matched source ID (if resolved)
    verified: Optional[bool] = None    # True if citation checks out
    confidence: float = 1.0            # Extraction confidence (1.0 for trace, <1.0 for inferred)


@dataclass
class ResponseAnnotation:
    """
    Complete annotation of a single bot response with RAG/tool evidence.
    This is the primary output of the ResponseAnnotator.
    """
    # Evidence mode used
    evidence_mode: EvidenceMode

    # Extraction confidence (1.0 for trace, 0.7-0.9 for structured, 0.3-0.7 for inferred)
    overall_confidence: float = 1.0

    # RAG evidence
    retrieved_chunks: List[RetrievedChunk] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    has_rag_evidence: bool = False

    # Tool evidence
    tool_calls: List[ToolCall] = field(default_factory=list)
    has_tool_evidence: bool = False

    # Raw bot response text
    response_text: str = ""

    # User question that prompted this response
    user_query: str = ""

    # Context document text (if available for grounding checks)
    context_document: str = ""

    # Metadata from extraction
    extraction_metadata: Dict[str, Any] = field(default_factory=dict)

    # Warnings/issues during extraction
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON export."""
        return {
            "evidence_mode": self.evidence_mode.value,
            "overall_confidence": round(self.overall_confidence, 3),
            "has_rag_evidence": self.has_rag_evidence,
            "has_tool_evidence": self.has_tool_evidence,
            "retrieved_chunks": [
                {
                    "content": c.content[:200],
                    "source_id": c.source_id,
                    "source_name": c.source_name,
                    "relevance_score": round(c.relevance_score, 3),
                }
                for c in self.retrieved_chunks
            ],
            "citations": [
                {
                    "text": c.text[:100],
                    "source_reference": c.source_reference,
                    "verified": c.verified,
                    "confidence": round(c.confidence, 3),
                }
                for c in self.citations
            ],
            "tool_calls": [
                {
                    "tool_name": t.tool_name,
                    "arguments": t.arguments,
                    "has_result": t.result is not None,
                    "has_error": t.error is not None,
                    "duration_ms": t.duration_ms,
                }
                for t in self.tool_calls
            ],
            "warnings": self.warnings,
        }


# ============================================================================
# Metric Results
# ============================================================================

@dataclass
class MetricResult:
    """Result from evaluating a single RAG or tool metric."""
    metric_name: str                    # e.g. "faithfulness", "tool_selection"
    metric_type: str                    # "rag" or "tool"
    score: float = 0.0                  # 0.0 - 1.0
    passed: bool = True
    threshold: float = 0.7
    reliability: str = "deterministic"  # deterministic / llm / hybrid
    confidence: float = 1.0            # How confident in this result
    evidence: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "metric_type": self.metric_type,
            "score": round(self.score, 3),
            "passed": self.passed,
            "threshold": self.threshold,
            "reliability": self.reliability,
            "confidence": round(self.confidence, 3),
            "message": self.message,
            "issues": self.issues,
        }


@dataclass
class TurnRAGResult:
    """RAG/Tool evaluation result for a single turn."""
    turn_index: int
    annotation: ResponseAnnotation
    rag_metrics: List[MetricResult] = field(default_factory=list)
    tool_metrics: List[MetricResult] = field(default_factory=list)
    rag_score: float = 0.0
    tool_score: float = 0.0
    overall_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "evidence_mode": self.annotation.evidence_mode.value,
            "rag_score": round(self.rag_score, 3),
            "tool_score": round(self.tool_score, 3),
            "overall_score": round(self.overall_score, 3),
            "rag_metrics": [m.to_dict() for m in self.rag_metrics],
            "tool_metrics": [m.to_dict() for m in self.tool_metrics],
        }


@dataclass
class ConversationRAGResult:
    """RAG/Tool evaluation for an entire conversation."""
    conversation_id: str
    turn_results: List[TurnRAGResult] = field(default_factory=list)
    avg_rag_score: float = 0.0
    avg_tool_score: float = 0.0
    avg_overall_score: float = 0.0
    evidence_mode: EvidenceMode = EvidenceMode.INFERRED
    total_rag_issues: int = 0
    total_tool_issues: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "evidence_mode": self.evidence_mode.value,
            "avg_rag_score": round(self.avg_rag_score, 3),
            "avg_tool_score": round(self.avg_tool_score, 3),
            "avg_overall_score": round(self.avg_overall_score, 3),
            "total_rag_issues": self.total_rag_issues,
            "total_tool_issues": self.total_tool_issues,
            "turns": [t.to_dict() for t in self.turn_results],
        }


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class RAGEvalConfig:
    """Configuration for the RAG/Tool evaluation framework."""
    # Evidence mode (auto-detected if not specified)
    evidence_mode: Optional[EvidenceMode] = None

    # Speed mode
    eval_speed: EvalSpeed = EvalSpeed.STANDARD

    # Thresholds (per-metric overrides possible)
    default_rag_threshold: float = 0.7
    default_tool_threshold: float = 0.7
    metric_thresholds: Dict[str, float] = field(default_factory=dict)

    # Which metrics to run (empty = all eligible for speed mode)
    enabled_rag_metrics: List[RAGMetricType] = field(default_factory=list)
    enabled_tool_metrics: List[ToolMetricType] = field(default_factory=list)

    # Sampling (for large conversation sets)
    sample_rate: float = 1.0  # 1.0 = evaluate all turns
    max_turns_per_conversation: Optional[int] = None

    # LLM config for inferred mode / LLM-based metrics
    llm_model: Optional[str] = None  # Override default LLM for RAG eval

    # Trace config
    trace_format: str = "auto"  # auto, otel, openinference

    # Tool sandbox config
    tool_definitions: List[Dict[str, Any]] = field(default_factory=list)

    # CI/CD gate
    fail_if_below: Optional[float] = None  # Exit code 1 if overall score < this

    def get_threshold(self, metric_name: str) -> float:
        """Get threshold for a specific metric, falling back to defaults."""
        if metric_name in self.metric_thresholds:
            return self.metric_thresholds[metric_name]
        # Determine if RAG or tool metric
        try:
            RAGMetricType(metric_name)
            return self.default_rag_threshold
        except ValueError:
            pass
        try:
            ToolMetricType(metric_name)
            return self.default_tool_threshold
        except ValueError:
            return 0.7

    def is_metric_eligible(self, metric_name: str) -> bool:
        """Check if a metric should run based on speed mode."""
        # Check RAG metrics
        try:
            mt = RAGMetricType(metric_name)
            reliability = RAG_METRIC_RELIABILITY.get(mt)
            if reliability is None:
                return False
            return reliability in SPEED_MODE_INCLUDES[self.eval_speed]
        except ValueError:
            pass
        # Check Tool metrics
        try:
            mt = ToolMetricType(metric_name)
            reliability = TOOL_METRIC_RELIABILITY.get(mt)
            if reliability is None:
                return False
            return reliability in SPEED_MODE_INCLUDES[self.eval_speed]
        except ValueError:
            return False
