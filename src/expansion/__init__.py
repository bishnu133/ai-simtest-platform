"""
Adaptive Expansion Module — Auto-generate failure variations and confirm reproducibility.

P3 #13: The "self-improving QA engineer" feature.

When a simulation finds failures, Adaptive Expansion:
1. Extracts structured failure signals from judged conversations
2. Generates N conversation variants per failure (LLM-powered)
3. Executes variants against the target bot and judges them
4. Computes reproducibility scores and finds minimal reproducible conversations

Usage:
    from src.expansion import AdaptiveExpansionEngine, AdaptiveExpansionConfig

    config = AdaptiveExpansionConfig(
        max_signals=5,
        variants_per_signal=5,
        reproducibility_threshold=0.6,
    )
    engine = AdaptiveExpansionEngine(bot_config=bot_config, config=config)
    report = await engine.run(judged_conversations)

    print(f"Confirmed bugs: {report.confirmed_count}")
    print(f"Likely bugs: {report.likely_count}")
    print(f"Flukes: {report.fluke_count}")

CLI:
    simtest run --bot-endpoint URL --expand-failures
    simtest run --bot-endpoint URL --expand-failures --variants 3 --expand-top 5
    simtest expand reports/summary.json --bot-endpoint URL
"""

from .analyzer import ExpansionAnalyzer
from .engine import AdaptiveExpansionEngine
from .models import (
    AdaptiveExpansionConfig,
    ExpansionReport,
    ExpansionVariant,
    FailureSignal,
    ReproducibilityVerdict,
    SignalExpansionResult,
    SignalSeverity,
    VariantResult,
    VariantStrategy,
)
from .signal_extractor import SignalExtractor
from .variant_generator import VariantGenerator

__all__ = [
    "AdaptiveExpansionEngine",
    "AdaptiveExpansionConfig",
    "ExpansionAnalyzer",
    "ExpansionReport",
    "ExpansionVariant",
    "FailureSignal",
    "ReproducibilityVerdict",
    "SignalExpansionResult",
    "SignalExtractor",
    "SignalSeverity",
    "VariantGenerator",
    "VariantResult",
    "VariantStrategy",
]
