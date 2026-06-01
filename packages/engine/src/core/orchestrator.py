"""
Simulation Orchestrator - The main engine that coordinates the entire simulation testing workflow.
"""

from __future__ import annotations

import time

from src.core.logging import get_logger
from src.core.report_generator import ReportGenerator
from src.exporters.dataset_exporter import DatasetExporter
from src.generators.persona_generator import PersonaGenerator
from src.judges import BaseJudge, JudgeEngine
from src.judges.grounding_judge import GroundingJudge
from src.judges.quality_judge import QualityJudge, RelevanceJudge
from src.judges.safety_judge import SafetyJudge
from src.models import (
    Persona,
    SimulationConfig,
    SimulationReport,
    SimulationRun,
    SimulationStatus,
)
from src.simulators.conversation_simulator import ConversationSimulator

logger = get_logger(__name__)


class SimulationOrchestrator:
    """
    Main orchestrator that coordinates the full simulation testing pipeline:
    1. Generate personas
    2. Run conversations
    3. Judge responses
    4. Generate report
    """

    def __init__(self, config: SimulationConfig):
        self.config = config
        self.persona_generator = PersonaGenerator()
        self.conversation_simulator = ConversationSimulator(
            max_parallel=config.max_parallel_conversations,
        )
        self.judge_engine = JudgeEngine(
            pass_threshold=config.pass_threshold,
            warn_threshold=config.warn_threshold,
        )
        self.report_generator = ReportGenerator()
        self.exporter = DatasetExporter()
        self.run = SimulationRun(id=config.id, config=config)

    async def setup_judges(self, custom_judges: list[BaseJudge] | None = None) -> None:
        """Initialize all judges based on configuration."""
        judge_map = {
            "grounding": lambda cfg: GroundingJudge(
                threshold=cfg.config.get("threshold", 0.35),
            ),
            "safety": lambda cfg: SafetyJudge(
                pii_enabled=cfg.config.get("pii_enabled", True),
                toxicity_enabled=cfg.config.get("toxicity_enabled", True),
                toxicity_threshold=cfg.config.get("toxicity_threshold", 0.7),
            ),
            "quality": lambda cfg: QualityJudge(
                pass_threshold=cfg.config.get("pass_threshold", 6.0),
            ),
            "relevance": lambda _: RelevanceJudge(),
        }

        for judge_config in self.config.judges:
            if not judge_config.enabled:
                continue
            factory = judge_map.get(judge_config.name)
            if factory:
                judge = factory(judge_config)
                judge.weight = judge_config.weight
                self.judge_engine.add_judge(judge)

        if custom_judges:
            for j in custom_judges:
                self.judge_engine.add_judge(j)

        await self.judge_engine.initialize_all()
        logger.info("judges_ready", count=len(self.judge_engine.judges))

    async def generate_personas_only(self) -> list[Persona]:
        """Generate personas without running the full simulation (for preview)."""
        from src.core.llm_client import LLMProviderManager
        provider_mgr = LLMProviderManager()
        await provider_mgr.check_all_providers()

        personas = await self.persona_generator.generate(
            bot_description=self._build_bot_description(),
            documentation=self.config.documentation,
            success_criteria=self.config.success_criteria,
            num_personas=self.config.num_personas,
            persona_type_distribution=self.config.persona_types,
        )
        self.run.personas = personas
        return personas

    async def run_simulation(
        self, personas: list[Persona] | None = None
    ) -> SimulationReport:
        """
        Run the complete simulation pipeline.

        Args:
            personas: Optional pre-defined personas. If None, generates them.

        Returns:
            Complete simulation report with judged conversations.
        """
        start_time = time.time()

        try:
            # Step 0: Validate LLM providers
            from src.core.llm_client import LLMProviderManager
            provider_mgr = LLMProviderManager()
            await provider_mgr.check_all_providers()

            # Step 1: Setup judges
            self._update_status(SimulationStatus.GENERATING_PERSONAS)
            await self.setup_judges()

            # Step 2: Generate or use provided personas
            if personas:
                self.run.personas = personas
            else:
                self.run.personas = await self.persona_generator.generate(
                    bot_description=self._build_bot_description(),
                    documentation=self.config.documentation,
                    success_criteria=self.config.success_criteria,
                    num_personas=self.config.num_personas,
                    persona_type_distribution=self.config.persona_types,
                )

            logger.info("personas_ready", count=len(self.run.personas))

            # Step 3: Run conversations
            self._update_status(SimulationStatus.RUNNING)
            self.run.conversations = await self.conversation_simulator.run_conversations(
                personas=self.run.personas,
                bot_config=self.config.bot,
                max_turns=self.config.max_turns_per_conversation,
                min_turns=getattr(self.config, 'min_turns_per_conversation', 1),
            )

            logger.info("conversations_done", count=len(self.run.conversations))

            # Step 4: Judge all conversations
            self._update_status(SimulationStatus.JUDGING)
            persona_map = {p.id: p for p in self.run.personas}
            judged = await self.judge_engine.judge_all_conversations(
                conversations=self.run.conversations,
                personas=persona_map,
                documentation=self.config.documentation,
            )

            # Step 5: Generate report
            self._update_status(SimulationStatus.GENERATING_REPORT)
            execution_time = time.time() - start_time

            report = self.report_generator.generate(
                simulation_id=self.config.id,
                simulation_name=self.config.name,
                personas=self.run.personas,
                judged_conversations=judged,
                execution_time_seconds=execution_time,
            )

            self.run.report = report
            self._update_status(SimulationStatus.COMPLETED)

            logger.info(
                "simulation_complete",
                pass_rate=report.summary.pass_rate,
                avg_score=report.summary.average_score,
                time=f"{execution_time:.1f}s",
            )

            return report

        except Exception as e:
            self._update_status(SimulationStatus.FAILED)
            self.run.error_message = str(e)
            logger.error("simulation_failed", error=str(e))
            raise

    def export_results(
        self, output_dir: str = "./reports", formats: list[str] | None = None
    ) -> dict[str, str]:
        """Export simulation results in multiple formats."""
        if not self.run.report:
            raise ValueError("No report available. Run simulation first.")

        from pathlib import Path
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        formats = formats or ["jsonl", "csv", "summary"]
        exported: dict[str, str] = {}

        if "jsonl" in formats:
            p = self.exporter.export_jsonl(self.run.report, out / "conversations.jsonl")
            exported["jsonl"] = str(p)

        if "csv" in formats:
            p = self.exporter.export_csv(self.run.report, out / "results.csv")
            exported["csv"] = str(p)

        if "dpo" in formats:
            p = self.exporter.export_dpo_pairs(self.run.report, out / "dpo_pairs.jsonl")
            exported["dpo"] = str(p)

        if "summary" in formats:
            p = self.exporter.export_summary_json(self.run.report, out / "summary.json")
            exported["summary"] = str(p)

        if "html" in formats:
            from src.exporters.html_report import HTMLReportExporter
            html_exporter = HTMLReportExporter()
            p = html_exporter.export(self.run.report, self.run.personas, out / "report.html")
            exported["html"] = str(p)

        return exported

    def _build_bot_description(self) -> str:
        """Build a bot description from config for persona generation."""
        parts = [f"API Endpoint: {self.config.bot.api_endpoint}"]
        if self.config.success_criteria:
            parts.append("Success Criteria:")
            for c in self.config.success_criteria:
                parts.append(f"  - {c}")
        return "\n".join(parts)

    def _update_status(self, status: SimulationStatus) -> None:
        self.run.status = status
        logger.info("status_change", status=status.value)