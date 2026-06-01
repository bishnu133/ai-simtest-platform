"""
Tests for RAG/Tool Evaluation Models — Phase 1.

Covers: Pydantic model validation, serialization, evidence mode enums,
threshold resolution (mode-aware), tool schema queries, sandbox config,
aggregation models, and edge cases.
"""

import pytest
from ai_simtest_engine.rag_eval.models import (
    # Enums
    AggregationLevel,
    ConfidenceLevel,
    EvalSpeedMode,
    EvidenceMode,
    JudgeType,
    SandboxInjectionType,
    SequenceMode,
    ToolRequirementType,
    ToolSideEffect,
    # Core data
    AnnotatedResponse,
    Citation,
    ConversationRagSummary,
    ConversationToolSummary,
    ExtractedClaim,
    MetricResult,
    RagContext,
    RagEvalResult,
    RetrievedChunk,
    ToolCall,
    ToolCallContext,
    ToolCallParam,
    ToolEvalResult,
    # Schemas
    ToolSchema,
    ToolSchemaSet,
    ToolParamSchema,
    ToolPolicyConfig,
    ToolSequenceConfig,
    SequenceConstraint,
    # Sandbox
    SandboxConfig,
    SandboxInjection,
    ToolSandboxConfig,
    # Config
    RagEvalConfig,
    RagEvalWeights,
    ToolEvalWeights,
    JudgeModelConfig,
    MODE_THRESHOLDS,
)


# ============================================================
# Test Enums
# ============================================================

class TestEnums:
    """Test all enum definitions and values."""

    def test_evidence_mode_values(self):
        assert EvidenceMode.TRACE == "trace"
        assert EvidenceMode.STRUCTURED == "structured"
        assert EvidenceMode.INFERRED == "inferred"

    def test_confidence_level_values(self):
        assert ConfidenceLevel.HIGH == "high"
        assert ConfidenceLevel.MEDIUM == "medium"
        assert ConfidenceLevel.LOW == "low"

    def test_eval_speed_mode_values(self):
        modes = [EvalSpeedMode.DETERMINISTIC, EvalSpeedMode.FAST,
                 EvalSpeedMode.STANDARD, EvalSpeedMode.FULL]
        assert len(modes) == 4

    def test_tool_side_effect_values(self):
        assert ToolSideEffect.READ_ONLY == "read_only"
        assert ToolSideEffect.IRREVERSIBLE == "irreversible"

    def test_sandbox_injection_types(self):
        types = list(SandboxInjectionType)
        assert len(types) == 8
        assert SandboxInjectionType.TIMEOUT in types
        assert SandboxInjectionType.AUTH_FAILURE in types

    def test_sequence_mode_values(self):
        assert SequenceMode.EXACT == "exact"
        assert SequenceMode.PARTIAL_ORDER == "partial_order"
        assert SequenceMode.CONSTRAINTS == "constraints"


# ============================================================
# Test RAG Context Models
# ============================================================

class TestRagContextModels:
    """Test RAG data structures."""

    def test_retrieved_chunk_defaults(self):
        chunk = RetrievedChunk(content="Test content")
        assert chunk.chunk_id.startswith("chunk_")
        assert chunk.content == "Test content"
        assert chunk.source == ""
        assert chunk.relevance_score is None

    def test_retrieved_chunk_with_metadata(self):
        chunk = RetrievedChunk(
            content="Drug interactions",
            source="Pharmacy Guide",
            title="Chapter 5",
            relevance_score=0.92,
            metadata={"page": 42}
        )
        assert chunk.relevance_score == 0.92
        assert chunk.metadata["page"] == 42

    def test_citation_defaults(self):
        cite = Citation(raw_reference="[1]")
        assert cite.citation_id.startswith("cite_")
        assert cite.raw_reference == "[1]"
        assert cite.exists_in_context is None
        assert cite.supports_claim is None

    def test_extracted_claim(self):
        claim = ExtractedClaim(
            text="Aspirin may cause stomach bleeding",
            supported=True,
            supporting_chunk_ids=["chunk_abc"],
            confidence=0.95
        )
        assert claim.supported is True
        assert len(claim.supporting_chunk_ids) == 1

    def test_rag_context_empty(self):
        ctx = RagContext()
        assert ctx.evidence_mode == EvidenceMode.INFERRED
        assert ctx.has_retrieval is False
        assert len(ctx.retrieved_chunks) == 0

    def test_rag_context_with_data(self):
        ctx = RagContext(
            evidence_mode=EvidenceMode.TRACE,
            retrieved_chunks=[RetrievedChunk(content="test")],
            citations=[Citation(raw_reference="[1]")],
            has_retrieval=True
        )
        assert ctx.has_retrieval is True
        assert len(ctx.retrieved_chunks) == 1
        assert len(ctx.citations) == 1


# ============================================================
# Test Tool Call Models
# ============================================================

class TestToolCallModels:
    """Test tool call data structures."""

    def test_tool_call_param(self):
        param = ToolCallParam(name="query", value="refund policy", expected_type="string")
        assert param.name == "query"
        assert param.valid is None

    def test_tool_call_defaults(self):
        call = ToolCall(tool_name="search_kb")
        assert call.call_id.startswith("tcall_")
        assert call.tool_name == "search_kb"
        assert call.result is None
        assert call.is_retry is False

    def test_tool_call_with_params(self):
        call = ToolCall(
            tool_name="create_ticket",
            parameters=[
                ToolCallParam(name="title", value="Refund request"),
                ToolCallParam(name="priority", value="high"),
            ],
            raw_arguments={"title": "Refund request", "priority": "high"}
        )
        assert len(call.parameters) == 2
        assert call.raw_arguments["priority"] == "high"

    def test_tool_call_context_empty(self):
        ctx = ToolCallContext()
        assert ctx.has_tool_usage is False
        assert len(ctx.tool_calls) == 0

    def test_tool_call_context_with_calls(self):
        ctx = ToolCallContext(
            evidence_mode=EvidenceMode.STRUCTURED,
            tool_calls=[ToolCall(tool_name="search"), ToolCall(tool_name="create")],
            has_tool_usage=True
        )
        assert ctx.has_tool_usage is True
        assert len(ctx.tool_calls) == 2


# ============================================================
# Test Annotated Response
# ============================================================

class TestAnnotatedResponse:
    """Test the annotated response bridge model."""

    def test_defaults(self):
        ar = AnnotatedResponse(response_text="Hello world")
        assert ar.response_text == "Hello world"
        assert ar.evidence_mode == EvidenceMode.INFERRED
        assert ar.confidence == ConfidenceLevel.LOW

    def test_auto_confidence_trace(self):
        ar = AnnotatedResponse(
            response_text="Test",
            evidence_mode=EvidenceMode.TRACE
        )
        assert ar.confidence == ConfidenceLevel.HIGH

    def test_auto_confidence_structured(self):
        ar = AnnotatedResponse(
            response_text="Test",
            evidence_mode=EvidenceMode.STRUCTURED
        )
        assert ar.confidence == ConfidenceLevel.HIGH

    def test_auto_confidence_inferred(self):
        ar = AnnotatedResponse(
            response_text="Test",
            evidence_mode=EvidenceMode.INFERRED
        )
        assert ar.confidence == ConfidenceLevel.LOW

    def test_with_rag_and_tool_context(self):
        ar = AnnotatedResponse(
            response_text="Based on our FAQ [1], your refund is being processed.",
            evidence_mode=EvidenceMode.STRUCTURED,
            rag_context=RagContext(
                evidence_mode=EvidenceMode.STRUCTURED,
                citations=[Citation(raw_reference="[1]")],
                has_retrieval=True,
            ),
            tool_context=ToolCallContext(
                evidence_mode=EvidenceMode.STRUCTURED,
                tool_calls=[ToolCall(tool_name="check_refund_status")],
                has_tool_usage=True,
            ),
        )
        assert ar.rag_context.has_retrieval is True
        assert ar.tool_context.has_tool_usage is True
        assert ar.confidence == ConfidenceLevel.HIGH


# ============================================================
# Test Tool Schema
# ============================================================

class TestToolSchema:
    """Test tool schema definitions."""

    def test_tool_param_schema(self):
        p = ToolParamSchema(name="query", type="string", required=True, max_length=200)
        assert p.required is True
        assert p.max_length == 200

    def test_tool_schema_required_params(self):
        schema = ToolSchema(
            name="search",
            parameters=[
                ToolParamSchema(name="query", required=True),
                ToolParamSchema(name="top_k", required=False, default=5),
            ]
        )
        assert schema.get_required_params() == ["query"]

    def test_tool_policy_config(self):
        policy = ToolPolicyConfig(
            required_by_policy=True,
            confirmation_required=True,
            policy_reference="finance_v1"
        )
        assert policy.required_by_policy is True
        assert policy.confirmation_required is True

    def test_tool_schema_set_lookup(self):
        schema_set = ToolSchemaSet(tools=[
            ToolSchema(name="search_kb", required_for=["knowledge questions"]),
            ToolSchema(name="create_ticket", required_for=["escalation"]),
        ])
        assert schema_set.get_tool("search_kb") is not None
        assert schema_set.get_tool("nonexistent") is None

    def test_tool_schema_set_intent_matching(self):
        schema_set = ToolSchemaSet(tools=[
            ToolSchema(name="search_kb", required_for=["knowledge questions", "policy inquiries"]),
            ToolSchema(name="create_ticket", required_for=["escalation requests"]),
        ])
        found = schema_set.get_required_tools_for_intent("I have a knowledge question")
        assert "search_kb" in found

    def test_tool_schema_set_forbidden_tools(self):
        schema_set = ToolSchemaSet(tools=[
            ToolSchema(name="safe_tool"),
            ToolSchema(name="dangerous_tool", policy=ToolPolicyConfig(forbidden_by_policy=True)),
        ])
        forbidden = schema_set.get_forbidden_tools()
        assert forbidden == ["dangerous_tool"]

    def test_tool_schema_set_policy_required(self):
        schema_set = ToolSchemaSet(tools=[
            ToolSchema(name="balance_check", policy=ToolPolicyConfig(required_by_policy=True)),
            ToolSchema(name="search"),
        ])
        required = schema_set.get_policy_required_tools()
        assert required == ["balance_check"]

    def test_sequence_config_exact(self):
        seq = ToolSequenceConfig(
            name="refund_flow",
            mode=SequenceMode.EXACT,
            sequence=["check_order", "verify", "process_refund"]
        )
        assert len(seq.sequence) == 3

    def test_sequence_config_partial_order(self):
        seq = ToolSequenceConfig(
            name="escalation",
            mode=SequenceMode.PARTIAL_ORDER,
            required_tools=["search_kb", "create_ticket"],
            constraints=[SequenceConstraint(tool="search_kb", relation="before", target="create_ticket")],
            allowed_alternatives={"search_kb": ["search_faq", "search_docs"]},
            max_calls=5
        )
        assert seq.mode == SequenceMode.PARTIAL_ORDER
        assert len(seq.constraints) == 1
        assert "search_kb" in seq.allowed_alternatives


# ============================================================
# Test Sandbox Config
# ============================================================

class TestSandboxConfig:
    """Test tool sandbox configuration."""

    def test_sandbox_disabled_by_default(self):
        sb = SandboxConfig()
        assert sb.enabled is False

    def test_sandbox_injection(self):
        inj = SandboxInjection(
            type=SandboxInjectionType.TIMEOUT,
            probability=0.1,
            delay_seconds=35
        )
        assert inj.probability == 0.1
        assert inj.delay_seconds == 35

    def test_sandbox_per_tool(self):
        sb = SandboxConfig(
            enabled=True,
            tools=[
                ToolSandboxConfig(
                    tool_name="search_kb",
                    injections=[
                        SandboxInjection(type=SandboxInjectionType.HTTP_500, probability=0.05),
                        SandboxInjection(type=SandboxInjectionType.TIMEOUT, probability=0.1),
                    ]
                )
            ]
        )
        tc = sb.get_tool_config("search_kb")
        assert tc is not None
        assert len(tc.injections) == 2
        assert sb.get_tool_config("unknown") is None


# ============================================================
# Test Evaluation Results
# ============================================================

class TestEvalResults:
    """Test evaluation result models."""

    def test_metric_result_defaults(self):
        mr = MetricResult(metric_name="faithfulness", score=0.85, passed=True, threshold=0.70)
        assert mr.passed is True
        assert mr.skipped is False

    def test_metric_result_skipped(self):
        mr = MetricResult(
            metric_name="retrieval_relevance",
            skipped=True,
            skip_reason="Not evaluable in inferred mode"
        )
        assert mr.skipped is True

    def test_rag_eval_result(self):
        result = RagEvalResult(
            turn_id="turn_1",
            evidence_mode=EvidenceMode.STRUCTURED,
            confidence=ConfidenceLevel.HIGH,
            metrics=[
                MetricResult(metric_name="faithfulness", score=0.9, passed=True, threshold=0.7),
                MetricResult(metric_name="citation_support", score=0.8, passed=True, threshold=0.75),
            ],
            overall_score=0.85,
            passed=True,
            claims_extracted=5,
            claims_supported=4,
            citations_found=3,
            citations_valid=3,
        )
        assert result.get_metric("faithfulness").score == 0.9
        assert result.get_metric("nonexistent") is None
        summary = result.to_summary_dict()
        assert summary["claims"] == "4/5"
        assert summary["citations"] == "3/3"

    def test_tool_eval_result(self):
        result = ToolEvalResult(
            turn_id="turn_2",
            evidence_mode=EvidenceMode.TRACE,
            metrics=[
                MetricResult(metric_name="tool_selection", score=1.0, passed=True, threshold=0.8),
                MetricResult(metric_name="argument_correctness", score=0.9, passed=True, threshold=0.85),
            ],
            overall_score=0.95,
            tool_calls_found=2,
            tool_calls_valid=2,
            sequence_correct=True,
        )
        summary = result.to_summary_dict()
        assert summary["tool_calls"] == "2/2"
        assert summary["sequence_correct"] is True

    def test_conversation_rag_summary(self):
        summary = ConversationRagSummary(
            conversation_id="conv_1",
            avg_faithfulness=0.85,
            total_claims=10,
            total_supported=8,
            overall_score=0.82,
            passed=True,
        )
        d = summary.to_summary_dict()
        assert d["claims"] == "8/10"
        assert d["passed"] is True

    def test_conversation_tool_summary(self):
        summary = ConversationToolSummary(
            conversation_id="conv_2",
            total_tool_calls=5,
            total_valid_calls=4,
            sequence_violations=1,
            required_tools_missed=["balance_check"],
            overall_score=0.75,
            passed=False,
        )
        d = summary.to_summary_dict()
        assert d["tool_calls"] == "4/5"
        assert "balance_check" in d["required_missed"]


# ============================================================
# Test Configuration
# ============================================================

class TestRagEvalConfig:
    """Test configuration and threshold resolution."""

    def test_defaults(self):
        config = RagEvalConfig()
        assert config.enabled is True
        assert config.template == "general"
        assert config.speed_mode == EvalSpeedMode.STANDARD
        assert config.sample_rate == 1.0

    def test_threshold_from_template_trace(self):
        config = RagEvalConfig(template="healthcare")
        threshold = config.get_threshold("faithfulness", EvidenceMode.TRACE)
        assert threshold == 0.90

    def test_threshold_from_template_inferred(self):
        config = RagEvalConfig(template="healthcare")
        threshold = config.get_threshold("faithfulness", EvidenceMode.INFERRED)
        assert threshold == 0.60  # relaxed for inferred

    def test_threshold_not_evaluable_inferred(self):
        config = RagEvalConfig(template="general")
        threshold = config.get_threshold("retrieval_relevance", EvidenceMode.INFERRED)
        assert threshold is None  # not evaluable

    def test_threshold_explicit_override(self):
        config = RagEvalConfig(
            template="general",
            thresholds={"faithfulness": 0.99}
        )
        threshold = config.get_threshold("faithfulness", EvidenceMode.TRACE)
        assert threshold == 0.99  # explicit takes priority

    def test_is_metric_evaluable(self):
        config = RagEvalConfig(template="general")
        assert config.is_metric_evaluable("faithfulness", EvidenceMode.TRACE) is True
        assert config.is_metric_evaluable("retrieval_relevance", EvidenceMode.INFERRED) is False
        assert config.is_metric_evaluable("argument_correctness", EvidenceMode.INFERRED) is False
        assert config.is_metric_evaluable("argument_correctness", EvidenceMode.STRUCTURED) is True

    def test_all_templates_exist(self):
        for template in ["general", "healthcare", "finance", "legal"]:
            assert template in MODE_THRESHOLDS

    def test_mode_thresholds_healthcare_stricter(self):
        gen = MODE_THRESHOLDS["general"]["faithfulness"]["trace"]
        hc = MODE_THRESHOLDS["healthcare"]["faithfulness"]["trace"]
        assert hc > gen  # healthcare is stricter

    def test_judge_model_config(self):
        jmc = JudgeModelConfig(
            faithfulness="gpt-4o-mini",
            retrieval_relevance="gpt-4o",
            default="gpt-4o-mini"
        )
        assert jmc.get_model_for("faithfulness") == "gpt-4o-mini"
        assert jmc.get_model_for("retrieval_relevance") == "gpt-4o"
        assert jmc.get_model_for("unknown_metric") == "gpt-4o-mini"  # falls back to default

    def test_config_serialization(self):
        config = RagEvalConfig(template="finance", citation_strict=True)
        d = config.to_dict()
        assert d["template"] == "finance"
        assert d["citation_strict"] is True
        assert isinstance(d, dict)

    def test_weights_sum(self):
        """Weights should be configurable but documented defaults should sum to ~1.0."""
        rw = RagEvalWeights()
        total = sum(rw.to_dict().values())
        assert abs(total - 1.0) < 0.01

        tw = ToolEvalWeights()
        total = sum(tw.to_dict().values())
        assert abs(total - 1.0) < 0.01

    def test_deterministic_only_mode(self):
        config = RagEvalConfig(deterministic_only=True)
        assert config.deterministic_only is True
        assert config.speed_mode == EvalSpeedMode.STANDARD  # independent settings

    def test_config_with_sandbox(self):
        config = RagEvalConfig(
            sandbox=SandboxConfig(
                enabled=True,
                tools=[ToolSandboxConfig(
                    tool_name="search",
                    injections=[SandboxInjection(type=SandboxInjectionType.TIMEOUT, probability=0.1)]
                )]
            )
        )
        assert config.sandbox.enabled is True
        assert config.sandbox.get_tool_config("search") is not None


# ============================================================
# Test Edge Cases
# ============================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_tool_schema_set(self):
        ss = ToolSchemaSet()
        assert ss.get_tool("anything") is None
        assert ss.get_required_tools_for_intent("anything") == []
        assert ss.get_forbidden_tools() == []
        assert ss.get_policy_required_tools() == []

    def test_metric_result_score_boundaries(self):
        """Score boundaries: MetricResult.score is a plain float (no Pydantic constraints)
        but JudgmentResult.score has ge=0.0, le=1.0 from the core models."""
        # Valid scores
        m1 = MetricResult(metric_name="test", score=0.0)
        assert m1.score == 0.0
        m2 = MetricResult(metric_name="test", score=1.0)
        assert m2.score == 1.0
        # MetricResult doesn't enforce boundaries (intentional — some metrics
        # like unsupported_claim_rate may have inverted scales)
        m3 = MetricResult(metric_name="test", score=1.5)
        assert m3.score == 1.5

    def test_rag_eval_result_empty_metrics(self):
        result = RagEvalResult()
        assert result.get_metric("anything") is None
        summary = result.to_summary_dict()
        assert summary["claims"] == "0/0"

    def test_annotated_response_minimal(self):
        ar = AnnotatedResponse(response_text="")
        assert ar.response_text == ""
        assert ar.evidence_mode == EvidenceMode.INFERRED

    def test_unknown_template_falls_back(self):
        config = RagEvalConfig(template="nonexistent")
        threshold = config.get_threshold("faithfulness", EvidenceMode.TRACE)
        # Falls back to general template
        assert threshold is not None

    def test_tool_schema_set_case_insensitive_intent(self):
        ss = ToolSchemaSet(tools=[
            ToolSchema(name="search", required_for=["Knowledge Questions"]),
        ])
        found = ss.get_required_tools_for_intent("knowledge questions about refunds")
        assert "search" in found
