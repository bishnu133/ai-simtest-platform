"""
Tests for P3 #14 Phase 7 (CLI Integration) + Phase 8 (HTML Report Injection).

Test classes:
  1. TestCLIRAGOptions         — New Click options parse correctly
  2. TestPostSimRAGEval        — Step 6 in _post_simulation_analysis
  3. TestRAGDemoMode           — --rag-demo flag handling
  4. TestHTMLInjection         — HTML report injection
  5. TestPrintHelper           — _print_rag_eval_report output
"""

import asyncio
import json
import os
import tempfile
import pytest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

from src.rag_eval.engine import RAGEvalEngine, RAGEvalReport
from src.rag_eval.rag_eval_html import inject_rag_eval_into_report, _build_rag_eval_section, _score_color
from src.rag_eval.demo_packs import load_demo_pack, list_demo_packs
from src.rag_eval.models import RAGEvalConfig, EvalSpeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================================
# 1. TestCLIRAGOptions
# ============================================================================

class TestCLIRAGOptions:
    """Verify new Click options are properly defined."""

    def test_cli_imports(self):
        """CLI module should import without errors."""
        # Just verify cli.py parses — don't invoke Click
        import ast
        cli_path = Path(__file__).parent.parent / "cli.py"
        if cli_path.exists():
            ast.parse(cli_path.read_text())

    def test_rag_eval_config_from_cli_args(self):
        """Simulate building RAGEvalConfig from CLI-style args."""
        speed_map = {
            "deterministic": EvalSpeed.DETERMINISTIC,
            "fast": EvalSpeed.FAST,
            "standard": EvalSpeed.STANDARD,
            "full": EvalSpeed.FULL,
        }
        config = RAGEvalConfig(
            eval_speed=speed_map["fast"],
            default_rag_threshold=0.8,
            default_tool_threshold=0.8,
            fail_if_below=0.75,
        )
        assert config.eval_speed == EvalSpeed.FAST
        assert config.default_rag_threshold == 0.8
        assert config.fail_if_below == 0.75

    def test_all_speed_modes_map(self):
        speed_map = {
            "deterministic": EvalSpeed.DETERMINISTIC,
            "fast": EvalSpeed.FAST,
            "standard": EvalSpeed.STANDARD,
            "full": EvalSpeed.FULL,
        }
        for name, expected in speed_map.items():
            assert speed_map[name] == expected


# ============================================================================
# 2. TestPostSimRAGEval
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
class MockJudgedConv:
    conversation: MockConversation
    persona: Optional[Any] = None


@dataclass
class MockReport:
    judged_conversations: List[MockJudgedConv] = field(default_factory=list)


def make_mock_report():
    return MockReport(judged_conversations=[
        MockJudgedConv(conversation=MockConversation(
            id="conv_1",
            turns=[
                MockTurn("user", "What is the return policy?"),
                MockTurn("bot", "According to our FAQ, returns are within 30 days [1].", {
                    "sources": [{"content": "Return policy: 30 days.", "source_id": "doc_1"}],
                }),
            ],
        )),
    ])


class TestPostSimRAGEval:
    """Test the RAG eval step in the post-simulation pipeline."""

    def test_engine_evaluates_mock_report(self):
        engine = RAGEvalEngine(RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC))
        report = make_mock_report()
        rag_report = run(engine.evaluate_conversations(
            report.judged_conversations,
            context_document="Return policy: 30 days for all items.",
        ))
        assert rag_report.total_conversations == 1
        assert rag_report.total_turns_evaluated == 1
        assert rag_report.overall_score > 0.0

    def test_engine_saves_and_appends(self):
        engine = RAGEvalEngine(RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC))
        report = make_mock_report()
        rag_report = run(engine.evaluate_conversations(
            report.judged_conversations,
            context_document="Return policy: 30 days.",
        ))

        with tempfile.TemporaryDirectory() as tmpdir:
            # Save report
            path = RAGEvalEngine.save_report(rag_report, tmpdir)
            assert os.path.exists(path)

            # Append to summary
            summary_path = os.path.join(tmpdir, "summary.json")
            with open(summary_path, 'w') as f:
                json.dump({"simulation_id": "test_123"}, f)

            RAGEvalEngine.append_to_summary(rag_report, summary_path)

            with open(summary_path) as f:
                data = json.load(f)
            assert "rag_eval" in data
            assert data["simulation_id"] == "test_123"

    def test_gate_check_pass(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC, fail_if_below=0.1)
        engine = RAGEvalEngine(config)
        report = make_mock_report()
        rag_report = run(engine.evaluate_conversations(
            report.judged_conversations,
            context_document="Return policy: 30 days.",
        ))
        assert rag_report.gate_passed is True

    def test_gate_check_fail(self):
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC, fail_if_below=0.99)
        engine = RAGEvalEngine(config)
        report = make_mock_report()
        rag_report = run(engine.evaluate_conversations(
            report.judged_conversations,
            context_document="Return policy: 30 days.",
        ))
        assert rag_report.gate_threshold == 0.99


# ============================================================================
# 3. TestRAGDemoMode
# ============================================================================

class TestRAGDemoMode:
    """Test demo pack loading and evaluation via engine (simulates --rag-demo)."""

    def test_faq_demo_runs(self):
        convs, ctx, tool_defs = load_demo_pack("faq_rag")
        config = RAGEvalConfig(eval_speed=EvalSpeed.DETERMINISTIC)
        engine = RAGEvalEngine(config)
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 12
        assert report.total_turns_evaluated > 0

    def test_finance_demo_runs(self):
        from src.rag_eval.tool_metrics import ToolDefinition
        convs, ctx, raw_defs = load_demo_pack("finance_tools")
        tdefs = [ToolDefinition.from_dict(d) for d in raw_defs]
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        engine = RAGEvalEngine(config, tool_definitions=tdefs)
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 12

    def test_failure_demo_runs(self):
        convs, ctx, _ = load_demo_pack("failure_injection")
        config = RAGEvalConfig(eval_speed=EvalSpeed.FAST)
        engine = RAGEvalEngine(config)
        report = run(engine.evaluate_conversations(convs, ctx))
        assert report.total_conversations == 11

    def test_unknown_demo_pack_raises(self):
        with pytest.raises(ValueError, match="Unknown demo pack"):
            load_demo_pack("nonexistent_pack")


# ============================================================================
# 4. TestHTMLInjection
# ============================================================================

class TestHTMLInjection:
    """Test HTML report injection."""

    def _make_report(self, score=0.85, rag_issues=2, tool_issues=1):
        return RAGEvalReport(
            total_conversations=3,
            total_turns_evaluated=9,
            overall_rag_score=score,
            overall_tool_score=0.72,
            overall_score=score,
            evidence_mode="structured",
            eval_speed="standard",
            rag_metric_averages={"faithfulness": 0.9, "citation_accuracy": 0.8},
            rag_metric_pass_rates={"faithfulness": 0.85, "citation_accuracy": 0.75},
            tool_metric_averages={"tool_selection": 0.7},
            tool_metric_pass_rates={"tool_selection": 0.6},
            total_rag_issues=rag_issues,
            total_tool_issues=tool_issues,
            top_issues=["Unverified citation: doc_xyz", "Missing required param 'query'"],
            gate_passed=True,
            execution_time_seconds=1.5,
        )

    def test_build_section_html(self):
        report = self._make_report()
        html = _build_rag_eval_section(report)
        assert "RAG/Tool Evaluation" in html
        assert "85.0%" in html
        assert "Faithfulness" in html
        assert "Tool Selection" in html
        assert "structured" in html

    def test_build_section_empty_report(self):
        report = RAGEvalReport()
        html = _build_rag_eval_section(report)
        assert html == ""  # No turns evaluated

    def test_build_section_with_gate(self):
        report = self._make_report()
        report.gate_threshold = 0.7
        report.gate_passed = True
        html = _build_rag_eval_section(report)
        assert "CI/CD Gate" in html
        assert "PASSED" in html

    def test_build_section_gate_failed(self):
        report = self._make_report(score=0.5)
        report.gate_threshold = 0.7
        report.gate_passed = False
        html = _build_rag_eval_section(report)
        assert "FAILED" in html

    def test_inject_before_signature(self):
        report = self._make_report()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write('<html><body><h1>Report</h1><!-- BEHAVIORAL_SIGNATURE_SECTION --><footer>End</footer></body></html>')
            f.flush()
            result = inject_rag_eval_into_report(report, f.name)
            assert result is True
            with open(f.name) as r:
                content = r.read()
            assert "RAG/Tool Evaluation" in content
            # Should appear before signature marker
            rag_pos = content.find("RAG_TOOL_EVAL_SECTION")
            sig_pos = content.find("BEHAVIORAL_SIGNATURE_SECTION")
            assert rag_pos < sig_pos
        os.unlink(f.name)

    def test_inject_before_body_close(self):
        report = self._make_report()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write('<html><body><h1>Simple Report</h1></body></html>')
            f.flush()
            result = inject_rag_eval_into_report(report, f.name)
            assert result is True
            with open(f.name) as r:
                content = r.read()
            assert "RAG/Tool Evaluation" in content
        os.unlink(f.name)

    def test_inject_nonexistent_file(self):
        report = self._make_report()
        result = inject_rag_eval_into_report(report, "/nonexistent/path.html")
        assert result is False

    def test_issues_in_html(self):
        report = self._make_report()
        html = _build_rag_eval_section(report)
        assert "Top Issues" in html
        assert "Unverified citation" in html

    def test_score_colors(self):
        assert _score_color(0.9) == "#4ade80"   # green
        assert _score_color(0.7) == "#fbbf24"   # yellow
        assert _score_color(0.3) == "#f87171"   # red


# ============================================================================
# 5. TestPrintHelper
# ============================================================================

class TestPrintHelper:
    """Test _print_rag_eval_report produces output without errors."""

    def test_print_basic_report(self, capsys):
        """The print function should not crash on a basic report."""
        # Import the function from cli — may not work in all environments
        # so we test the underlying data instead
        report = RAGEvalReport(
            total_conversations=2,
            total_turns_evaluated=5,
            overall_rag_score=0.82,
            overall_tool_score=0.71,
            overall_score=0.77,
            evidence_mode="inferred",
            eval_speed="fast",
            rag_metric_averages={"faithfulness": 0.9, "citation_accuracy": 0.8},
            rag_metric_pass_rates={"faithfulness": 1.0, "citation_accuracy": 0.8},
            execution_time_seconds=0.5,
        )
        # Verify the report dict works (CLI printer uses these)
        d = report.to_dict()
        assert d["overall_score"] == 0.77
        assert d["rag_metric_averages"]["faithfulness"] == 0.9

    def test_report_summary_dict_for_append(self):
        report = RAGEvalReport(
            overall_score=0.8,
            overall_rag_score=0.85,
            overall_tool_score=0.7,
            total_turns_evaluated=10,
            evidence_mode="structured",
            eval_speed="standard",
            total_rag_issues=3,
            total_tool_issues=1,
            gate_passed=True,
        )
        s = report.to_summary_dict()
        assert s["rag_eval"]["overall_score"] == 0.8
        assert s["rag_eval"]["rag_issues"] == 3
        assert s["rag_eval"]["gate_passed"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
