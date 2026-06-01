"""
Tests for Scenario Templates (P2 #9).

Tests cover:
- ScenarioTemplate model (creation, serialization, prompt injection)
- ScenarioLibrary (register, get, list, resolve, load built-in)
- Built-in scenarios (all 8 present, valid structure)
- ScenarioRunner (prompt enhancement, min/max turns, judge hints)
- Edge cases (empty, unknown, duplicates)
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scenarios import (
    ScenarioCategory,
    ScenarioDifficulty,
    ScenarioLibrary,
    ScenarioRunner,
    ScenarioTemplate,
    ScenarioTurnInstruction,
)
from src.scenarios.built_in import get_built_in_scenarios


# ─── Fixtures ───────────────────────────────────────────────

@pytest.fixture
def sample_scenario() -> ScenarioTemplate:
    """A minimal scenario for testing."""
    return ScenarioTemplate(
        id="test_scenario",
        name="Test Scenario",
        description="A test scenario for unit tests",
        category=ScenarioCategory.ROBUSTNESS,
        difficulty=ScenarioDifficulty.MEDIUM,
        min_turns=4,
        max_turns=10,
        setup_instructions="You are testing the bot's robustness.",
        turn_instructions=[
            ScenarioTurnInstruction(
                turn_number=1,
                instruction="Ask a normal question",
                expected_bot_behavior="Bot responds normally",
            ),
            ScenarioTurnInstruction(
                turn_number=2,
                instruction="Do something unexpected",
                expected_bot_behavior="Bot handles it gracefully",
            ),
            ScenarioTurnInstruction(
                turn_number=None,
                instruction="Stay in character throughout",
                expected_bot_behavior="Consistent behavior",
            ),
        ],
        success_criteria=["Bot handles unexpected input", "Bot stays helpful"],
        failure_indicators=["Bot crashes", "Bot gives wrong info"],
        judge_hints={"quality": "Check robustness", "safety": "No unsafe outputs"},
        tags=["test", "robustness"],
    )


@pytest.fixture
def library() -> ScenarioLibrary:
    """An empty scenario library."""
    return ScenarioLibrary()


@pytest.fixture
def loaded_library() -> ScenarioLibrary:
    """A library with built-in scenarios loaded."""
    lib = ScenarioLibrary()
    lib.load_built_in()
    return lib


@pytest.fixture
def runner(loaded_library) -> ScenarioRunner:
    """A runner with built-in scenarios."""
    return ScenarioRunner(loaded_library)


# ─── ScenarioTemplate Model Tests ───────────────────────────

class TestScenarioTemplate:
    def test_create_basic(self, sample_scenario):
        assert sample_scenario.id == "test_scenario"
        assert sample_scenario.name == "Test Scenario"
        assert sample_scenario.category == ScenarioCategory.ROBUSTNESS
        assert sample_scenario.difficulty == ScenarioDifficulty.MEDIUM
        assert sample_scenario.min_turns == 4
        assert sample_scenario.max_turns == 10

    def test_turn_instructions(self, sample_scenario):
        assert len(sample_scenario.turn_instructions) == 3
        assert sample_scenario.turn_instructions[0].turn_number == 1
        assert sample_scenario.turn_instructions[2].turn_number is None  # General instruction

    def test_to_system_prompt_injection(self, sample_scenario):
        prompt = sample_scenario.to_system_prompt_injection()

        assert "SCENARIO: Test Scenario" in prompt
        assert "Objective:" in prompt
        assert "Setup:" in prompt
        assert "Turn 1:" in prompt
        assert "Turn 2:" in prompt
        assert "General:" in prompt
        assert "The bot is doing well if it:" in prompt
        assert "Watch for these bot failures:" in prompt
        assert "END SCENARIO" in prompt

    def test_to_dict_and_back(self, sample_scenario):
        data = sample_scenario.to_dict()

        assert isinstance(data, dict)
        assert data["id"] == "test_scenario"
        assert data["category"] == "robustness"
        assert data["difficulty"] == "medium"
        assert len(data["turn_instructions"]) == 3

        # Roundtrip
        restored = ScenarioTemplate.from_dict(data)
        assert restored.id == sample_scenario.id
        assert restored.name == sample_scenario.name
        assert restored.category == sample_scenario.category
        assert len(restored.turn_instructions) == 3
        assert restored.turn_instructions[0].turn_number == 1

    def test_to_dict_json_serializable(self, sample_scenario):
        data = sample_scenario.to_dict()
        json_str = json.dumps(data)
        assert json_str  # No serialization errors

    def test_default_values(self):
        minimal = ScenarioTemplate(
            id="minimal",
            name="Minimal",
            description="Minimal scenario",
            category=ScenarioCategory.QUALITY,
            difficulty=ScenarioDifficulty.EASY,
        )
        assert minimal.min_turns == 5
        assert minimal.max_turns == 15
        assert minimal.turn_instructions == []
        assert minimal.success_criteria == []
        assert minimal.tags == []


# ─── ScenarioLibrary Tests ──────────────────────────────────

class TestScenarioLibrary:
    def test_register_and_get(self, library, sample_scenario):
        library.register(sample_scenario)
        retrieved = library.get("test_scenario")
        assert retrieved.id == "test_scenario"
        assert retrieved.name == "Test Scenario"

    def test_get_unknown_raises_key_error(self, library):
        with pytest.raises(KeyError, match="not found"):
            library.get("nonexistent")

    def test_has(self, library, sample_scenario):
        assert not library.has("test_scenario")
        library.register(sample_scenario)
        assert library.has("test_scenario")

    def test_list_all(self, library, sample_scenario):
        library.register(sample_scenario)
        all_scenarios = library.list_all()
        assert len(all_scenarios) == 1
        assert all_scenarios[0].id == "test_scenario"

    def test_list_ids(self, library, sample_scenario):
        library.register(sample_scenario)
        assert "test_scenario" in library.list_ids()

    def test_count(self, library, sample_scenario):
        assert library.count == 0
        library.register(sample_scenario)
        assert library.count == 1

    def test_list_by_category(self, loaded_library):
        safety = loaded_library.list_by_category(ScenarioCategory.SAFETY)
        assert len(safety) >= 1
        assert all(s.category == ScenarioCategory.SAFETY for s in safety)

    def test_list_by_difficulty(self, loaded_library):
        hard = loaded_library.list_by_difficulty(ScenarioDifficulty.HARD)
        assert len(hard) >= 1
        assert all(s.difficulty == ScenarioDifficulty.HARD for s in hard)

    def test_overwrite_warning(self, library, sample_scenario):
        library.register(sample_scenario)
        # Register same ID again — should overwrite
        modified = ScenarioTemplate(
            id="test_scenario",
            name="Modified",
            description="Modified",
            category=ScenarioCategory.QUALITY,
            difficulty=ScenarioDifficulty.EASY,
        )
        library.register(modified)
        assert library.get("test_scenario").name == "Modified"

    def test_load_from_dicts(self, library):
        dicts = [
            {
                "id": "custom_1",
                "name": "Custom 1",
                "description": "A custom scenario",
                "category": "quality",
                "difficulty": "easy",
                "turn_instructions": [
                    {"instruction": "Do something", "expected_bot_behavior": "Respond well"}
                ],
            }
        ]
        library.load_from_dicts(dicts)
        assert library.has("custom_1")
        s = library.get("custom_1")
        assert s.name == "Custom 1"
        assert len(s.turn_instructions) == 1


# ─── Scenario Resolution Tests ──────────────────────────────

class TestScenarioResolution:
    def test_resolve_all(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("all")
        assert len(ids) == 8

    def test_resolve_specific_ids(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("goal_shift,prompt_injection")
        assert ids == ["goal_shift", "prompt_injection"]

    def test_resolve_single_id(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("emotional_escalation")
        assert ids == ["emotional_escalation"]

    def test_resolve_by_category(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("safety")
        assert "prompt_injection" in ids

    def test_resolve_by_difficulty(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("hard")
        assert len(ids) >= 1
        for sid in ids:
            assert loaded_library.get(sid).difficulty == ScenarioDifficulty.HARD

    def test_resolve_mixed(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("goal_shift,safety")
        assert "goal_shift" in ids
        assert "prompt_injection" in ids

    def test_resolve_deduplication(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("goal_shift,goal_shift")
        assert ids == ["goal_shift"]

    def test_resolve_unknown_raises_error(self, loaded_library):
        with pytest.raises(ValueError, match="Unknown scenario"):
            loaded_library.resolve_scenario_ids("totally_fake")

    def test_resolve_whitespace_handled(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids(" goal_shift , prompt_injection ")
        assert ids == ["goal_shift", "prompt_injection"]


# ─── Built-in Scenarios Tests ────────────────────────────────

class TestBuiltInScenarios:
    def test_all_8_scenarios_present(self, loaded_library):
        assert loaded_library.count == 8

    def test_expected_ids_exist(self, loaded_library):
        expected = [
            "clarification_required",
            "goal_shift",
            "emotional_escalation",
            "prompt_injection",
            "out_of_scope",
            "multi_intent",
            "correction_loop",
            "context_retention",
        ]
        for sid in expected:
            assert loaded_library.has(sid), f"Missing scenario: {sid}"

    def test_all_have_descriptions(self, loaded_library):
        for s in loaded_library.list_all():
            assert len(s.description) > 20, f"{s.id} has too-short description"

    def test_all_have_turn_instructions(self, loaded_library):
        for s in loaded_library.list_all():
            assert len(s.turn_instructions) >= 2, f"{s.id} needs more turn instructions"

    def test_all_have_success_criteria(self, loaded_library):
        for s in loaded_library.list_all():
            assert len(s.success_criteria) >= 2, f"{s.id} needs success criteria"

    def test_all_have_failure_indicators(self, loaded_library):
        for s in loaded_library.list_all():
            assert len(s.failure_indicators) >= 2, f"{s.id} needs failure indicators"

    def test_all_have_judge_hints(self, loaded_library):
        for s in loaded_library.list_all():
            assert len(s.judge_hints) >= 1, f"{s.id} needs judge hints"

    def test_all_have_tags(self, loaded_library):
        for s in loaded_library.list_all():
            assert len(s.tags) >= 2, f"{s.id} needs tags"

    def test_min_turns_less_than_max(self, loaded_library):
        for s in loaded_library.list_all():
            assert s.min_turns < s.max_turns, f"{s.id}: min_turns >= max_turns"

    def test_categories_distributed(self, loaded_library):
        """Ensure scenarios cover multiple categories."""
        categories = set(s.category for s in loaded_library.list_all())
        assert len(categories) >= 4

    def test_difficulties_distributed(self, loaded_library):
        """Ensure scenarios cover multiple difficulty levels."""
        difficulties = set(s.difficulty for s in loaded_library.list_all())
        assert len(difficulties) >= 2

    def test_all_serializable(self, loaded_library):
        """Every built-in scenario should round-trip through JSON."""
        for s in loaded_library.list_all():
            data = s.to_dict()
            json_str = json.dumps(data)
            restored = ScenarioTemplate.from_dict(json.loads(json_str))
            assert restored.id == s.id
            assert restored.name == s.name

    def test_prompt_injection_is_safety_category(self, loaded_library):
        s = loaded_library.get("prompt_injection")
        assert s.category == ScenarioCategory.SAFETY

    def test_context_retention_is_hard(self, loaded_library):
        s = loaded_library.get("context_retention")
        assert s.difficulty == ScenarioDifficulty.HARD

    def test_emotional_escalation_is_empathy(self, loaded_library):
        s = loaded_library.get("emotional_escalation")
        assert s.category == ScenarioCategory.EMPATHY


# ─── Scenario Runner Tests ──────────────────────────────────

class TestScenarioRunner:
    def test_apply_scenario_enhances_prompt(self, runner):
        original = "You are a frustrated customer named Jane."
        enhanced = runner.apply_scenario_by_id(original, "goal_shift")

        assert original in enhanced
        assert "SCENARIO: Goal Shift" in enhanced
        assert "END SCENARIO" in enhanced

    def test_apply_preserves_original(self, runner):
        original = "Original persona prompt with specific details."
        enhanced = runner.apply_scenario_by_id(original, "clarification_required")

        assert enhanced.startswith(original)

    def test_apply_all_scenarios_produce_valid_prompts(self, runner, loaded_library):
        original = "You are a test user."
        for s in loaded_library.list_all():
            enhanced = runner.apply_scenario_to_prompt(original, s)
            assert original in enhanced
            assert f"SCENARIO: {s.name}" in enhanced
            assert len(enhanced) > len(original) + 50

    def test_get_min_turns(self, runner):
        min_t = runner.get_scenario_min_turns("context_retention")
        assert min_t >= 5

    def test_get_max_turns(self, runner):
        max_t = runner.get_scenario_max_turns("clarification_required")
        assert max_t >= 5

    def test_get_judge_hints(self, runner):
        hints = runner.get_judge_hints("prompt_injection")
        assert "safety" in hints
        assert len(hints["safety"]) > 10

    def test_unknown_scenario_raises(self, runner):
        with pytest.raises(KeyError):
            runner.apply_scenario_by_id("test prompt", "nonexistent_scenario")


# ─── Edge Cases ──────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_library(self):
        lib = ScenarioLibrary()
        assert lib.count == 0
        assert lib.list_all() == []
        assert lib.list_ids() == []

    def test_scenario_with_no_turn_instructions(self):
        s = ScenarioTemplate(
            id="bare",
            name="Bare",
            description="No turn instructions",
            category=ScenarioCategory.QUALITY,
            difficulty=ScenarioDifficulty.EASY,
        )
        prompt = s.to_system_prompt_injection()
        assert "SCENARIO: Bare" in prompt
        # Should not crash even with no instructions

    def test_scenario_with_empty_strings(self):
        s = ScenarioTemplate(
            id="empty",
            name="Empty",
            description="",
            category=ScenarioCategory.QUALITY,
            difficulty=ScenarioDifficulty.EASY,
            setup_instructions="",
        )
        prompt = s.to_system_prompt_injection()
        assert "SCENARIO: Empty" in prompt

    def test_get_built_in_returns_list(self):
        scenarios = get_built_in_scenarios()
        assert isinstance(scenarios, list)
        assert len(scenarios) == 8
        assert all(isinstance(s, ScenarioTemplate) for s in scenarios)

    def test_resolve_empty_string(self, loaded_library):
        ids = loaded_library.resolve_scenario_ids("")
        assert ids == []

    def test_from_dict_minimal(self):
        data = {
            "id": "min",
            "name": "Min",
            "description": "Minimal",
        }
        s = ScenarioTemplate.from_dict(data)
        assert s.id == "min"
        assert s.category == ScenarioCategory.ROBUSTNESS  # Default
        assert s.difficulty == ScenarioDifficulty.MEDIUM   # Default
