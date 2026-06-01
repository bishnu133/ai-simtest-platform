"""
Adaptive Expansion Engine — Main orchestrator combining all 4 phases.

This is the top-level entry point for the Adaptive Expansion feature.
It coordinates:
  Phase 1: Signal Extraction (scan judged conversations for failures)
  Phase 2: Variant Generation (LLM creates test variations)
  Phase 3: Execution (run variants against bot + judge)
  Phase 4: Analysis (reproducibility scores + minimal repro)

Usage:
    from ai_simtest_engine.expansion import AdaptiveExpansionEngine, AdaptiveExpansionConfig

    config = AdaptiveExpansionConfig(max_signals=5, variants_per_signal=5)
    engine = AdaptiveExpansionEngine(
        bot_config=bot_config,
        config=config,
    )
    report = await engine.run(judged_conversations)
    print(f"Confirmed bugs: {report.confirmed_count}")
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ai_simtest_engine.models import BotConfig

from .analyzer import ExpansionAnalyzer
from .models import (
    AdaptiveExpansionConfig,
    ExpansionReport,
    SignalExpansionResult,
)
from .signal_extractor import SignalExtractor
from .variant_generator import VariantGenerator

logger = logging.getLogger(__name__)


class AdaptiveExpansionEngine:
    """
    Main engine that orchestrates the 4-phase adaptive expansion pipeline.

    Designed to be called from the CLI after a simulation completes,
    or standalone via `simtest expand`.
    """

    def __init__(
        self,
        bot_config: BotConfig,
        config: AdaptiveExpansionConfig | None = None,
        llm_client=None,
        judge_engine=None,
    ):
        self.bot_config = bot_config
        self.config = config or AdaptiveExpansionConfig()
        self._llm = llm_client
        self._judge_engine = judge_engine

        # Initialize sub-components
        self.signal_extractor = SignalExtractor(self.config)
        self.variant_generator = VariantGenerator(llm_client=llm_client, config=self.config)
        self.analyzer = ExpansionAnalyzer(self.config)

    async def run(
        self,
        judged_conversations: list,
        workflow_results: list[dict[str, Any]] | None = None,
        progress_callback=None,
    ) -> ExpansionReport:
        """
        Run the complete adaptive expansion pipeline.

        Args:
            judged_conversations: Judged conversations from the simulation.
            workflow_results: Optional workflow judge results.
            progress_callback: Optional callable(phase, detail) for progress updates.

        Returns:
            ExpansionReport with confirmed bugs, likely bugs, and flukes.
        """
        start_time = time.time()
        errors: list[str] = []

        # Phase 1: Extract failure signals
        self._notify(progress_callback, "signal_extraction", "Scanning for failure signals...")
        signals = self.signal_extractor.extract(
            judged_conversations=judged_conversations,
            workflow_results=workflow_results,
        )

        total_signals_found = len(signals)
        logger.info("signals_extracted: count=%d", total_signals_found)

        if not signals:
            return self.analyzer.build_report(
                total_signals_found=0,
                expansion_results=[],
                errors=[],
                execution_time=time.time() - start_time,
            )

        # Phase 2 + 3: For each signal, generate variants and execute them
        expansion_results: list[SignalExpansionResult] = []

        for i, signal in enumerate(signals):
            signal_label = f"[{i+1}/{len(signals)}] {signal.judge_name}:{signal.severity.value}"
            self._notify(
                progress_callback,
                "variant_generation",
                f"Generating variants for {signal_label}...",
            )

            try:
                # Phase 2: Generate variants
                variants = await self.variant_generator.generate(signal)

                if not variants:
                    errors.append(f"No variants generated for signal {signal.signal_id}")
                    continue

                logger.info("variants_generated: signal=%s count=%d", signal.signal_id, len(variants))

                # Phase 3: Execute variants
                self._notify(
                    progress_callback,
                    "execution",
                    f"Running {len(variants)} variants for {signal_label}...",
                )

                from .executor import VariantExecutor
                executor = VariantExecutor(
                    bot_config=self.bot_config,
                    judge_engine=self._judge_engine,
                    llm_client=self._llm,
                    config=self.config,
                )
                results = await executor.execute_variants(signal, variants)

                # Phase 4: Analyze results
                self._notify(
                    progress_callback,
                    "analysis",
                    f"Analyzing reproducibility for {signal_label}...",
                )
                signal_result = self.analyzer.analyze_signal(signal, variants, results)
                expansion_results.append(signal_result)

                logger.info(
                    "signal_analyzed: signal=%s verdict=%s repro=%.0f%%",
                    signal.signal_id, signal_result.verdict.value,
                    signal_result.reproducibility_score * 100,
                )

            except Exception as e:
                error_msg = f"Error expanding signal {signal.signal_id}: {e}"
                logger.error("expansion_error: signal=%s error=%s", signal.signal_id, str(e))
                errors.append(error_msg)

        # Build final report
        report = self.analyzer.build_report(
            total_signals_found=total_signals_found,
            expansion_results=expansion_results,
            errors=errors,
            execution_time=time.time() - start_time,
        )

        logger.info(
            "expansion_complete: confirmed=%d likely=%d flukes=%d time=%.1fs",
            report.confirmed_count, report.likely_count, report.fluke_count,
            report.execution_time_seconds,
        )

        return report

    def _notify(self, callback, phase: str, detail: str) -> None:
        """Send progress notification if callback is provided."""
        if callback:
            try:
                callback(phase, detail)
            except Exception:
                pass
