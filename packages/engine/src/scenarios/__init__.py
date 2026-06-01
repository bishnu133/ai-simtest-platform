"""
Scenario Templates — Structured, repeatable test patterns for AI bot testing.

Moves AI SimTest beyond random persona conversations into structured test
patterns that every QA engineer recognizes. Each scenario template defines
a specific conversational challenge that the bot must handle correctly.

Usage:
    from src.scenarios import ScenarioLibrary, ScenarioRunner

    library = ScenarioLibrary()
    library.load_built_in()

    # Get a specific scenario
    scenario = library.get("goal_shift")

    # Apply scenario to a persona's system prompt
    runner = ScenarioRunner(library)
    enhanced_prompt = runner.apply_scenario(persona, scenario)

CLI:
    simtest run --bot-endpoint http://... --scenarios clarification_required,goal_shift
    simtest run --bot-endpoint http://... --scenarios all
    simtest scenarios --list
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Enums ──────────────────────────────────────────────────

class ScenarioDifficulty(str, Enum):
    """How challenging the scenario is for the bot."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ScenarioCategory(str, Enum):
    """What dimension of bot behavior the scenario tests."""
    ROBUSTNESS = "robustness"       # Handles unexpected inputs
    SAFETY = "safety"               # Resists manipulation
    QUALITY = "quality"             # Maintains response quality
    MEMORY = "memory"               # Tracks context across turns
    EMPATHY = "empathy"             # Handles emotional users


# ─── Core Models ────────────────────────────────────────────

@dataclass
class ScenarioTurnInstruction:
    """
    Instruction for what the simulated user should do at a specific turn.

    If turn_number is None, the instruction applies generally.
    """
    turn_number: Optional[int]  # None = applies to any turn
    instruction: str            # What the user should do
    expected_bot_behavior: str  # What good bot behavior looks like


@dataclass
class ScenarioTemplate:
    """
    A structured test pattern that defines a specific conversational challenge.

    Each template includes:
    - What the scenario tests (category, difficulty)
    - Turn-by-turn instructions for the user simulator
    - What good/bad bot responses look like
    - Judge hints for evaluation
    """
    id: str                                          # Unique identifier (e.g., "goal_shift")
    name: str                                        # Human-readable name
    description: str                                 # What this scenario tests
    category: ScenarioCategory                       # Dimension being tested
    difficulty: ScenarioDifficulty                    # How hard for the bot
    min_turns: int = 5                               # Minimum turns needed
    max_turns: int = 15                              # Maximum turns allowed
    setup_instructions: str = ""                     # Initial context for the persona
    turn_instructions: list[ScenarioTurnInstruction] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)  # What PASS looks like
    failure_indicators: list[str] = field(default_factory=list) # What FAIL looks like
    judge_hints: dict[str, str] = field(default_factory=dict)  # Extra context for judges
    tags: list[str] = field(default_factory=list)    # Searchable tags

    def to_system_prompt_injection(self) -> str:
        """
        Generate the text to inject into a persona's system prompt
        to make them follow this scenario.
        """
        lines = []
        lines.append(f"\n--- SCENARIO: {self.name} ---")
        lines.append(f"Objective: {self.description}")
        lines.append("")

        if self.setup_instructions:
            lines.append(f"Setup: {self.setup_instructions}")
            lines.append("")

        if self.turn_instructions:
            lines.append("Turn-by-turn guide:")
            for ti in self.turn_instructions:
                if ti.turn_number is not None:
                    lines.append(f"  Turn {ti.turn_number}: {ti.instruction}")
                else:
                    lines.append(f"  General: {ti.instruction}")
            lines.append("")

        if self.success_criteria:
            lines.append("The bot is doing well if it:")
            for sc in self.success_criteria:
                lines.append(f"  - {sc}")
            lines.append("")

        if self.failure_indicators:
            lines.append("Watch for these bot failures:")
            for fi in self.failure_indicators:
                lines.append(f"  - {fi}")

        lines.append("--- END SCENARIO ---\n")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "difficulty": self.difficulty.value,
            "min_turns": self.min_turns,
            "max_turns": self.max_turns,
            "setup_instructions": self.setup_instructions,
            "turn_instructions": [
                {
                    "turn_number": ti.turn_number,
                    "instruction": ti.instruction,
                    "expected_bot_behavior": ti.expected_bot_behavior,
                }
                for ti in self.turn_instructions
            ],
            "success_criteria": self.success_criteria,
            "failure_indicators": self.failure_indicators,
            "judge_hints": self.judge_hints,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ScenarioTemplate:
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            category=ScenarioCategory(data.get("category", "robustness")),
            difficulty=ScenarioDifficulty(data.get("difficulty", "medium")),
            min_turns=data.get("min_turns", 5),
            max_turns=data.get("max_turns", 15),
            setup_instructions=data.get("setup_instructions", ""),
            turn_instructions=[
                ScenarioTurnInstruction(
                    turn_number=ti.get("turn_number"),
                    instruction=ti["instruction"],
                    expected_bot_behavior=ti.get("expected_bot_behavior", ""),
                )
                for ti in data.get("turn_instructions", [])
            ],
            success_criteria=data.get("success_criteria", []),
            failure_indicators=data.get("failure_indicators", []),
            judge_hints=data.get("judge_hints", {}),
            tags=data.get("tags", []),
        )


# ─── Scenario Library (Registry) ───────────────────────────

class ScenarioLibrary:
    """
    Registry of available scenario templates.

    Supports built-in scenarios and custom user-defined scenarios.
    """

    def __init__(self):
        self._scenarios: dict[str, ScenarioTemplate] = {}

    def register(self, scenario: ScenarioTemplate) -> None:
        """Register a scenario template."""
        if scenario.id in self._scenarios:
            logger.warning(f"Overwriting scenario: {scenario.id}")
        self._scenarios[scenario.id] = scenario
        logger.debug(f"Registered scenario: {scenario.id}")

    def get(self, scenario_id: str) -> ScenarioTemplate:
        """Get a scenario by ID. Raises KeyError if not found."""
        if scenario_id not in self._scenarios:
            available = ", ".join(sorted(self._scenarios.keys()))
            raise KeyError(
                f"Scenario '{scenario_id}' not found. Available: {available}"
            )
        return self._scenarios[scenario_id]

    def list_all(self) -> list[ScenarioTemplate]:
        """List all registered scenarios."""
        return list(self._scenarios.values())

    def list_by_category(self, category: ScenarioCategory) -> list[ScenarioTemplate]:
        """List scenarios filtered by category."""
        return [s for s in self._scenarios.values() if s.category == category]

    def list_by_difficulty(self, difficulty: ScenarioDifficulty) -> list[ScenarioTemplate]:
        """List scenarios filtered by difficulty."""
        return [s for s in self._scenarios.values() if s.difficulty == difficulty]

    def list_ids(self) -> list[str]:
        """List all scenario IDs."""
        return sorted(self._scenarios.keys())

    def has(self, scenario_id: str) -> bool:
        """Check if a scenario exists."""
        return scenario_id in self._scenarios

    @property
    def count(self) -> int:
        return len(self._scenarios)

    def load_built_in(self) -> None:
        """Load all built-in scenario templates."""
        from src.scenarios.built_in import get_built_in_scenarios
        for scenario in get_built_in_scenarios():
            self.register(scenario)
        logger.info(f"Loaded {self.count} built-in scenarios")

    def load_from_dicts(self, scenario_dicts: list[dict]) -> None:
        """Load scenarios from a list of dictionaries (e.g., from JSON file)."""
        for data in scenario_dicts:
            scenario = ScenarioTemplate.from_dict(data)
            self.register(scenario)

    def resolve_scenario_ids(self, scenario_input: str) -> list[str]:
        """
        Resolve a comma-separated scenario input string.

        Supports:
          - "all" → all registered scenario IDs
          - "goal_shift,prompt_injection" → specific IDs
          - "robustness" → all scenarios in that category

        Returns list of valid scenario IDs.
        Raises ValueError for unknown IDs.
        """
        if scenario_input.strip().lower() == "all":
            return self.list_ids()

        requested = [s.strip() for s in scenario_input.split(",") if s.strip()]
        resolved = []

        for item in requested:
            # Check if it's a category name
            try:
                category = ScenarioCategory(item.lower())
                cat_ids = [s.id for s in self.list_by_category(category)]
                resolved.extend(cat_ids)
                continue
            except ValueError:
                pass

            # Check if it's a difficulty
            try:
                difficulty = ScenarioDifficulty(item.lower())
                diff_ids = [s.id for s in self.list_by_difficulty(difficulty)]
                resolved.extend(diff_ids)
                continue
            except ValueError:
                pass

            # Must be a scenario ID
            if not self.has(item):
                available = ", ".join(self.list_ids())
                raise ValueError(
                    f"Unknown scenario: '{item}'. Available: {available}"
                )
            resolved.append(item)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for sid in resolved:
            if sid not in seen:
                seen.add(sid)
                unique.append(sid)

        return unique


# ─── Scenario Runner ────────────────────────────────────────

class ScenarioRunner:
    """
    Applies scenario templates to personas by enhancing their system prompts.

    The runner doesn't modify the conversation simulator — it only enriches
    the persona's instructions so the LLM-based user simulator follows
    the scenario pattern naturally.
    """

    def __init__(self, library: ScenarioLibrary):
        self.library = library

    def apply_scenario_to_prompt(
        self,
        original_system_prompt: str,
        scenario: ScenarioTemplate,
    ) -> str:
        """
        Inject scenario instructions into a persona's system prompt.

        Args:
            original_system_prompt: The persona's existing system prompt.
            scenario: The scenario template to apply.

        Returns:
            Enhanced system prompt with scenario instructions appended.
        """
        injection = scenario.to_system_prompt_injection()
        return f"{original_system_prompt}\n{injection}"

    def apply_scenario_by_id(
        self,
        original_system_prompt: str,
        scenario_id: str,
    ) -> str:
        """Apply a scenario by its ID."""
        scenario = self.library.get(scenario_id)
        return self.apply_scenario_to_prompt(original_system_prompt, scenario)

    def get_scenario_min_turns(self, scenario_id: str) -> int:
        """Get the minimum turns needed for a scenario."""
        return self.library.get(scenario_id).min_turns

    def get_scenario_max_turns(self, scenario_id: str) -> int:
        """Get the maximum turns for a scenario."""
        return self.library.get(scenario_id).max_turns

    def get_judge_hints(self, scenario_id: str) -> dict[str, str]:
        """Get judge-specific hints for evaluating this scenario."""
        return self.library.get(scenario_id).judge_hints