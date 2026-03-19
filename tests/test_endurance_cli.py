"""
CLI Integration Tests for Context Endurance / Memory Stress Testing (P2 #10).

Tests cover:
- --stress-memory flag recognition and parsing
- --stress-turns parameter handling
- --stress-patterns parameter (comma-separated filter)
- --stress-facts / --stress-contradictions overrides
- EnduranceConfig construction from CLI args
- Runner creation from CLI config
- Combined usage with scenario templates
- simtest stress info data
"""

import pytest

from src.endurance import (
    BUILT_IN_COMPLEXITY_LEVELS,
    BUILT_IN_CONTRADICTIONS,
    BUILT_IN_FACTS,
    EnduranceConfig,
    EnduranceRunner,
    StressPattern,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper: Simulates CLI → EnduranceConfig mapping (mirrors cli.py logic)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_endurance_config_from_cli(
    stress_memory: bool = False,
    stress_turns: int = 30,
    stress_patterns: str = "",
    stress_facts: int = 5,
    stress_contradictions: int = 3,
) -> EnduranceConfig | None:
    """
    Simulates CLI → EnduranceConfig mapping.
    This mirrors what cli.py does when processing --stress-memory.
    """
    if not stress_memory:
        return None

    # Parse patterns
    if stress_patterns:
        pattern_map = {
            "fact_seeding": StressPattern.FACT_SEEDING,
            "contradiction": StressPattern.CONTRADICTION,
            "progressive_complexity": StressPattern.PROGRESSIVE_COMPLEXITY,
        }
        patterns = []
        for p in stress_patterns.split(","):
            p = p.strip().lower()
            if p in pattern_map:
                patterns.append(pattern_map[p])
        if not patterns:
            patterns = list(StressPattern)
    else:
        patterns = list(StressPattern)

    # Calculate windows based on target turns
    seed_end = max(3, stress_turns // 6)
    recall_start = max(seed_end + 3, stress_turns // 2)
    recall_end = min(stress_turns - 2, int(stress_turns * 0.85))

    return EnduranceConfig(
        patterns=patterns,
        target_turns=stress_turns,
        num_facts=stress_facts,
        seed_window=(1, seed_end),
        recall_window=(recall_start, recall_end),
        num_contradictions=stress_contradictions,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI Flag Parsing Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCLIFlagParsing:
    def test_stress_memory_disabled_returns_none(self):
        config = build_endurance_config_from_cli(stress_memory=False)
        assert config is None

    def test_stress_memory_enabled_returns_config(self):
        config = build_endurance_config_from_cli(stress_memory=True)
        assert isinstance(config, EnduranceConfig)
        assert config.target_turns == 30

    def test_stress_turns_40_sets_target(self):
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=40)
        assert config.target_turns == 40

    def test_stress_turns_50_adjusts_windows(self):
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=50)
        assert config.seed_window[1] <= 50
        assert config.recall_window[0] > config.seed_window[1]
        assert config.recall_window[1] <= 50

    def test_stress_patterns_single(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_patterns="fact_seeding"
        )
        assert config.patterns == [StressPattern.FACT_SEEDING]

    def test_stress_patterns_multiple(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_patterns="fact_seeding,contradiction"
        )
        assert StressPattern.FACT_SEEDING in config.patterns
        assert StressPattern.CONTRADICTION in config.patterns
        assert StressPattern.PROGRESSIVE_COMPLEXITY not in config.patterns

    def test_stress_patterns_empty_uses_all(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_patterns=""
        )
        assert len(config.patterns) == 3

    def test_stress_patterns_invalid_uses_all(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_patterns="nonexistent,fake"
        )
        assert len(config.patterns) == 3  # Falls back to all

    def test_stress_facts_overrides_count(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_facts=8
        )
        assert config.num_facts == 8

    def test_stress_contradictions_overrides_count(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_contradictions=5
        )
        assert config.num_contradictions == 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Config Validation from CLI Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCLIConfigValidation:
    def test_default_cli_args_produce_valid_config(self):
        config = build_endurance_config_from_cli(stress_memory=True)
        issues = config.validate()
        assert issues == [], f"Validation issues: {issues}"

    def test_stress_turns_15_produces_valid_config(self):
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=15)
        issues = config.validate()
        assert issues == [], f"Validation issues: {issues}"

    def test_stress_turns_50_produces_valid_config(self):
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=50)
        issues = config.validate()
        assert issues == [], f"Validation issues: {issues}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Runner from CLI Config Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRunnerFromCLI:
    def test_runner_creates_from_cli_config(self):
        config = build_endurance_config_from_cli(stress_memory=True)
        runner = EnduranceRunner(config)
        assert runner.get_min_turns() == 30

    def test_runner_with_custom_turns(self):
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=40)
        runner = EnduranceRunner(config)
        assert runner.get_min_turns() == 40

    def test_runner_applies_to_persona_prompt(self):
        config = build_endurance_config_from_cli(stress_memory=True)
        runner = EnduranceRunner(config)
        enhanced = runner.apply_to_persona_prompt("You are a customer.")
        assert "MEMORY STRESS TEST" in enhanced
        assert "You are a customer." in enhanced

    def test_runner_single_pattern_from_cli(self):
        config = build_endurance_config_from_cli(
            stress_memory=True, stress_patterns="contradiction"
        )
        runner = EnduranceRunner(config)
        enhanced = runner.apply_to_persona_prompt("Test.")
        assert "CONTRADICTION" in enhanced
        # Should NOT have fact seeding instructions
        assert "MEMORY SEED" not in enhanced

    def test_runner_min_turns_overrides_default(self):
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=40)
        runner = EnduranceRunner(config)
        assert runner.get_min_turns() >= 40


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Combined with Scenarios Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCombinedWithScenarios:
    def test_stress_memory_prompt_appends_after_scenario(self):
        """Verify stress instructions can be added to a prompt that already has scenario instructions."""
        scenario_prompt = (
            "You are a frustrated customer.\n"
            "SCENARIO: Goal Shift\n"
            "Turn 1: Start with billing question.\n"
            "Turn 3: Switch topic to returns.\n"
            "END SCENARIO"
        )
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=20)
        runner = EnduranceRunner(config)
        enhanced = runner.apply_to_persona_prompt(scenario_prompt)

        # Both should be present
        assert "SCENARIO: Goal Shift" in enhanced
        assert "MEMORY STRESS TEST INSTRUCTIONS" in enhanced
        assert "END MEMORY STRESS TEST INSTRUCTIONS" in enhanced

    def test_stress_and_scenarios_no_turn_conflict(self):
        """Stress instructions use their own turn numbering that works alongside scenarios."""
        config = build_endurance_config_from_cli(stress_memory=True, stress_turns=20)
        runner = EnduranceRunner(config)
        all_insts = runner.get_all_instructions()
        for turn in all_insts:
            assert 1 <= turn <= 20, f"Turn {turn} out of range"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Simtest Stress Info Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestStressInfo:
    def test_stress_patterns_are_listable(self):
        patterns = [
            {"id": p.value, "name": p.value.replace("_", " ").title()}
            for p in StressPattern
        ]
        assert len(patterns) == 3
        names = [p["name"] for p in patterns]
        assert "Fact Seeding" in names
        assert "Contradiction" in names
        assert "Progressive Complexity" in names

    def test_built_in_data_counts(self):
        assert len(BUILT_IN_FACTS) == 10
        assert len(BUILT_IN_CONTRADICTIONS) == 5
        assert len(BUILT_IN_COMPLEXITY_LEVELS) == 5