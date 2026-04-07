"""
Multi-Model Comparison — Run Plan & Persona Reuse (Phase 3A).

Provides:
- FairnessControls: Temperature, seed, deterministic overrides
- ExperimentMetadata: Name, version, tags for experiment tracking
- RunPlan: Frozen immutable execution blueprint (shared personas,
  endpoint list, scenario assignments, fairness, experiment metadata)
- PlanBuilder: Constructs a RunPlan from config + generated personas

Design principles:
- Plan is frozen after creation — no mutation during execution
- Personas are deep-copied into the plan, isolating from source
- SHA-256 plan_hash enforced for checkpoint resume validation
- Zero changes to existing SimulationOrchestrator or PersonaGenerator

Addresses review points:
- #1: Plan immutability via frozen Pydantic + deep copy + plan_hash
- Medium #1: Plan export — plan serializes to plan.json for reproducibility
- Medium #2: Endpoint tags flow through from ModelSpec
- #10: Fairness controls (temperature, seed, deterministic mode)
- #8: Experiment tracking metadata
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ConfigDict

from src.multi_compare.models import (
    ModelSpec,
    MultiCompareConfig,
    ComparisonSettings,
    RunManifest,
)


# ─────────────────────────────────────────────────────────────
# Fairness Controls
# ─────────────────────────────────────────────────────────────

class FairnessControls(BaseModel):
    """
    Controls for fair cross-model comparison.

    When set, overrides per-endpoint settings to ensure
    identical conditions across all models.
    """
    model_config = ConfigDict(frozen=True)

    deterministic: bool = Field(
        default=False,
        description="Request deterministic responses where supported",
    )
    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0,
        description="Override temperature for all models (None = use model default)",
    )
    seed: int | None = Field(
        default=None,
        description="Fixed seed passed to models that support it",
    )

    def to_adapter_overrides(self) -> dict[str, Any]:
        """
        Generate adapter-level overrides from fairness controls.

        Returns dict that can be merged into adapter metadata.
        """
        overrides: dict[str, Any] = {}
        if self.temperature is not None:
            overrides["temperature"] = self.temperature
        if self.seed is not None:
            overrides["seed"] = self.seed
        if self.deterministic:
            if self.temperature is None:
                overrides["temperature"] = 0.0
            if self.seed is None:
                overrides["seed"] = 42
        return overrides


# ─────────────────────────────────────────────────────────────
# Experiment Metadata
# ─────────────────────────────────────────────────────────────

class ExperimentMetadata(BaseModel):
    """
    Optional experiment tracking metadata.

    Stored in plan, checkpoints, and final result JSON.
    Future hook for W&B/LangSmith/MLflow export.
    """
    model_config = ConfigDict(frozen=True)

    name: str = Field(
        default="",
        description="Experiment name (e.g., 'gpt4_vs_claude_q1')",
    )
    version: str = Field(
        default="",
        description="Experiment version (e.g., 'v1.2')",
    )
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary tags (e.g., team, sprint, purpose)",
    )
    description: str = Field(default="")

    @property
    def is_set(self) -> bool:
        return bool(self.name)


# ─────────────────────────────────────────────────────────────
# Execution Settings (from YAML + review points)
# ─────────────────────────────────────────────────────────────

class RetryPolicy(BaseModel):
    """Per-endpoint retry configuration."""
    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff: str = Field(
        default="exponential",
        pattern="^(exponential|fixed)$",
    )
    initial_delay: float = Field(default=5.0, ge=0.5, le=60.0)


class ExecutionSettings(BaseModel):
    """Controls how endpoints are executed."""
    model_config = ConfigDict(frozen=True)

    strategy: str = Field(
        default="parallel",
        pattern="^(parallel|sequential)$",
    )
    max_parallel: int = Field(default=3, ge=1, le=10)
    delay_between: float = Field(
        default=0.0, ge=0.0,
        description="Seconds between sequential runs",
    )
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    model_timeout: int = Field(
        default=600, ge=30, le=7200,
        description="Total seconds per endpoint run before kill",
    )
    allow_partial_results: bool = Field(
        default=True,
        description="Allow comparison with incomplete results",
    )
    min_completion_ratio: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum fraction of endpoints that must complete",
    )


class BudgetSettings(BaseModel):
    """Global budget guard across all endpoints."""
    model_config = ConfigDict(frozen=True)

    total_limit: float | None = Field(
        default=None, ge=0.0,
        description="Max USD across ALL models (None = unlimited)",
    )
    mode: str = Field(
        default="soft",
        pattern="^(soft|hard)$",
    )


# ─────────────────────────────────────────────────────────────
# Serialized Persona (for frozen plan storage)
# ─────────────────────────────────────────────────────────────

class SerializedPersona(BaseModel):
    """
    Minimal persona snapshot stored in the plan.

    Deep-copied from source Persona objects. Contains only
    the fields needed for identification and scenario assignment.
    Full Persona objects are reconstructed at execution time.
    """
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    persona_type: str
    role: str = ""
    technical_level: str = "intermediate"
    goals: list[str] = Field(default_factory=list)
    tone: str = "neutral"
    scenario_id: str | None = Field(
        default=None,
        description="Assigned scenario slug (from round-robin)",
    )


# ─────────────────────────────────────────────────────────────
# Run Plan (Frozen)
# ─────────────────────────────────────────────────────────────

class RunPlan(BaseModel):
    """
    Frozen immutable execution blueprint for multi-model comparison.

    Once created by PlanBuilder, a RunPlan cannot be modified.
    It contains everything needed to execute or resume a comparison.

    Immutability enforcement:
    - Pydantic frozen=True prevents attribute mutation
    - Personas are deep-copied from source (no shared references)
    - plan_hash validates integrity on checkpoint resume
    """
    model_config = ConfigDict(frozen=True)

    # Identity
    plan_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z",
    )

    # Config snapshot
    config_name: str = Field(default="Multi-Model Comparison")
    config_description: str = Field(default="")

    # Endpoints (serialized from ModelSpec, api_key excluded)
    endpoint_ids: list[str] = Field(
        ..., min_length=2,
        description="Ordered list of model IDs to execute",
    )
    endpoint_configs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="model_id -> serialized ModelSpec (secrets redacted)",
    )

    # Personas (deep-copied, frozen)
    personas: list[SerializedPersona] = Field(
        ..., min_length=1,
        description="Shared persona set for all endpoints",
    )
    persona_set_hash: str = Field(
        default="",
        description="SHA-256 of serialized persona set",
    )

    # Scenario assignments
    scenario_assignments: dict[str, str] = Field(
        default_factory=dict,
        description="persona_id -> scenario_id mapping",
    )
    scenario_assignment_hash: str = Field(default="")

    # Simulation settings (from ComparisonSettings)
    max_turns: int = Field(default=8)
    min_turns: int = Field(default=1)
    pass_threshold: float = Field(default=0.7)
    warn_threshold: float = Field(default=0.5)
    parallel_per_model: int = Field(default=3)

    # Review-driven additions
    fairness: FairnessControls = Field(default_factory=FairnessControls)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    experiment: ExperimentMetadata = Field(default_factory=ExperimentMetadata)

    # Comparison mode
    comparison_mode: str = Field(default="champion_challenger")
    champion_id: str | None = Field(default=None)
    decision_profile: str = Field(default="balanced")

    # Integrity
    plan_hash: str = Field(
        default="",
        description="SHA-256 of the plan's canonical JSON (set by PlanBuilder)",
    )

    @staticmethod
    def compute_plan_hash(plan_data: dict[str, Any]) -> str:
        """
        Compute deterministic SHA-256 hash of plan data.

        Excludes plan_hash and plan_id (circular) and created_at (non-deterministic).
        """
        hashable = {k: v for k, v in plan_data.items()
                    if k not in ("plan_hash", "plan_id", "created_at")}
        canonical = json.dumps(hashable, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def validate_integrity(self) -> bool:
        """Verify plan_hash matches current content."""
        if not self.plan_hash:
            return False
        data = self.model_dump(mode="json")
        expected = self.compute_plan_hash(data)
        return self.plan_hash == expected


# ─────────────────────────────────────────────────────────────
# Plan Builder
# ─────────────────────────────────────────────────────────────

class PlanBuilder:
    """
    Constructs a RunPlan from a MultiCompareConfig and generated personas.

    Workflow:
    1. Accept validated config + persona list
    2. Deep-copy personas into SerializedPersona objects
    3. Assign scenarios via round-robin
    4. Serialize endpoint configs (redacted)
    5. Compute all hashes
    6. Return frozen RunPlan

    Does NOT call PersonaGenerator — that's the orchestrator's job.
    PlanBuilder only packages the results.
    """

    @staticmethod
    def build(
        config: MultiCompareConfig,
        personas: list[Any],
        scenario_ids: list[str] | None = None,
        fairness: FairnessControls | None = None,
        execution: ExecutionSettings | None = None,
        budget: BudgetSettings | None = None,
        experiment: ExperimentMetadata | None = None,
    ) -> RunPlan:
        """
        Build a frozen RunPlan from config and personas.

        Args:
            config: Validated MultiCompareConfig.
            personas: List of Persona objects (will be deep-copied).
            scenario_ids: Optional scenario slugs for round-robin assignment.
            fairness: Fairness controls override.
            execution: Execution settings override.
            budget: Budget settings override.
            experiment: Experiment metadata.

        Returns:
            Frozen RunPlan with computed hashes.
        """
        # Deep-copy personas to prevent mutation
        serialized_personas = PlanBuilder._serialize_personas(personas)

        # Compute persona set hash
        persona_json = json.dumps(
            [p.model_dump(mode="json") for p in serialized_personas],
            sort_keys=True,
        )
        persona_set_hash = RunManifest.compute_hash(persona_json)

        # Assign scenarios via round-robin
        scenario_assignments: dict[str, str] = {}
        if scenario_ids:
            for i, persona in enumerate(serialized_personas):
                assigned = scenario_ids[i % len(scenario_ids)]
                # Create new persona with scenario set
                serialized_personas[i] = persona.model_copy(
                    update={"scenario_id": assigned}
                )
                scenario_assignments[persona.id] = assigned

        scenario_hash = ""
        if scenario_assignments:
            scenario_hash = RunManifest.compute_hash(
                json.dumps(scenario_assignments, sort_keys=True)
            )

        # Serialize endpoint configs (redacted)
        from src.multi_compare.config_loader import redact_model_spec
        endpoint_configs = {
            spec.id: redact_model_spec(spec)
            for spec in config.models
        }

        # Build plan data dict first (to compute hash)
        plan_data = dict(
            config_name=config.name,
            config_description=config.description,
            endpoint_ids=[m.id for m in config.models],
            endpoint_configs=endpoint_configs,
            personas=[p.model_dump(mode="json") for p in serialized_personas],
            persona_set_hash=persona_set_hash,
            scenario_assignments=scenario_assignments,
            scenario_assignment_hash=scenario_hash,
            max_turns=config.settings.max_turns,
            min_turns=config.settings.min_turns,
            pass_threshold=config.settings.pass_threshold,
            warn_threshold=config.settings.warn_threshold,
            parallel_per_model=config.settings.parallel,
            fairness=(fairness or FairnessControls()).model_dump(mode="json"),
            execution=(execution or ExecutionSettings()).model_dump(mode="json"),
            budget=(budget or BudgetSettings()).model_dump(mode="json"),
            experiment=(experiment or ExperimentMetadata()).model_dump(mode="json"),
            comparison_mode=config.mode.value,
            champion_id=config.champion,
            decision_profile=config.decision_profile,
        )

        plan_hash = RunPlan.compute_plan_hash(plan_data)

        return RunPlan(
            config_name=config.name,
            config_description=config.description,
            endpoint_ids=[m.id for m in config.models],
            endpoint_configs=endpoint_configs,
            personas=serialized_personas,
            persona_set_hash=persona_set_hash,
            scenario_assignments=scenario_assignments,
            scenario_assignment_hash=scenario_hash,
            max_turns=config.settings.max_turns,
            min_turns=config.settings.min_turns,
            pass_threshold=config.settings.pass_threshold,
            warn_threshold=config.settings.warn_threshold,
            parallel_per_model=config.settings.parallel,
            fairness=fairness or FairnessControls(),
            execution=execution or ExecutionSettings(),
            budget=budget or BudgetSettings(),
            experiment=experiment or ExperimentMetadata(),
            comparison_mode=config.mode.value,
            champion_id=config.champion,
            decision_profile=config.decision_profile,
            plan_hash=plan_hash,
        )

    @staticmethod
    def _serialize_personas(personas: list[Any]) -> list[SerializedPersona]:
        """
        Deep-copy and serialize personas into frozen plan format.

        Accepts any object with persona-like attributes.
        """
        result = []
        for p in personas:
            # Deep copy to isolate from source
            p_copy = copy.deepcopy(p)

            # Extract fields defensively
            persona_id = getattr(p_copy, "id", str(uuid.uuid4()))
            name = getattr(p_copy, "name", f"Persona {len(result) + 1}")
            persona_type = getattr(p_copy, "persona_type", None)
            if persona_type and hasattr(persona_type, "value"):
                persona_type = persona_type.value
            else:
                persona_type = str(persona_type or "standard")

            role = getattr(p_copy, "role", "user")
            tech_level = getattr(p_copy, "technical_level", None)
            if tech_level and hasattr(tech_level, "value"):
                tech_level = tech_level.value
            else:
                tech_level = str(tech_level or "intermediate")

            goals = list(getattr(p_copy, "goals", []))
            tone = getattr(p_copy, "tone", "neutral")

            result.append(SerializedPersona(
                id=str(persona_id),
                name=name,
                persona_type=persona_type,
                role=role,
                technical_level=tech_level,
                goals=goals,
                tone=tone,
            ))

        return result


# ─────────────────────────────────────────────────────────────
# Plan I/O
# ─────────────────────────────────────────────────────────────

def save_plan(plan: RunPlan, output_dir: str | Path) -> Path:
    """Save the plan to output_dir/plan.json."""
    from src.multi_compare.security import SafeSerializer

    output_path = Path(output_dir) / "plan.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = plan.model_dump(mode="json")
    SafeSerializer.write_json(data, output_path)
    return output_path


def load_plan(path: str | Path) -> RunPlan:
    """
    Load a plan from plan.json.

    Validates integrity after loading.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If plan hash doesn't match (tampered/corrupted).
    """
    plan_path = Path(path)
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = RunPlan(**data)

    if plan.plan_hash and not plan.validate_integrity():
        raise ValueError(
            f"Plan integrity check failed. The plan file may have been "
            f"modified after creation. Use --force-restart to create a new plan."
        )

    return plan
