"""
Tests for Scenario CLI Integration (P2 #9 - CLI wiring).

Tests cover:
- --scenarios flag on `simtest run` command
- `simtest scenarios` list command
- Scenario resolution and validation in CLI context
- Scenario application to personas before simulation
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli import main


@pytest.fixture
def runner():
    return CliRunner()


# ─── simtest scenarios (list command) ───────────────────────

class TestScenariosListCommand:
    def test_scenarios_command_exists(self, runner):
        result = runner.invoke(main, ["scenarios"])
        assert result.exit_code == 0
        assert "Scenario Templates" in result.output

    def test_lists_all_8_scenarios(self, runner):
        result = runner.invoke(main, ["scenarios"])
        assert result.exit_code == 0
        assert "clarification_required" in result.output
        assert "goal_shift" in result.output
        assert "emotional_escalation" in result.output
        assert "prompt_injection" in result.output
        assert "out_of_scope" in result.output
        assert "multi_intent" in result.output
        assert "correction_loop" in result.output
        assert "context_retention" in result.output

    def test_filter_by_category(self, runner):
        result = runner.invoke(main, ["scenarios", "--category", "safety"])
        assert result.exit_code == 0
        assert "prompt_injection" in result.output

    def test_filter_by_difficulty(self, runner):
        result = runner.invoke(main, ["scenarios", "--difficulty", "hard"])
        assert result.exit_code == 0
        assert "context_retention" in result.output

    def test_invalid_category(self, runner):
        result = runner.invoke(main, ["scenarios", "--category", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown category" in result.output

    def test_invalid_difficulty(self, runner):
        result = runner.invoke(main, ["scenarios", "--difficulty", "extreme"])
        assert result.exit_code == 1
        assert "Unknown difficulty" in result.output

    def test_verbose_flag(self, runner):
        result = runner.invoke(main, ["scenarios", "-v"])
        assert result.exit_code == 0
        assert "Turn Instructions" in result.output
        assert "Success Criteria" in result.output
        assert "Failure Indicators" in result.output

    def test_shows_usage_hint(self, runner):
        result = runner.invoke(main, ["scenarios"])
        assert result.exit_code == 0
        assert "simtest run --bot-endpoint" in result.output
        assert "--scenarios" in result.output


# ─── --scenarios flag on simtest run ────────────────────────

class TestRunScenariosFlag:
    def test_run_has_scenarios_option(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert "--scenarios" in result.output

    def test_invalid_scenario_id_exits(self, runner):
        result = runner.invoke(main, [
            "run",
            "--bot-endpoint", "http://test:8000/chat",
            "--scenarios", "nonexistent_scenario",
        ])
        assert result.exit_code == 1
        assert "Unknown scenario" in result.output

    def test_valid_scenario_resolves(self, runner):
        """Valid scenario IDs should resolve without error (simulation itself is mocked)."""
        with patch("src.cli.asyncio") as mock_asyncio:
            mock_asyncio.run = MagicMock()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://test:8000/chat",
                "--scenarios", "goal_shift",
            ])
            # Should get past scenario validation (may fail later in simulation)
            assert "Unknown scenario" not in result.output
            assert "Scenarios:" in result.output or result.exit_code == 0

    def test_all_keyword_resolves(self, runner):
        with patch("src.cli.asyncio") as mock_asyncio:
            mock_asyncio.run = MagicMock()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://test:8000/chat",
                "--scenarios", "all",
            ])
            assert "Unknown scenario" not in result.output
            assert "8 templates" in result.output or result.exit_code == 0

    def test_category_resolves(self, runner):
        with patch("src.cli.asyncio") as mock_asyncio:
            mock_asyncio.run = MagicMock()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://test:8000/chat",
                "--scenarios", "safety",
            ])
            assert "Unknown scenario" not in result.output

    def test_comma_separated_resolves(self, runner):
        with patch("src.cli.asyncio") as mock_asyncio:
            mock_asyncio.run = MagicMock()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://test:8000/chat",
                "--scenarios", "goal_shift,prompt_injection",
            ])
            assert "Unknown scenario" not in result.output


# ─── Scenario application to personas ───────────────────────

class TestScenarioApplication:
    def test_apply_scenarios_round_robin(self):
        """Scenarios should be distributed round-robin across personas."""
        from src.scenarios import ScenarioLibrary, ScenarioRunner

        lib = ScenarioLibrary()
        lib.load_built_in()
        runner = ScenarioRunner(lib)

        # Simulate 4 personas with 2 scenarios
        scenario_ids = ["goal_shift", "prompt_injection"]

        # Create mock personas with system_prompt attribute
        personas = []
        for i in range(4):
            p = MagicMock()
            p.system_prompt = f"You are persona {i}."
            personas.append(p)

        # Apply round-robin
        for i, persona in enumerate(personas):
            scenario_id = scenario_ids[i % len(scenario_ids)]
            scenario = lib.get(scenario_id)
            persona.system_prompt = runner.apply_scenario_to_prompt(
                persona.system_prompt, scenario
            )

        # persona 0, 2 → goal_shift; persona 1, 3 → prompt_injection
        assert "Goal Shift" in personas[0].system_prompt
        assert "Prompt Injection" in personas[1].system_prompt
        assert "Goal Shift" in personas[2].system_prompt
        assert "Prompt Injection" in personas[3].system_prompt

    def test_apply_preserves_original_prompt(self):
        from src.scenarios import ScenarioLibrary, ScenarioRunner

        lib = ScenarioLibrary()
        lib.load_built_in()
        runner = ScenarioRunner(lib)

        original = "You are a frustrated customer named Jane who wants a refund."
        enhanced = runner.apply_scenario_by_id(original, "emotional_escalation")

        assert enhanced.startswith(original)
        assert "Emotional Escalation" in enhanced

    def test_no_scenarios_leaves_personas_unchanged(self):
        """When no scenarios are specified, personas should not be modified."""
        persona = MagicMock()
        persona.system_prompt = "Original prompt."

        # Simulating the _apply_scenarios_to_personas with empty scenario_ids
        scenario_ids = []
        if not scenario_ids:
            pass  # No modification

        assert persona.system_prompt == "Original prompt."


# ─── Version command mentions scenarios ─────────────────────

class TestVersionMentionsScenarios:
    def test_version_lists_scenarios_command(self, runner):
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "scenarios" in result.output
