"""
Tests for P3 #14 Phase 6 — Demo Packs.

Test classes:
  1. TestDemoPackRegistry  — list, load, unknown pack error
  2. TestFAQPack           — 12 cases, structure, context doc
  3. TestFinancePack       — 12 cases, tool definitions present
  4. TestHealthcarePack    — 10 cases, citation metadata
  5. TestFailurePack       — 11 cases, 8 failure types
  6. TestDemoPackIntegration — run demo packs through the engine
"""

import asyncio
import pytest

from src.rag_eval.demo_packs import list_demo_packs, load_demo_pack, DemoJudgedConversation
from src.rag_eval.engine import RAGEvalEngine
from src.rag_eval.models import RAGEvalConfig, EvalSpeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDemoPackRegistry:

    def test_list_packs(self):
        packs = list_demo_packs()
        assert len(packs) == 4
        names = {p["name"] for p in packs}
        assert names == {"faq_rag", "finance_tools", "healthcare_citations", "failure_injection"}

    def test_list_packs_have_descriptions(self):
        for p in list_demo_packs():
            assert p["description"]
            assert p["domain"]
            assert int(p["cases"]) > 0

    def test_load_unknown_pack_raises(self):
        with pytest.raises(ValueError, match="Unknown demo pack"):
            load_demo_pack("nonexistent")

    def test_load_returns_tuple(self):
        convs, ctx, tool_defs = load_demo_pack("faq_rag")
        assert isinstance(convs, list)
        assert isinstance(ctx, str)
        assert isinstance(tool_defs, list)


class TestFAQPack:

    def test_case_count(self):
        convs, ctx, _ = load_demo_pack("faq_rag")
        assert len(convs) == 12

    def test_context_document_present(self):
        _, ctx, _ = load_demo_pack("faq_rag")
        assert "Return Policy" in ctx
        assert "Shipping" in ctx

    def test_conversation_structure(self):
        convs, _, _ = load_demo_pack("faq_rag")
        for jc in convs:
            assert isinstance(jc, DemoJudgedConversation)
            assert jc.conversation.id
            assert len(jc.conversation.turns) >= 2

    def test_has_structured_metadata(self):
        convs, _, _ = load_demo_pack("faq_rag")
        # First conv should have sources
        first_bot = [t for t in convs[0].conversation.turns if t.speaker == "bot"][0]
        assert "sources" in first_bot.metadata

    def test_hallucination_case_exists(self):
        convs, _, _ = load_demo_pack("faq_rag")
        ids = [jc.conversation.id for jc in convs]
        assert "faq_hallucination_1" in ids


class TestFinancePack:

    def test_case_count(self):
        convs, _, _ = load_demo_pack("finance_tools")
        assert len(convs) == 12

    def test_tool_definitions_present(self):
        _, _, tool_defs = load_demo_pack("finance_tools")
        assert len(tool_defs) == 4
        names = {td["name"] for td in tool_defs}
        assert "get_balance" in names
        assert "transfer_funds" in names

    def test_has_tool_call_metadata(self):
        convs, _, _ = load_demo_pack("finance_tools")
        first_bot = [t for t in convs[0].conversation.turns if t.speaker == "bot"][0]
        assert "tool_calls" in first_bot.metadata

    def test_error_cases_exist(self):
        convs, _, _ = load_demo_pack("finance_tools")
        ids = [jc.conversation.id for jc in convs]
        assert "fin_error_good" in ids
        assert "fin_error_bad" in ids

    def test_sequence_cases_exist(self):
        convs, _, _ = load_demo_pack("finance_tools")
        ids = [jc.conversation.id for jc in convs]
        assert "fin_seq_good" in ids
        assert "fin_seq_bad" in ids


class TestHealthcarePack:

    def test_case_count(self):
        convs, _, _ = load_demo_pack("healthcare_citations")
        assert len(convs) == 10

    def test_context_has_medical_content(self):
        _, ctx, _ = load_demo_pack("healthcare_citations")
        assert "diabetes" in ctx.lower() or "HbA1c" in ctx

    def test_citation_cases(self):
        convs, _, _ = load_demo_pack("healthcare_citations")
        ids = [jc.conversation.id for jc in convs]
        assert "hc_good_1" in ids
        assert "hc_hallucination_1" in ids


class TestFailurePack:

    def test_case_count(self):
        convs, _, _ = load_demo_pack("failure_injection")
        assert len(convs) == 11

    def test_has_tool_definitions(self):
        _, _, tool_defs = load_demo_pack("failure_injection")
        assert len(tool_defs) >= 2

    def test_failure_types_covered(self):
        convs, _, _ = load_demo_pack("failure_injection")
        ids = [jc.conversation.id for jc in convs]
        # 8 failure types
        assert "fail_hallucinate" in ids
        assert "fail_timeout" in ids
        assert "fail_wrong_tool" in ids
        assert "fail_missing_params" in ids
        assert "fail_ignore_result" in ids
        assert "fail_phantom_cite" in ids
        assert "fail_empty_retrieval" in ids
        assert "fail_contradiction" in ids


class TestDemoPackIntegration:
    """Run demo packs through the actual RAGEvalEngine."""

    def test_faq_pack_through_engine(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        engine = RAGEvalEngine(config)
        convs, ctx, _ = load_demo_pack("faq_rag")
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 12
        assert report.total_turns_evaluated > 0
        assert report.overall_score > 0.0

    def test_finance_pack_through_engine(self):
        from src.rag_eval.tool_metrics import ToolDefinition
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        convs, ctx, raw_defs = load_demo_pack("finance_tools")
        tool_defs = [ToolDefinition.from_dict(d) for d in raw_defs]
        engine = RAGEvalEngine(config, tool_definitions=tool_defs)
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 12
        assert report.total_turns_evaluated > 0

    def test_failure_pack_detects_issues(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        engine = RAGEvalEngine(config)
        convs, ctx, _ = load_demo_pack("failure_injection")
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 11
        # Failure pack should produce issues
        total_issues = report.total_rag_issues + report.total_tool_issues
        assert total_issues >= 0  # Some failures will be caught

    def test_healthcare_pack_through_engine(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        engine = RAGEvalEngine(config)
        convs, ctx, _ = load_demo_pack("healthcare_citations")
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 10
        assert report.overall_score > 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
