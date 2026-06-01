"""
Tests for Phase D: Autonomous Orchestrator (Partial Autonomous Mode).
Run with: PYTHONPATH=. pytest tests/test_autonomous.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.approval_gate import (
    GateDecision,
    GateManager,
    GateProposal,
    GateResult,
    ProgrammaticApprovalGate,
)
from src.core.autonomous_orchestrator import (
    AnalysisPipelineResult,
    AutonomousOrchestrator,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_docs(tmp_path):
    """Create a temp directory with sample documentation."""
    (tmp_path / "readme.md").write_text(
        "# ShopBot Documentation\n\n"
        "## Purpose\nHelp customers with orders, refunds, and product info.\n\n"
        "## Capabilities\n- Order tracking\n- Refund processing\n- Product search\n\n"
        "## Limitations\n- Cannot process payments directly\n- No inventory access\n"
    )
    (tmp_path / "policies.txt").write_text(
        "Refund Policy: 30-day window for all returns.\n"
        "Escalation: Transfer to human agent for complaints.\n"
        "Privacy: Never share customer PII.\n"
    )
    return tmp_path


@pytest.fixture
def mock_llm():
    """Create a mock LLM that returns appropriate responses per stage."""
    llm = AsyncMock()
    llm.model = "test-model"
    llm.is_local = False

    async def smart_generate(prompt, system_prompt=None):
        sp = (system_prompt or "").lower()
        if "technical analyst" in sp:
            # Document analyzer
            return {
                "bot_name": "ShopBot",
                "domain": "E-commerce",
                "purpose": "Help customers with orders and refunds",
                "capabilities": ["order tracking", "refund processing"],
                "limitations": ["cannot process payments"],
                "target_audience": "Online shoppers",
                "topics": ["orders", "refunds"],
                "tone_and_style": "Professional",
                "key_entities": ["ShopBot"],
                "confidence": "high",
            }
        elif "qa strategist" in sp:
            # Test plan generator
            return {
                "topics": [
                    {"name": "Refunds", "priority": "high", "risk_level": "high", "estimated_personas": 3},
                    {"name": "Orders", "priority": "high", "risk_level": "medium", "estimated_personas": 3},
                ],
                "persona_strategy": {
                    "total_personas": 10,
                    "standard_pct": 60, "edge_case_pct": 25, "adversarial_pct": 15,
                },
                "conversation_config": {"min_turns": 3, "max_turns": 10},
                "judge_recommendations": [
                    {"judge_name": "grounding", "weight": 0.3},
                    {"judge_name": "safety", "weight": 0.3},
                    {"judge_name": "quality", "weight": 0.2},
                    {"judge_name": "relevance", "weight": 0.2},
                ],
                "risk_summary": "Refunds are highest risk.",
            }
        elif "safety expert" in sp:
            # Guardrail generator
            return {"rules": [
                {"rule": "No PII leakage", "category": "data_privacy", "severity": "critical"},
                {"rule": "No system prompt reveal", "category": "prompt_security", "severity": "critical"},
            ]}
        elif "success criteria" in sp:
            # Criteria generator
            return {"criteria": [
                {"criterion": "Must answer from docs", "category": "grounding", "importance": "high"},
                {"criterion": "Must not reveal system prompt", "category": "safety", "importance": "high"},
                {"criterion": "Must be helpful", "category": "quality", "importance": "medium"},
            ]}
        return {}

    llm.generate_json = AsyncMock(side_effect=smart_generate)
    llm.generate = AsyncMock(return_value="Mock response")
    return llm


def make_auto_approve_gate():
    """Gate that auto-approves everything."""
    return ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)


def make_reject_at_stage(stage_name: str):
    """Gate that rejects at a specific stage."""
    def decision_fn(proposal: GateProposal) -> GateResult:
        if proposal.gate_name == stage_name:
            return GateResult(
                gate_name=proposal.gate_name,
                decision=GateDecision.REJECTED,
                original_proposal=proposal,
                reviewer_notes=f"Rejected at {stage_name}",
            )
        items = [item.content for item in proposal.items] if proposal.items else proposal.raw_data
        return GateResult(
            gate_name=proposal.gate_name,
            decision=GateDecision.APPROVED,
            original_proposal=proposal,
            modified_data=items,
        )
    return ProgrammaticApprovalGate(decision_fn=decision_fn)


# ============================================================
# Tests: Pipeline Result Model
# ============================================================

class TestAnalysisPipelineResult:

    def test_default_state(self):
        result = AnalysisPipelineResult()
        assert not result.completed
        assert not result.analysis_complete
        assert not result.aborted

    def test_aborted_state(self):
        result = AnalysisPipelineResult()
        result.aborted = True
        result.abort_reason = "Test rejection"
        assert not result.completed
        assert result.aborted


# ============================================================
# Tests: Document Loading
# ============================================================

class TestDocumentLoading:

    def test_load_from_directory(self, tmp_docs):
        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999", doc_dir=str(tmp_docs),
            gate=make_auto_approve_gate(),
        )
        load_result = orch._load_documents()

        assert load_result.successful == 2
        assert "ShopBot" in load_result.all_text

    def test_load_from_file_list(self, tmp_docs):
        files = [str(tmp_docs / "readme.md")]
        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999", doc_files=files,
            gate=make_auto_approve_gate(),
        )
        load_result = orch._load_documents()

        assert load_result.successful == 1

    def test_load_no_docs(self):
        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            gate=make_auto_approve_gate(),
        )
        load_result = orch._load_documents()

        assert load_result.successful == 0
        assert len(load_result.errors) > 0


# ============================================================
# Tests: Analysis-Only Pipeline
# ============================================================

class TestAnalysisOnly:

    @pytest.mark.asyncio
    async def test_full_analysis_pipeline(self, tmp_docs, mock_llm):
        """Test the 4-stage analysis pipeline without simulation."""
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            doc_dir=str(tmp_docs),
            gate=make_auto_approve_gate(),
        )

        # Inject mock LLM into analyzers
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        orch._test_plan_gen = TestPlanGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        assert not result.aborted
        assert result.analysis_complete
        assert result.bot_context is not None
        assert result.bot_context.bot_name == "ShopBot"
        assert result.criteria_set is not None
        assert result.criteria_set.count == 3
        assert result.guardrail_set is not None
        assert result.guardrail_set.count == 2
        assert result.test_plan is not None
        assert result.test_plan.topic_count == 2
        assert result.gate_manager is not None
        assert result.gate_manager.total_gates == 4
        assert result.gate_manager.all_approved

    @pytest.mark.asyncio
    async def test_analysis_no_docs_aborts(self):
        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            gate=make_auto_approve_gate(),
        )
        result = await orch.run_analysis_only()

        assert result.aborted
        assert "No documents loaded" in result.abort_reason


# ============================================================
# Tests: Gate Rejections
# ============================================================

class TestGateRejections:

    @pytest.mark.asyncio
    async def test_reject_at_context(self, tmp_docs, mock_llm):
        from src.analyzers.document_analyzer import DocumentAnalyzer

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            doc_dir=str(tmp_docs),
            gate=make_reject_at_stage("bot_context"),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        assert result.aborted
        assert "rejected" in result.abort_reason.lower()
        assert result.criteria_set is None  # Didn't reach stage 2

    @pytest.mark.asyncio
    async def test_reject_at_criteria(self, tmp_docs, mock_llm):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            doc_dir=str(tmp_docs),
            gate=make_reject_at_stage("success_criteria"),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        assert result.aborted
        assert result.bot_context is not None  # Stage 1 passed
        assert result.guardrail_set is None    # Didn't reach stage 3

    @pytest.mark.asyncio
    async def test_reject_at_guardrails(self, tmp_docs, mock_llm):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            doc_dir=str(tmp_docs),
            gate=make_reject_at_stage("guardrail_rules"),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        assert result.aborted
        assert result.criteria_set is not None  # Stage 2 passed
        assert result.test_plan is None          # Didn't reach stage 4

    @pytest.mark.asyncio
    async def test_reject_at_test_plan(self, tmp_docs, mock_llm):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            doc_dir=str(tmp_docs),
            gate=make_reject_at_stage("test_plan"),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        orch._test_plan_gen = TestPlanGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        assert result.aborted
        assert result.guardrail_set is not None  # Stage 3 passed
        assert result.test_plan is not None       # Generated but rejected


# ============================================================
# Tests: SimulationConfig Building
# ============================================================

class TestSimulationConfigBuilding:

    @pytest.mark.asyncio
    async def test_build_config_from_analysis(self, tmp_docs, mock_llm):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999",
            doc_dir=str(tmp_docs),
            bot_api_key="test-key",
            bot_format="openai",
            gate=make_auto_approve_gate(),
            simulation_name="Config Test",
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        orch._test_plan_gen = TestPlanGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        config = orch._build_simulation_config(result)

        assert config.name == "Config Test"
        assert config.bot.api_endpoint == "http://test:9999"
        assert config.bot.api_key == "test-key"
        assert config.num_personas == 10  # From test plan
        assert config.max_turns_per_conversation == 10  # From test plan
        assert len(config.judges) == 4
        assert len(config.success_criteria) >= 3  # criteria + guardrails
        assert len(config.documentation) > 0

    @pytest.mark.asyncio
    async def test_config_includes_guardrails_as_criteria(self, tmp_docs, mock_llm):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999", doc_dir=str(tmp_docs),
            gate=make_auto_approve_gate(),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        orch._test_plan_gen = TestPlanGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()
        config = orch._build_simulation_config(result)

        # Guardrail rules should be included as success criteria
        all_criteria = " ".join(config.success_criteria)
        assert "PII" in all_criteria or "system prompt" in all_criteria


# ============================================================
# Tests: Audit Trail
# ============================================================

class TestAuditTrail:

    @pytest.mark.asyncio
    async def test_audit_trail_recorded(self, tmp_docs, mock_llm):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999", doc_dir=str(tmp_docs),
            gate=make_auto_approve_gate(),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        orch._test_plan_gen = TestPlanGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        trail = result.gate_manager.audit_trail
        assert len(trail.entries) == 4

        gate_names = [e.gate_name for e in trail.entries]
        assert "bot_context" in gate_names
        assert "success_criteria" in gate_names
        assert "guardrail_rules" in gate_names
        assert "test_plan" in gate_names

    @pytest.mark.asyncio
    async def test_audit_trail_saves(self, tmp_docs, mock_llm, tmp_path):
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import DocumentAnalyzer
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        orch = AutonomousOrchestrator(
            bot_endpoint="http://test:9999", doc_dir=str(tmp_docs),
            gate=make_auto_approve_gate(),
        )
        orch._doc_analyzer = DocumentAnalyzer(llm_client=mock_llm)
        orch._criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        orch._guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        orch._test_plan_gen = TestPlanGenerator(llm_client=mock_llm)

        result = await orch.run_analysis_only()

        path = result.gate_manager.save_audit_trail(tmp_path / "audit.json")
        assert path.exists()

        with open(path) as f:
            data = json.load(f)
        assert len(data["entries"]) == 4


# ============================================================
# Tests: CLI Wiring
# ============================================================

class TestCLIWiring:

    def test_run_command_has_mode_option(self):
        from src.cli import main
        run_cmd = main.commands.get("run")
        param_names = [p.name for p in run_cmd.params]
        assert "mode" in param_names

    def test_run_command_has_doc_dir_option(self):
        from src.cli import main
        run_cmd = main.commands.get("run")
        param_names = [p.name for p in run_cmd.params]
        assert "doc_dir" in param_names

    def test_run_command_has_auto_approve_option(self):
        from src.cli import main
        run_cmd = main.commands.get("run")
        param_names = [p.name for p in run_cmd.params]
        assert "auto_approve" in param_names

    def test_run_command_has_analysis_only_option(self):
        from src.cli import main
        run_cmd = main.commands.get("run")
        param_names = [p.name for p in run_cmd.params]
        assert "analysis_only" in param_names

    def test_partial_mode_requires_docs(self):
        """Partial mode without --doc-dir or --doc-file should fail."""
        from click.testing import CliRunner
        from src.cli import main

        runner = CliRunner()
        result = runner.invoke(main, [
            "run",
            "--bot-endpoint", "http://test:9999",
            "--mode", "partial",
        ])
        # Should exit with error about needing docs
        assert result.exit_code != 0 or "doc-dir" in result.output.lower() or "doc-file" in result.output.lower()

    def test_partial_mode_analysis_only(self, tmp_docs, mock_llm):
        """Test analysis-only mode via CLI."""
        from click.testing import CliRunner
        from src.cli import main

        with patch("src.core.autonomous_orchestrator.DocumentAnalyzer") as MockAnalyzer, \
             patch("src.core.autonomous_orchestrator.CriteriaGenerator") as MockCritGen, \
             patch("src.core.autonomous_orchestrator.GuardrailGenerator") as MockGuardGen, \
             patch("src.core.autonomous_orchestrator.TestPlanGenerator") as MockPlanGen:

            # Setup mocks to return valid objects
            from src.analyzers.criteria_generator import CriteriaSet, SuccessCriterion
            from src.analyzers.document_analyzer import BotContext
            from src.analyzers.guardrail_generator import GuardrailRule, GuardrailSet
            from src.analyzers.test_plan_generator import PersonaStrategy, TestPlan, TestTopic

            mock_analyzer = AsyncMock()
            mock_analyzer.analyze = AsyncMock(return_value=BotContext(
                bot_name="TestBot", domain="Testing", purpose="Test",
                capabilities=["test"], limitations=["none"],
            ))
            MockAnalyzer.return_value = mock_analyzer

            mock_crit = AsyncMock()
            mock_crit.generate = AsyncMock(return_value=CriteriaSet(criteria=[
                SuccessCriterion(criterion="Rule 1", category="grounding"),
            ]))
            MockCritGen.return_value = mock_crit

            mock_guard = AsyncMock()
            mock_guard.generate = AsyncMock(return_value=GuardrailSet(rules=[
                GuardrailRule(rule="Guard 1", severity="critical"),
            ]))
            MockGuardGen.return_value = mock_guard

            mock_plan = AsyncMock()
            mock_plan.generate = AsyncMock(return_value=TestPlan(
                topics=[TestTopic(name="Topic 1")],
                persona_strategy=PersonaStrategy(total_personas=10),
            ))
            MockPlanGen.return_value = mock_plan

            runner = CliRunner()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://test:9999",
                "--mode", "partial",
                "--doc-dir", str(tmp_docs),
                "--auto-approve",
                "--analysis-only",
            ])

            assert result.exit_code == 0, f"CLI failed: {result.output}\n{getattr(result, 'exception', '')}"
            assert "Analysis Pipeline Results" in result.output or "Bot Context" in result.output

    def test_mode_defaults_to_manual(self):
        """Without --mode, should default to manual mode."""
        from click.testing import CliRunner
        from unittest.mock import MagicMock
        from src.cli import main

        mock_report = MagicMock()
        mock_report.summary = MagicMock(
            total_personas=1, total_conversations=1, total_turns=2,
            pass_rate=1.0, average_score=0.9, critical_failures=0,
            warnings=0, execution_time_seconds=1.0,
        )
        mock_report.score_by_judge = {}
        mock_report.recommendations = []

        with patch("src.core.orchestrator.SimulationOrchestrator") as MockOrch:
            mock_inst = AsyncMock()
            mock_inst.run_simulation = AsyncMock(return_value=mock_report)
            mock_inst.export_results = MagicMock(return_value={})
            MockOrch.return_value = mock_inst

            runner = CliRunner()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://test:9999",
                "--personas", "1",
            ])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        # Should go through manual mode (SimulationOrchestrator)
        MockOrch.assert_called_once()