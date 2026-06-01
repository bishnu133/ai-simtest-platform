"""
Autonomous Orchestrator — Partial Autonomous simulation pipeline.

Chains the document analysis stages with approval gates and simulation execution:

  Mode: partial
  Input: Bot endpoint + documentation directory
  Flow:
    1. Load documents (format-agnostic)
    2. Analyze → Bot Context        → [Approval Gate 1]
    3. Generate → Success Criteria   → [Approval Gate 2]
    4. Generate → Guardrail Rules    → [Approval Gate 3]
    5. Generate → Test Plan          → [Approval Gate 4]
    6. Generate → Personas           → [Approval Gate 5]
    7. Execute simulation with approved personas
    8. Export results + audit trail
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.analyzers.criteria_generator import CriteriaGenerator, CriteriaSet
from src.analyzers.document_analyzer import BotContext, DocumentAnalyzer
from src.analyzers.document_loader import DocumentLoader, LoadResult
from src.analyzers.guardrail_generator import GuardrailGenerator, GuardrailSet
from src.analyzers.test_plan_generator import TestPlan, TestPlanGenerator
from src.core.approval_gate import (
    ApprovalGate,
    CLIApprovalGate,
    GateDecision,
    GateManager,
    GateResult,
    ProgrammaticApprovalGate,
)
from src.core.logging import get_logger
from src.core.orchestrator import SimulationOrchestrator
from src.models import (
    BotConfig,
    JudgeConfig,
    SimulationConfig,
    SimulationReport,
)

logger = get_logger(__name__)


# ============================================================
# Pipeline Stage Results
# ============================================================

class AnalysisPipelineResult:
    """Holds all intermediate and final results from the analysis pipeline."""

    def __init__(self):
        self.load_result: LoadResult | None = None
        self.bot_context: BotContext | None = None
        self.criteria_set: CriteriaSet | None = None
        self.guardrail_set: GuardrailSet | None = None
        self.test_plan: TestPlan | None = None
        self.simulation_report: SimulationReport | None = None
        self.gate_manager: GateManager | None = None
        self.aborted: bool = False
        self.abort_reason: str = ""
        self.execution_time_seconds: float = 0.0

    @property
    def completed(self) -> bool:
        return self.simulation_report is not None and not self.aborted

    @property
    def analysis_complete(self) -> bool:
        return self.test_plan is not None and not self.aborted


# ============================================================
# Autonomous Orchestrator
# ============================================================

class AutonomousOrchestrator:
    """
    Orchestrates the Partial Autonomous simulation pipeline.

    Usage (CLI):
        simtest run --mode partial --doc-dir ./docs/ --bot-endpoint http://bot/api

    Usage (Programmatic):
        orch = AutonomousOrchestrator(
            bot_endpoint="http://bot/api",
            doc_dir="./docs/",
            gate=CLIApprovalGate(),  # or ProgrammaticApprovalGate for testing
        )
        result = await orch.run()
    """

    def __init__(
        self,
        bot_endpoint: str,
        doc_dir: str | None = None,
        doc_files: list[str] | None = None,
        bot_api_key: str | None = None,
        bot_format: str = "openai",
        bot_response_path: str = "choices.0.message.content",
        gate: ApprovalGate | None = None,
        auto_approve: bool = False,
        output_dir: str = "./reports",
        export_formats: list[str] | None = None,
        simulation_name: str = "Partial Autonomous Simulation",
        num_personas: int | None = None,
        max_turns: int | None = None,
        min_turns: int = 1,
        max_parallel: int = 3,
    ):
        self.bot_endpoint = bot_endpoint
        self.doc_dir = doc_dir
        self.doc_files = doc_files
        self.bot_api_key = bot_api_key
        self.bot_format = bot_format
        self.bot_response_path = bot_response_path
        self.output_dir = output_dir
        self.export_formats = export_formats or ["jsonl", "csv", "summary", "html"]
        self.simulation_name = simulation_name
        self.num_personas = num_personas
        self.max_turns = max_turns
        self.min_turns = min_turns
        self.max_parallel = max_parallel

        # Gate setup
        if gate:
            self.gate = gate
        elif auto_approve:
            self.gate = CLIApprovalGate(auto_approve=True)
        else:
            self.gate = CLIApprovalGate()

        # Components (lazy initialized)
        self._doc_loader = DocumentLoader()
        self._doc_analyzer: DocumentAnalyzer | None = None
        self._criteria_gen: CriteriaGenerator | None = None
        self._guardrail_gen: GuardrailGenerator | None = None
        self._test_plan_gen: TestPlanGenerator | None = None

    async def run(self) -> AnalysisPipelineResult:
        """
        Execute the full partial autonomous pipeline.

        Returns AnalysisPipelineResult with all intermediate outputs.
        """
        start_time = time.time()
        result = AnalysisPipelineResult()
        gate_manager = GateManager(gate=self.gate, simulation_id=self.simulation_name)
        result.gate_manager = gate_manager

        try:
            # ── Stage 0: Load Documents ──────────────────────────
            logger.info("pipeline_stage", stage=0, name="loading_documents")
            result.load_result = self._load_documents()

            if not result.load_result or result.load_result.successful == 0:
                result.aborted = True
                result.abort_reason = (
                    f"No documents loaded. Errors: {result.load_result.errors if result.load_result else 'No path provided'}"
                )
                logger.error("pipeline_aborted", reason=result.abort_reason)
                return result

            logger.info(
                "documents_loaded",
                files=result.load_result.successful,
                total_chars=len(result.load_result.all_text),
            )

            # ── Stage 1: Analyze Documents → Bot Context ─────────
            logger.info("pipeline_stage", stage=1, name="analyzing_documents")
            self._doc_analyzer = self._doc_analyzer or DocumentAnalyzer()
            result.bot_context = await self._doc_analyzer.analyze(result.load_result)

            gate_result = await gate_manager.submit_gate(
                gate_name="bot_context",
                title="Bot Description & Context",
                description=f"Extracted from {result.load_result.successful} document(s). "
                            f"Confidence: {result.bot_context.confidence}",
                raw_data=result.bot_context.model_dump(mode="json"),
                stage_number=1,
            )

            if gate_manager.was_rejected("bot_context"):
                result.aborted = True
                result.abort_reason = f"Bot context rejected: {gate_result.reviewer_notes}"
                return result

            # Apply modifications if any
            if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
                result.bot_context = BotContext(**gate_result.modified_data)

            # ── Stage 2: Generate Success Criteria ───────────────
            logger.info("pipeline_stage", stage=2, name="generating_criteria")
            self._criteria_gen = self._criteria_gen or CriteriaGenerator()
            result.criteria_set = await self._criteria_gen.generate(
                result.bot_context,
                extra_documentation=result.load_result.all_text[:3000],
            )

            gate_result = await gate_manager.submit_gate(
                gate_name="success_criteria",
                title="Success Criteria",
                description=f"Generated {result.criteria_set.count} criteria in "
                            f"{len(result.criteria_set.by_category)} categories",
                items=result.criteria_set.to_proposal_items(),
                stage_number=2,
            )

            if gate_manager.was_rejected("success_criteria"):
                result.aborted = True
                result.abort_reason = f"Success criteria rejected: {gate_result.reviewer_notes}"
                return result

            if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
                result.criteria_set = CriteriaGenerator.from_approval_data(gate_result.modified_data)

            # ── Stage 3: Generate Guardrail Rules ────────────────
            logger.info("pipeline_stage", stage=3, name="generating_guardrails")
            self._guardrail_gen = self._guardrail_gen or GuardrailGenerator()
            result.guardrail_set = await self._guardrail_gen.generate(
                result.bot_context, result.criteria_set,
            )

            gate_result = await gate_manager.submit_gate(
                gate_name="guardrail_rules",
                title="Guardrail Rules",
                description=f"Generated {result.guardrail_set.count} rules "
                            f"({len(result.guardrail_set.critical_rules)} critical)",
                items=result.guardrail_set.to_proposal_items(),
                stage_number=3,
            )

            if gate_manager.was_rejected("guardrail_rules"):
                result.aborted = True
                result.abort_reason = f"Guardrail rules rejected: {gate_result.reviewer_notes}"
                return result

            if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
                result.guardrail_set = GuardrailGenerator.from_approval_data(gate_result.modified_data)

            # ── Stage 4: Generate Test Plan ──────────────────────
            logger.info("pipeline_stage", stage=4, name="generating_test_plan")
            self._test_plan_gen = self._test_plan_gen or TestPlanGenerator()
            result.test_plan = await self._test_plan_gen.generate(
                result.bot_context, result.criteria_set, result.guardrail_set,
            )

            gate_result = await gate_manager.submit_gate(
                gate_name="test_plan",
                title="Test Plan",
                description=f"{result.test_plan.topic_count} topics, "
                            f"{result.test_plan.persona_strategy.total_personas} personas, "
                            f"{result.test_plan.persona_strategy.adversarial_pct}% adversarial",
                items=result.test_plan.to_proposal_items(),
                stage_number=4,
            )

            if gate_manager.was_rejected("test_plan"):
                result.aborted = True
                result.abort_reason = f"Test plan rejected: {gate_result.reviewer_notes}"
                return result

            if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
                result.test_plan = TestPlanGenerator.from_approval_data(gate_result.modified_data)

            # ── Stage 5: Generate & Approve Personas ────────────
            logger.info("pipeline_stage", stage=5, name="generating_personas")
            sim_config = self._build_simulation_config(result)
            sim_orchestrator = SimulationOrchestrator(sim_config)

            # Generate personas only (no simulation yet)
            personas = await sim_orchestrator.generate_personas_only()

            if not personas:
                result.aborted = True
                result.abort_reason = "No personas generated"
                return result

            # Build persona items for approval gate
            persona_items = self._personas_to_proposal_items(personas)

            type_counts = {}
            for p in personas:
                t = p.persona_type.value if hasattr(p.persona_type, 'value') else str(p.persona_type)
                type_counts[t] = type_counts.get(t, 0) + 1
            type_summary = ", ".join(f"{c} {t}" for t, c in type_counts.items())

            gate_result = await gate_manager.submit_gate(
                gate_name="personas",
                title="Test Personas",
                description=f"Generated {len(personas)} personas ({type_summary}). "
                            f"Remove unwanted personas before simulation.",
                items=persona_items,
                stage_number=5,
            )

            if gate_manager.was_rejected("personas"):
                result.aborted = True
                result.abort_reason = f"Personas rejected: {gate_result.reviewer_notes}"
                return result

            # Apply modifications — filter out removed personas
            approved_personas = personas
            if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
                approved_personas = self._apply_persona_modifications(
                    personas, gate_result.modified_data
                )

            if not approved_personas:
                result.aborted = True
                result.abort_reason = "All personas were removed"
                return result

            logger.info(
                "personas_approved",
                total_generated=len(personas),
                total_approved=len(approved_personas),
                removed=len(personas) - len(approved_personas),
            )

            # ── Stage 6: Execute Simulation with Approved Personas ─
            logger.info("pipeline_stage", stage=6, name="executing_simulation")
            result.simulation_report = await sim_orchestrator.run_simulation(
                personas=approved_personas
            )

            # ── Stage 7: Export Results ───────────────────────────
            logger.info("pipeline_stage", stage=7, name="exporting_results")
            exported = sim_orchestrator.export_results(
                output_dir=self.output_dir,
                formats=self.export_formats,
            )

            # Save audit trail alongside reports
            audit_path = gate_manager.save_audit_trail(
                Path(self.output_dir) / "audit_trail.json"
            )
            exported["audit_trail"] = str(audit_path)

            result.execution_time_seconds = time.time() - start_time

            logger.info(
                "pipeline_complete",
                pass_rate=result.simulation_report.summary.pass_rate,
                execution_time=f"{result.execution_time_seconds:.1f}s",
                gates=gate_manager.summary,
                exported=list(exported.keys()),
            )

            return result

        except Exception as e:
            result.aborted = True
            result.abort_reason = f"Pipeline error: {str(e)}"
            result.execution_time_seconds = time.time() - start_time
            logger.error("pipeline_failed", error=str(e))
            raise

    async def run_analysis_only(self) -> AnalysisPipelineResult:
        """
        Run only the analysis stages (1-4) without executing simulation.
        Useful for previewing what the platform would test.
        """
        start_time = time.time()
        result = AnalysisPipelineResult()
        gate_manager = GateManager(gate=self.gate, simulation_id=self.simulation_name)
        result.gate_manager = gate_manager

        # Load documents
        result.load_result = self._load_documents()
        if not result.load_result or result.load_result.successful == 0:
            result.aborted = True
            result.abort_reason = "No documents loaded"
            return result

        # Stage 1: Context
        self._doc_analyzer = self._doc_analyzer or DocumentAnalyzer()
        result.bot_context = await self._doc_analyzer.analyze(result.load_result)

        gate_result = await gate_manager.submit_gate(
            gate_name="bot_context", title="Bot Context",
            raw_data=result.bot_context.model_dump(mode="json"), stage_number=1,
        )
        if gate_manager.was_rejected("bot_context"):
            result.aborted = True
            result.abort_reason = "Bot context rejected"
            return result
        if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
            result.bot_context = BotContext(**gate_result.modified_data)

        # Stage 2: Criteria
        self._criteria_gen = self._criteria_gen or CriteriaGenerator()
        result.criteria_set = await self._criteria_gen.generate(result.bot_context)

        gate_result = await gate_manager.submit_gate(
            gate_name="success_criteria", title="Success Criteria",
            items=result.criteria_set.to_proposal_items(), stage_number=2,
        )
        if gate_manager.was_rejected("success_criteria"):
            result.aborted = True
            return result
        if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
            result.criteria_set = CriteriaGenerator.from_approval_data(gate_result.modified_data)

        # Stage 3: Guardrails
        self._guardrail_gen = self._guardrail_gen or GuardrailGenerator()
        result.guardrail_set = await self._guardrail_gen.generate(
            result.bot_context, result.criteria_set,
        )

        gate_result = await gate_manager.submit_gate(
            gate_name="guardrail_rules", title="Guardrail Rules",
            items=result.guardrail_set.to_proposal_items(), stage_number=3,
        )
        if gate_manager.was_rejected("guardrail_rules"):
            result.aborted = True
            return result
        if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
            result.guardrail_set = GuardrailGenerator.from_approval_data(gate_result.modified_data)

        # Stage 4: Test Plan
        self._test_plan_gen = self._test_plan_gen or TestPlanGenerator()
        result.test_plan = await self._test_plan_gen.generate(
            result.bot_context, result.criteria_set, result.guardrail_set,
        )

        gate_result = await gate_manager.submit_gate(
            gate_name="test_plan", title="Test Plan",
            items=result.test_plan.to_proposal_items(), stage_number=4,
        )
        if gate_manager.was_rejected("test_plan"):
            result.aborted = True
            return result
        if gate_result.decision == GateDecision.MODIFIED and gate_result.modified_data:
            result.test_plan = TestPlanGenerator.from_approval_data(gate_result.modified_data)

        result.execution_time_seconds = time.time() - start_time
        return result

    # ============================================================
    # Internal helpers
    # ============================================================

    def _load_documents(self) -> LoadResult:
        """Load documents from directory or file list."""
        if self.doc_dir:
            return self._doc_loader.load_directory(self.doc_dir)
        elif self.doc_files:
            return self._doc_loader.load_files([Path(f) for f in self.doc_files])
        else:
            return LoadResult(errors=["No document directory or files provided"])

    def _build_simulation_config(self, result: AnalysisPipelineResult) -> SimulationConfig:
        """Build SimulationConfig from analysis pipeline results."""
        plan = result.test_plan
        context = result.bot_context
        criteria = result.criteria_set
        guardrails = result.guardrail_set

        # Build documentation string from context + original docs
        doc_text = ""
        if result.load_result:
            doc_text = result.load_result.all_text[:10000]  # Cap at 10K chars

        # Build success criteria list
        success_criteria = []
        if criteria:
            success_criteria = criteria.as_string_list
        if guardrails:
            success_criteria.extend(guardrails.as_policy_strings())

        # Build judge configs from test plan
        judge_configs = []
        if plan and plan.judge_recommendations:
            for j in plan.judge_recommendations:
                judge_configs.append(JudgeConfig(
                    name=j.judge_name,
                    weight=j.weight,
                    enabled=j.enabled,
                ))
        else:
            judge_configs = [
                JudgeConfig(name="grounding", weight=0.30),
                JudgeConfig(name="safety", weight=0.30),
                JudgeConfig(name="quality", weight=0.20),
                JudgeConfig(name="relevance", weight=0.20),
            ]

        # Persona config
        # num_personas = plan.persona_strategy.total_personas if plan else 20
        num_personas = self.num_personas or (plan.persona_strategy.total_personas if plan else 20)
        # max_turns = plan.conversation_config.max_turns if plan else 15
        max_turns = self.max_turns or (plan.conversation_config.max_turns if plan else 15)

        persona_types = {}
        if plan:
            persona_types = {
                "standard": plan.persona_strategy.standard_pct,
                "edge_case": plan.persona_strategy.edge_case_pct,
                "adversarial": plan.persona_strategy.adversarial_pct,
            }

        return SimulationConfig(
            name=self.simulation_name,
            bot=BotConfig(
                api_endpoint=self.bot_endpoint,
                api_key=self.bot_api_key,
                request_format=self.bot_format,
                response_path=self.bot_response_path,
            ),
            documentation=doc_text,
            success_criteria=success_criteria,
            num_personas=num_personas,
            max_turns_per_conversation=max_turns,
            min_turns_per_conversation=self.min_turns,
            persona_types=persona_types,
            judges=judge_configs,
        )

    @staticmethod
    def _personas_to_proposal_items(personas) -> list[dict[str, Any]]:
        """Convert Persona objects to approval gate proposal items."""
        items = []
        for i, p in enumerate(personas):
            p_type = p.persona_type.value if hasattr(p.persona_type, 'value') else str(p.persona_type)
            tech = p.technical_level.value if hasattr(p.technical_level, 'value') else str(p.technical_level)

            item_text = (
                f"name: {p.name}\n"
                f"type: {p_type}\n"
                f"tone: {p.tone}\n"
                f"tech_level: {tech}\n"
                f"goals: {', '.join(p.goals[:3])}"
            )

            if p.topics:
                item_text += f"\ntopics: {', '.join(p.topics[:3])}"

            if p.adversarial_tactics:
                item_text += f"\ntactics: {', '.join(p.adversarial_tactics[:3])}"

            reasoning = f"{p_type.replace('_', ' ').title()} persona"
            if p.adversarial_tactics:
                reasoning += f" — tests: {', '.join(p.adversarial_tactics[:2])}"
            else:
                reasoning += f" — tests: {', '.join(p.goals[:2])}"

            items.append({
                "content": item_text,
                "confidence": "high" if p_type == "standard" else "medium",
                "reasoning": reasoning,
            })

        return items

    @staticmethod
    def _apply_persona_modifications(personas, modified_data) -> list:
        """
        Apply gate modifications to persona list.

        Supports:
        - removed_indices: list of 0-based indices to remove
        - kept_items: list of approved items (by position)
        """
        if not modified_data:
            return personas

        # Handle removed_indices (from gate "remove 1,3" action)
        removed_indices = set()
        if "removed_indices" in modified_data:
            removed_indices = {int(i) for i in modified_data["removed_indices"]}
        elif "kept_items" in modified_data:
            # Inverse: kept_items contains indices that survived
            kept = {int(i) for i in modified_data["kept_items"]}
            removed_indices = {i for i in range(len(personas)) if i not in kept}

        if removed_indices:
            approved = [
                p for i, p in enumerate(personas)
                if i not in removed_indices
            ]
            logger.info(
                "personas_filtered",
                original=len(personas),
                removed=len(removed_indices),
                remaining=len(approved),
                removed_names=[personas[i].name for i in sorted(removed_indices) if i < len(personas)],
            )
            return approved

        return personas