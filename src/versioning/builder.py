"""
Fingerprint Builder — captures a RunFingerprint from the current run context.

Called automatically at the start of every simulation to snapshot
the configuration. The fingerprint is embedded in summary.json.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from src.versioning.models import RunFingerprint


class FingerprintBuilder:
    """Builds a RunFingerprint from simulation config and environment."""

    SIMTEST_VERSION = "0.2.0"

    def build(
        self,
        simulation_id: str = "",
        simulation_name: str = "",
        bot_endpoint: str = "",
        bot_format: str = "openai",
        bot_model: str = "",
        bot_prompt: str | None = None,
        num_personas: int = 0,
        max_turns: int = 0,
        min_turns: int = 0,
        pass_threshold: float = 0.7,
        warn_threshold: float = 0.5,
        scenarios: list[str] | None = None,
        stress_enabled: bool = False,
        topics: list[str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> RunFingerprint:
        """Build a fingerprint from explicit parameters."""

        # Hash the bot prompt if provided
        prompt_hash = ""
        prompt_snippet = ""
        if bot_prompt:
            prompt_hash = hashlib.sha256(bot_prompt.encode()).hexdigest()[:16]
            prompt_snippet = bot_prompt[:100].replace("\n", " ")

        # Detect LLM models from environment
        persona_model = os.environ.get("SIMTEST_PERSONA_MODEL", os.environ.get("DEFAULT_MODEL", ""))
        simulator_model = os.environ.get("SIMTEST_SIMULATOR_MODEL", os.environ.get("DEFAULT_MODEL", ""))
        judge_model = os.environ.get("SIMTEST_JUDGE_MODEL", os.environ.get("DEFAULT_MODEL", ""))

        return RunFingerprint(
            simulation_id=simulation_id,
            simulation_name=simulation_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            bot_endpoint=bot_endpoint,
            bot_format=bot_format,
            bot_model=bot_model,
            bot_prompt_hash=prompt_hash,
            bot_prompt_snippet=prompt_snippet,
            num_personas=num_personas,
            max_turns=max_turns,
            min_turns=min_turns,
            pass_threshold=pass_threshold,
            warn_threshold=warn_threshold,
            scenarios_used=scenarios or [],
            stress_enabled=stress_enabled,
            topics=topics or [],
            persona_model=persona_model,
            simulator_model=simulator_model,
            judge_model=judge_model,
            judges_enabled=["grounding", "safety", "quality", "relevance"],
            simtest_version=self.SIMTEST_VERSION,
            tags=tags or {},
        )

    def build_from_config(self, config, **overrides) -> RunFingerprint:
        """
        Build from a SimulationConfig object.

        Accepts overrides for fields not in SimulationConfig
        (like scenarios, stress, topics, tags).
        """
        bot = config.bot if hasattr(config, "bot") else None

        return self.build(
            simulation_id=getattr(config, "id", ""),
            simulation_name=getattr(config, "name", ""),
            bot_endpoint=bot.api_endpoint if bot else "",
            bot_format=bot.request_format if bot else "openai",
            num_personas=getattr(config, "num_personas", 0),
            max_turns=getattr(config, "max_turns_per_conversation", 0),
            min_turns=getattr(config, "min_turns_per_conversation", 0),
            pass_threshold=getattr(config, "pass_threshold", 0.7),
            warn_threshold=getattr(config, "warn_threshold", 0.5),
            **overrides,
        )
