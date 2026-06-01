"""
Version Tracking Models — P3 #16

Tracks WHAT was tested per simulation run so that compare mode
can answer "why did scores change?" not just "scores changed."

Three layers:
  1. RunFingerprint — snapshot of bot + tool config at run time
  2. VersionHistory — append-only log of all run fingerprints
  3. VersionComparison — diff between two fingerprints
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RunFingerprint:
    """
    Immutable snapshot of everything that could affect test results.

    Captured automatically at the start of every simulation run.
    Stored inside summary.json so compare mode can diff them.
    """

    # ── Simulation identity ────────────────────────────────────
    simulation_id: str = ""
    simulation_name: str = ""
    timestamp: str = ""

    # ── Bot under test ─────────────────────────────────────────
    bot_endpoint: str = ""
    bot_format: str = "openai"                  # openai, anthropic, custom
    bot_model: str = ""                         # Detected or user-provided
    bot_prompt_hash: str = ""                   # SHA-256 of system prompt (if provided)
    bot_prompt_snippet: str = ""                # First 100 chars of prompt (for display)

    # ── Test configuration ─────────────────────────────────────
    num_personas: int = 0
    max_turns: int = 0
    min_turns: int = 0
    pass_threshold: float = 0.7
    warn_threshold: float = 0.5
    scenarios_used: list[str] = field(default_factory=list)
    stress_enabled: bool = False
    topics: list[str] = field(default_factory=list)

    # ── LLM configuration ─────────────────────────────────────
    persona_model: str = ""                     # Model used for persona generation
    simulator_model: str = ""                   # Model used for user simulation
    judge_model: str = ""                       # Model used for quality judge

    # ── Judge configuration ────────────────────────────────────
    judges_enabled: list[str] = field(default_factory=list)
    grounding_threshold: float = 0.35
    safety_toxicity_threshold: float = 0.7

    # ── Tool version ───────────────────────────────────────────
    simtest_version: str = "0.2.0"

    # ── Custom metadata ────────────────────────────────────────
    tags: dict[str, str] = field(default_factory=dict)  # e.g. {"environment": "staging", "sprint": "24"}

    @property
    def fingerprint_hash(self) -> str:
        """
        Deterministic hash of the config that affects results.
        Two runs with identical fingerprint_hash should produce similar results.
        """
        key_parts = [
            self.bot_endpoint,
            self.bot_format,
            self.bot_model,
            self.bot_prompt_hash,
            str(self.num_personas),
            str(self.max_turns),
            str(self.pass_threshold),
            str(self.warn_threshold),
            ",".join(sorted(self.scenarios_used)),
            str(self.stress_enabled),
            ",".join(sorted(self.topics)),
            self.persona_model,
            self.simulator_model,
            self.judge_model,
            ",".join(sorted(self.judges_enabled)),
        ]
        combined = "|".join(key_parts)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "simulation_id": self.simulation_id,
            "simulation_name": self.simulation_name,
            "timestamp": self.timestamp,
            "fingerprint_hash": self.fingerprint_hash,
            "bot": {
                "endpoint": self.bot_endpoint,
                "format": self.bot_format,
                "model": self.bot_model,
                "prompt_hash": self.bot_prompt_hash,
                "prompt_snippet": self.bot_prompt_snippet,
            },
            "config": {
                "num_personas": self.num_personas,
                "max_turns": self.max_turns,
                "min_turns": self.min_turns,
                "pass_threshold": self.pass_threshold,
                "warn_threshold": self.warn_threshold,
                "scenarios": self.scenarios_used,
                "stress_enabled": self.stress_enabled,
                "topics": self.topics,
            },
            "llm": {
                "persona_model": self.persona_model,
                "simulator_model": self.simulator_model,
                "judge_model": self.judge_model,
            },
            "judges": {
                "enabled": self.judges_enabled,
                "grounding_threshold": self.grounding_threshold,
                "safety_toxicity_threshold": self.safety_toxicity_threshold,
            },
            "simtest_version": self.simtest_version,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RunFingerprint:
        bot = data.get("bot", {})
        config = data.get("config", {})
        llm = data.get("llm", {})
        judges = data.get("judges", {})

        return cls(
            simulation_id=data.get("simulation_id", ""),
            simulation_name=data.get("simulation_name", ""),
            timestamp=data.get("timestamp", ""),
            bot_endpoint=bot.get("endpoint", ""),
            bot_format=bot.get("format", "openai"),
            bot_model=bot.get("model", ""),
            bot_prompt_hash=bot.get("prompt_hash", ""),
            bot_prompt_snippet=bot.get("prompt_snippet", ""),
            num_personas=config.get("num_personas", 0),
            max_turns=config.get("max_turns", 0),
            min_turns=config.get("min_turns", 0),
            pass_threshold=config.get("pass_threshold", 0.7),
            warn_threshold=config.get("warn_threshold", 0.5),
            scenarios_used=config.get("scenarios", []),
            stress_enabled=config.get("stress_enabled", False),
            topics=config.get("topics", []),
            persona_model=llm.get("persona_model", ""),
            simulator_model=llm.get("simulator_model", ""),
            judge_model=llm.get("judge_model", ""),
            judges_enabled=judges.get("enabled", []),
            grounding_threshold=judges.get("grounding_threshold", 0.35),
            safety_toxicity_threshold=judges.get("safety_toxicity_threshold", 0.7),
            simtest_version=data.get("simtest_version", ""),
            tags=data.get("tags", {}),
        )


@dataclass
class ConfigChange:
    """A single change between two fingerprints."""
    field: str              # e.g. "bot.model", "config.pass_threshold"
    old_value: str          # Value in baseline
    new_value: str          # Value in current
    category: str = ""      # "bot", "config", "llm", "judges", "tags"
    impact: str = "unknown" # "high", "medium", "low" — how likely to affect results

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "category": self.category,
            "impact": self.impact,
        }


@dataclass
class VersionComparison:
    """Diff between two RunFingerprints — answers 'what changed?'"""

    baseline_id: str = ""
    current_id: str = ""
    baseline_hash: str = ""
    current_hash: str = ""
    configs_identical: bool = False
    changes: list[ConfigChange] = field(default_factory=list)
    change_summary: str = ""

    @property
    def total_changes(self) -> int:
        return len(self.changes)

    @property
    def high_impact_changes(self) -> list[ConfigChange]:
        return [c for c in self.changes if c.impact == "high"]

    def to_dict(self) -> dict:
        return {
            "baseline_id": self.baseline_id,
            "current_id": self.current_id,
            "baseline_hash": self.baseline_hash,
            "current_hash": self.current_hash,
            "configs_identical": self.configs_identical,
            "total_changes": self.total_changes,
            "high_impact_changes": len(self.high_impact_changes),
            "changes": [c.to_dict() for c in self.changes],
            "change_summary": self.change_summary,
        }


@dataclass
class VersionHistoryEntry:
    """One entry in the version history log."""
    fingerprint: RunFingerprint
    pass_rate: float = 0.0
    avg_score: float = 0.0
    critical_failures: int = 0
    summary_path: str = ""

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "results": {
                "pass_rate": round(self.pass_rate, 4),
                "avg_score": round(self.avg_score, 4),
                "critical_failures": self.critical_failures,
            },
            "summary_path": self.summary_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VersionHistoryEntry:
        results = data.get("results", {})
        return cls(
            fingerprint=RunFingerprint.from_dict(data.get("fingerprint", {})),
            pass_rate=results.get("pass_rate", 0.0),
            avg_score=results.get("avg_score", 0.0),
            critical_failures=results.get("critical_failures", 0),
            summary_path=data.get("summary_path", ""),
        )
