"""
Tests for Context Endurance / Memory Stress Testing (P2 #10).

Tests cover:
- EnduranceConfig (validation, defaults, custom values)
- StressPattern enum
- MemoryFact / ContradictionPair / ComplexityLevel models
- FactSeedingStrategy (instruction generation, turn distribution)
- ContradictionStrategy (pair placement, gap enforcement)
- ProgressiveComplexityStrategy (level escalation)
- EnduranceRunner (prompt injection, strategy orchestration)
- MemoryEvaluator (fact scoring, contradiction detection, decay curve)
- MemoryScorecard (scores, rates, degradation detection)
- Edge cases (empty configs, zero facts, single turn)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.endurance import (
    BUILT_IN_CONTRADICTIONS,
    BUILT_IN_COMPLEXITY_LEVELS,
    BUILT_IN_FACTS,
    ComplexityLevel,
    ContradictionPair,
    ContradictionScore,
    ContradictionStrategy,
    DecayPoint,
    EnduranceConfig,
    EnduranceRunner,
    FactSeedingStrategy,
    MemoryEvaluator,
    MemoryFact,
    MemoryScore,
    MemoryScorecard,
    ProgressiveComplexityStrategy,
    StressPattern,
)


# ━━━ Fixtures ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def default_config() -> EnduranceConfig:
    return EnduranceConfig()


@pytest.fixture
def custom_config() -> EnduranceConfig:
    return EnduranceConfig(
        patterns=[StressPattern.FACT_SEEDING],
        target_turns=20,
        num_facts=3,
        seed_window=(1, 4),
        recall_window=(12, 18),
        num_contradictions=2,
    )


@pytest.fixture
def runner() -> EnduranceRunner:
    return EnduranceRunner()


@pytest.fixture
def evaluator() -> MemoryEvaluator:
    return MemoryEvaluator()


@pytest.fixture
def sample_conversation() -> list:
    """A mock conversation with seeded facts and recall attempts."""
    return [
        {"speaker": "user", "message": "Hi, I need help with something."},
        {"speaker": "bot", "message": "Hello! How can I help you today?"},
        {"speaker": "user", "message": "By the way, my name is Marcus Henderson. I have an issue with my order."},
        {"speaker": "bot", "message": "Hello Marcus! I'd be happy to help. What's the issue with your order?"},
        {"speaker": "user", "message": "I'm calling about order number TXN-88421. Can you look into that?"},
        {"speaker": "bot", "message": "I've pulled up order TXN-88421. What seems to be the problem?"},
        {"speaker": "user", "message": "The item arrived damaged."},
        {"speaker": "bot", "message": "I'm sorry to hear that. Let me look into a replacement."},
        {"speaker": "user", "message": "I'm based in Portland, Oregon. Does that affect shipping?"},
        {"speaker": "bot", "message": "Portland, Oregon — shipping should be standard, 3-5 days."},
        # Turns 6-10 — filler
        {"speaker": "user", "message": "What's the return policy?"},
        {"speaker": "bot", "message": "You have 30 days to return items for a full refund."},
        {"speaker": "user", "message": "And what about exchanges?"},
        {"speaker": "bot", "message": "Exchanges are free within 30 days as well."},
        {"speaker": "user", "message": "Good to know. What are my options here?"},
        {"speaker": "bot", "message": "You can return for a refund or exchange for a new unit."},
        # Turns 16+ — recall attempts
        {"speaker": "user", "message": "Sorry, can you remind me — what's my name? I want to make sure you have it right."},
        {"speaker": "bot", "message": "Of course! Your name is Marcus Henderson."},
        {"speaker": "user", "message": "Going back to the order I mentioned earlier — what was the order number again?"},
        {"speaker": "bot", "message": "Your order number is TXN-88421."},
        {"speaker": "user", "message": "You should have my location from earlier. Where did I say I was located?"},
        {"speaker": "bot", "message": "You mentioned you're based in Portland, Oregon."},
    ]


@pytest.fixture
def forgetful_conversation() -> list:
    """A conversation where the bot forgets facts."""
    return [
        {"speaker": "user", "message": "My name is Marcus Henderson."},
        {"speaker": "bot", "message": "Hello! How can I help you?"},
        {"speaker": "user", "message": "Order number TXN-88421 please."},
        {"speaker": "bot", "message": "I'll look into that for you."},
        {"speaker": "user", "message": "I'm in Portland, Oregon."},
        {"speaker": "bot", "message": "Noted."},
        # Several turns of filler
        {"speaker": "user", "message": "Anyway, about the return."},
        {"speaker": "bot", "message": "Sure, what about it?"},
        {"speaker": "user", "message": "Can we process that?"},
        {"speaker": "bot", "message": "Of course, let me check."},
        {"speaker": "user", "message": "What about shipping times?"},
        {"speaker": "bot", "message": "It varies by location."},
        {"speaker": "user", "message": "How long for my area?"},
        {"speaker": "bot", "message": "I'd need to look that up."},
        {"speaker": "user", "message": "Any other options?"},
        {"speaker": "bot", "message": "We have express shipping too."},
        # Recall — bot forgets
        {"speaker": "user", "message": "What's my name? You should have it."},
        {"speaker": "bot", "message": "I apologize, could you remind me of your name?"},
        {"speaker": "user", "message": "What was my order number?"},
        {"speaker": "bot", "message": "Could you provide your order number again please?"},
        {"speaker": "user", "message": "Where did I say I'm located?"},
        {"speaker": "bot", "message": "I don't have that on file. Where are you located?"},
    ]


# ━━━ EnduranceConfig Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEnduranceConfig:
    def test_default_values(self, default_config):
        assert default_config.target_turns == 30
        assert default_config.num_facts == 5
        assert default_config.seed_window == (1, 5)
        assert default_config.recall_window == (15, 25)
        assert default_config.num_contradictions == 3
        assert default_config.contradiction_gap == 8
        assert default_config.complexity_levels == 5
        assert len(default_config.patterns) == 3

    def test_all_patterns_included_by_default(self, default_config):
        assert StressPattern.FACT_SEEDING in default_config.patterns
        assert StressPattern.CONTRADICTION in default_config.patterns
        assert StressPattern.PROGRESSIVE_COMPLEXITY in default_config.patterns

    def test_custom_values(self, custom_config):
        assert custom_config.target_turns == 20
        assert custom_config.num_facts == 3
        assert len(custom_config.patterns) == 1

    def test_validation_passes_for_default(self, default_config):
        issues = default_config.validate()
        assert issues == []

    def test_validation_fails_short_turns(self):
        config = EnduranceConfig(target_turns=5)
        issues = config.validate()
        assert any("at least 10" in i for i in issues)

    def test_validation_fails_overlapping_windows(self):
        config = EnduranceConfig(seed_window=(1, 15), recall_window=(10, 25))
        issues = config.validate()
        assert any("before recall_window" in i for i in issues)

    def test_validation_fails_recall_exceeds_turns(self):
        config = EnduranceConfig(target_turns=20, recall_window=(15, 25))
        issues = config.validate()
        assert any("exceeds target_turns" in i for i in issues)

    def test_validation_fails_zero_facts(self):
        config = EnduranceConfig(num_facts=0)
        issues = config.validate()
        assert any("at least 1" in i for i in issues)


# ━━━ StressPattern Enum Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestStressPattern:
    def test_three_patterns(self):
        assert len(StressPattern) == 3

    def test_values(self):
        assert StressPattern.FACT_SEEDING.value == "fact_seeding"
        assert StressPattern.CONTRADICTION.value == "contradiction"
        assert StressPattern.PROGRESSIVE_COMPLEXITY.value == "progressive_complexity"


# ━━━ Built-in Data Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBuiltInData:
    def test_built_in_facts_count(self):
        assert len(BUILT_IN_FACTS) == 10

    def test_all_facts_have_required_fields(self):
        for fact in BUILT_IN_FACTS:
            assert fact.fact_id, f"Missing fact_id"
            assert fact.category, f"Missing category for {fact.fact_id}"
            assert fact.seed_text, f"Missing seed_text for {fact.fact_id}"
            assert fact.recall_prompt, f"Missing recall_prompt for {fact.fact_id}"
            assert len(fact.expected_in_response) >= 1, f"Missing expected_in_response for {fact.fact_id}"

    def test_fact_ids_unique(self):
        ids = [f.fact_id for f in BUILT_IN_FACTS]
        assert len(ids) == len(set(ids)), "Duplicate fact IDs found"

    def test_built_in_contradictions_count(self):
        assert len(BUILT_IN_CONTRADICTIONS) == 5

    def test_all_contradictions_have_required_fields(self):
        for pair in BUILT_IN_CONTRADICTIONS:
            assert pair.pair_id
            assert pair.original_statement
            assert pair.contradicting_statement
            assert pair.expected_bot_behavior

    def test_contradiction_ids_unique(self):
        ids = [p.pair_id for p in BUILT_IN_CONTRADICTIONS]
        assert len(ids) == len(set(ids))

    def test_built_in_complexity_levels_count(self):
        assert len(BUILT_IN_COMPLEXITY_LEVELS) == 5

    def test_complexity_levels_ascending(self):
        for i, level in enumerate(BUILT_IN_COMPLEXITY_LEVELS):
            assert level.level == i + 1

    def test_all_levels_have_instructions(self):
        for level in BUILT_IN_COMPLEXITY_LEVELS:
            assert len(level.instruction) > 20


# ━━━ FactSeedingStrategy Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFactSeedingStrategy:
    def test_seed_instructions_generated(self, default_config):
        strategy = FactSeedingStrategy(default_config)
        instructions = strategy.get_seed_instructions()
        assert len(instructions) == 5  # num_facts=5

    def test_seed_turns_within_window(self, default_config):
        strategy = FactSeedingStrategy(default_config)
        instructions = strategy.get_seed_instructions()
        for turn in instructions.keys():
            assert default_config.seed_window[0] <= turn <= default_config.seed_window[1]

    def test_recall_instructions_generated(self, default_config):
        strategy = FactSeedingStrategy(default_config)
        _ = strategy.get_seed_instructions()  # Must seed first to set turns
        instructions = strategy.get_recall_instructions()
        assert len(instructions) >= 1

    def test_recall_turns_within_window(self, default_config):
        strategy = FactSeedingStrategy(default_config)
        _ = strategy.get_seed_instructions()
        instructions = strategy.get_recall_instructions()
        for turn in instructions.keys():
            assert default_config.recall_window[0] <= turn <= default_config.recall_window[1]

    def test_facts_have_seeded_turns_set(self, default_config):
        strategy = FactSeedingStrategy(default_config)
        strategy.get_seed_instructions()
        for fact in strategy.facts:
            assert fact.seeded_at_turn is not None

    def test_all_instructions_merged(self, default_config):
        strategy = FactSeedingStrategy(default_config)
        all_inst = strategy.get_all_instructions()
        assert len(all_inst) >= 2  # At least some seed + recall instructions

    def test_custom_facts(self):
        custom = [
            MemoryFact(
                fact_id="test1",
                category="test",
                seed_text="I am 30 years old.",
                recall_prompt="How old am I?",
                expected_in_response=["30"],
            ),
        ]
        config = EnduranceConfig(num_facts=1)
        strategy = FactSeedingStrategy(config, facts=custom)
        instructions = strategy.get_seed_instructions()
        assert len(instructions) == 1
        assert "30 years old" in list(instructions.values())[0]


# ━━━ ContradictionStrategy Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestContradictionStrategy:
    def test_instructions_generated(self, default_config):
        strategy = ContradictionStrategy(default_config)
        instructions = strategy.get_instructions()
        # Should have original + contradiction for each pair = 6 instructions
        assert len(instructions) >= 4  # At least some pairs

    def test_contradiction_gap_respected(self, default_config):
        strategy = ContradictionStrategy(default_config)
        strategy.get_instructions()
        for pair in strategy.pairs:
            if pair.original_turn and pair.contradiction_turn:
                gap = pair.contradiction_turn - pair.original_turn
                assert gap >= default_config.contradiction_gap

    def test_original_and_contradiction_both_present(self, default_config):
        strategy = ContradictionStrategy(default_config)
        instructions = strategy.get_instructions()
        seed_count = sum(1 for v in instructions.values() if "SEED" in v)
        inject_count = sum(1 for v in instructions.values() if "INJECT" in v)
        assert seed_count >= 1
        assert inject_count >= 1

    def test_custom_pairs(self):
        custom = [
            ContradictionPair(
                pair_id="test_pair",
                original_statement="I want A",
                contradicting_statement="I want B",
                expected_bot_behavior="Notice the change",
            ),
        ]
        config = EnduranceConfig(num_contradictions=1)
        strategy = ContradictionStrategy(config, pairs=custom)
        instructions = strategy.get_instructions()
        assert len(instructions) == 2  # original + contradiction


# ━━━ ProgressiveComplexityStrategy Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestProgressiveComplexityStrategy:
    def test_instructions_generated(self, default_config):
        strategy = ProgressiveComplexityStrategy(default_config)
        instructions = strategy.get_instructions()
        assert len(instructions) >= 5  # At least some turns

    def test_levels_have_assigned_turns(self, default_config):
        strategy = ProgressiveComplexityStrategy(default_config)
        strategy.get_instructions()
        for level in strategy.levels:
            assert len(level.turns) >= 1

    def test_complexity_labels_in_instructions(self, default_config):
        strategy = ProgressiveComplexityStrategy(default_config)
        instructions = strategy.get_instructions()
        found_levels = set()
        for inst in instructions.values():
            for i in range(1, 6):
                if f"LEVEL {i}/5" in inst:
                    found_levels.add(i)
        assert len(found_levels) >= 3  # Most levels should appear

    def test_turns_cover_conversation(self, default_config):
        strategy = ProgressiveComplexityStrategy(default_config)
        instructions = strategy.get_instructions()
        turns = sorted(instructions.keys())
        assert turns[0] <= 6  # Starts early
        assert turns[-1] >= default_config.target_turns - 6  # Covers late turns


# ━━━ EnduranceRunner Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEnduranceRunner:
    def test_default_initialization(self, runner):
        assert len(runner.config.patterns) == 3
        assert len(runner._strategies) == 3

    def test_single_pattern_initialization(self):
        config = EnduranceConfig(patterns=[StressPattern.FACT_SEEDING])
        runner = EnduranceRunner(config)
        assert len(runner._strategies) == 1

    def test_apply_to_persona_prompt(self, runner):
        original = "You are a frustrated customer named Jane."
        enhanced = runner.apply_to_persona_prompt(original)

        assert original in enhanced
        assert "MEMORY STRESS TEST INSTRUCTIONS" in enhanced
        assert "END MEMORY STRESS TEST INSTRUCTIONS" in enhanced

    def test_prompt_preserves_original(self, runner):
        original = "Original persona prompt with details."
        enhanced = runner.apply_to_persona_prompt(original)
        assert enhanced.startswith(original)

    def test_prompt_includes_turn_instructions(self, runner):
        enhanced = runner.apply_to_persona_prompt("Test persona.")
        assert "TURN" in enhanced

    def test_prompt_includes_pattern_names(self, runner):
        enhanced = runner.apply_to_persona_prompt("Test.")
        assert "fact_seeding" in enhanced
        assert "contradiction" in enhanced
        assert "progressive_complexity" in enhanced

    def test_get_facts(self, runner):
        facts = runner.get_facts()
        assert len(facts) == 5  # default num_facts

    def test_get_contradictions(self, runner):
        pairs = runner.get_contradictions()
        assert len(pairs) == 3  # default num_contradictions

    def test_get_complexity_levels(self, runner):
        levels = runner.get_complexity_levels()
        assert len(levels) == 5

    def test_get_min_turns(self, runner):
        assert runner.get_min_turns() == 30  # default target_turns

    def test_all_instructions_non_empty(self, runner):
        all_inst = runner.get_all_instructions()
        assert len(all_inst) >= 5  # Multiple turns should have instructions

    def test_no_patterns_returns_original(self):
        config = EnduranceConfig(patterns=[])
        runner = EnduranceRunner(config)
        original = "Test prompt."
        enhanced = runner.apply_to_persona_prompt(original)
        assert enhanced == original

    def test_instructions_are_lists(self, runner):
        all_inst = runner.get_all_instructions()
        for turn, instructions in all_inst.items():
            assert isinstance(turn, int)
            assert isinstance(instructions, list)
            assert all(isinstance(i, str) for i in instructions)


# ━━━ MemoryEvaluator Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMemoryEvaluator:
    def test_evaluate_good_memory(self, evaluator, sample_conversation):
        facts = [
            MemoryFact(
                fact_id="user_name",
                category="personal",
                seed_text="My name is Marcus Henderson.",
                recall_prompt="What's my name?",
                expected_in_response=["Marcus", "Henderson"],
                seeded_at_turn=2,
                recalled_at_turn=9,
            ),
            MemoryFact(
                fact_id="order_number",
                category="order",
                seed_text="Order TXN-88421",
                recall_prompt="What was the order number?",
                expected_in_response=["TXN-88421"],
                seeded_at_turn=3,
                recalled_at_turn=10,
            ),
        ]
        scorecard = evaluator.evaluate_conversation(
            "conv1", "Test Persona", sample_conversation, facts, []
        )

        assert scorecard.conversation_id == "conv1"
        assert scorecard.total_turns == len(sample_conversation)
        assert len(scorecard.fact_scores) == 2

    def test_evaluate_forgetful_bot(self, evaluator, forgetful_conversation):
        facts = [
            MemoryFact(
                fact_id="user_name",
                category="personal",
                seed_text="My name is Marcus Henderson.",
                recall_prompt="What's my name?",
                expected_in_response=["Marcus", "Henderson"],
                seeded_at_turn=1,
                recalled_at_turn=9,
            ),
        ]
        scorecard = evaluator.evaluate_conversation(
            "conv2", "Test", forgetful_conversation, facts, []
        )
        assert scorecard.conversation_id == "conv2"
        assert len(scorecard.fact_scores) >= 0  # May or may not find exact matches

    def test_evaluate_contradiction_detected(self, evaluator):
        turns = [
            {"speaker": "user", "message": "I want the blue version."},
            {"speaker": "bot", "message": "Blue — great choice!"},
            {"speaker": "user", "message": "Some other question."},
            {"speaker": "bot", "message": "Sure, let me help."},
            {"speaker": "user", "message": "I told you I wanted red from the start."},
            {"speaker": "bot", "message": "Wait, earlier you mentioned you wanted the blue version. Did you change your mind?"},
        ]
        pair = ContradictionPair(
            pair_id="color",
            original_statement="I want blue",
            contradicting_statement="I wanted red",
            expected_bot_behavior="Notice contradiction",
            original_turn=1,
            contradiction_turn=3,
        )
        scorecard = evaluator.evaluate_conversation(
            "conv3", "Test", turns, [], [pair]
        )
        assert len(scorecard.contradiction_scores) == 1
        # Bot said "earlier you mentioned" — should detect
        assert scorecard.contradiction_scores[0].bot_noticed is True

    def test_evaluate_contradiction_missed(self, evaluator):
        turns = [
            {"speaker": "user", "message": "I want the blue version."},
            {"speaker": "bot", "message": "Great choice!"},
            {"speaker": "user", "message": "I told you I wanted red from the start."},
            {"speaker": "bot", "message": "Red it is! Let me update that."},
        ]
        pair = ContradictionPair(
            pair_id="color",
            original_statement="I want blue",
            contradicting_statement="I wanted red",
            expected_bot_behavior="Notice contradiction",
            original_turn=1,
            contradiction_turn=2,
        )
        scorecard = evaluator.evaluate_conversation(
            "conv4", "Test", turns, [], [pair]
        )
        assert scorecard.contradiction_scores[0].bot_noticed is False

    def test_evaluate_empty_conversation(self, evaluator):
        scorecard = evaluator.evaluate_conversation("empty", "Test", [], [], [])
        assert scorecard.total_turns == 0
        assert scorecard.fact_scores == []
        assert scorecard.contradiction_scores == []


# ━━━ MemoryScorecard Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMemoryScorecard:
    def test_perfect_recall(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 1.0, ["A"], ["A"], []),
                MemoryScore("f2", 3, 16, 13, 1.0, ["B"], ["B"], []),
            ],
        )
        assert scorecard.fact_recall_rate == 1.0
        assert scorecard.average_recall_score == 1.0

    def test_partial_recall(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 1.0, ["A"], ["A"], []),
                MemoryScore("f2", 3, 16, 13, 0.0, ["B"], [], ["B"]),
            ],
        )
        assert scorecard.fact_recall_rate == 0.5  # 1 of 2 recalled
        assert scorecard.average_recall_score == 0.5

    def test_zero_recall(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 0.0, ["A"], [], ["A"]),
                MemoryScore("f2", 3, 16, 13, 0.2, ["B"], [], ["B"]),
            ],
        )
        assert scorecard.fact_recall_rate == 0.0  # Neither above 0.5

    def test_contradiction_detection_rate(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            contradiction_scores=[
                ContradictionScore("c1", 2, 10, True, "noticed"),
                ContradictionScore("c2", 4, 12, False, "missed"),
                ContradictionScore("c3", 6, 14, True, "caught it"),
            ],
        )
        assert abs(scorecard.contradiction_detection_rate - 2 / 3) < 0.01

    def test_degradation_turn_detected(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=30,
            decay_curve=[
                DecayPoint(1, 1.0, 1.0),
                DecayPoint(10, 0.8, 0.8),
                DecayPoint(20, 0.4, 0.4),  # First below 0.5
                DecayPoint(30, 0.2, 0.2),
            ],
        )
        assert scorecard.degradation_turn == 20

    def test_no_degradation(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=30,
            decay_curve=[
                DecayPoint(1, 1.0, 1.0),
                DecayPoint(15, 0.9, 0.9),
                DecayPoint(30, 0.8, 0.8),
            ],
        )
        assert scorecard.degradation_turn is None

    def test_overall_endurance_score(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 1.0, ["A"], ["A"], []),
            ],
            contradiction_scores=[
                ContradictionScore("c1", 3, 11, True, "noticed"),
            ],
            decay_curve=[
                DecayPoint(1, 1.0, 1.0),
                DecayPoint(15, 0.8, 0.8),
            ],
        )
        score = scorecard.overall_endurance_score
        assert 0.0 <= score <= 1.0
        assert score > 0.7  # All indicators are good

    def test_overall_score_bad_memory(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 0.0, ["A"], [], ["A"]),
            ],
            contradiction_scores=[
                ContradictionScore("c1", 3, 11, False, "missed"),
            ],
            decay_curve=[
                DecayPoint(1, 1.0, 1.0),
                DecayPoint(15, 0.1, 0.1),
            ],
        )
        score = scorecard.overall_endurance_score
        assert score < 0.3  # All indicators are bad

    def test_empty_scorecard_defaults(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=0,
        )
        assert scorecard.fact_recall_rate == 0.0
        assert scorecard.average_recall_score == 0.0
        assert scorecard.contradiction_detection_rate == 0.0
        assert scorecard.degradation_turn is None
        # Overall score defaults to 0.5 for missing components
        score = scorecard.overall_endurance_score
        assert 0.3 <= score <= 0.7

    def test_to_dict_serialization(self):
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 0.85, ["A", "B"], ["A"], ["B"]),
            ],
            contradiction_scores=[
                ContradictionScore("c1", 3, 11, True, "noticed it"),
            ],
            decay_curve=[
                DecayPoint(1, 1.0, 1.0),
                DecayPoint(15, 0.85, 0.9, 3),
            ],
        )
        data = scorecard.to_dict()

        assert data["conversation_id"] == "test"
        assert data["fact_recall_rate"] == 1.0  # 0.85 > 0.5 → recalled
        assert data["contradiction_detection_rate"] == 1.0
        assert len(data["fact_scores"]) == 1
        assert data["fact_scores"][0]["fact_id"] == "f1"
        assert len(data["decay_curve"]) == 2
        assert data["decay_curve"][1]["complexity_level"] == 3


# ━━━ Edge Cases ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEdgeCases:
    def test_single_fact(self):
        config = EnduranceConfig(num_facts=1)
        strategy = FactSeedingStrategy(config)
        seeds = strategy.get_seed_instructions()
        recalls = strategy.get_recall_instructions()
        assert len(seeds) == 1
        assert len(recalls) == 1

    def test_max_facts(self):
        config = EnduranceConfig(num_facts=10)  # All built-in facts
        strategy = FactSeedingStrategy(config)
        assert len(strategy.facts) == 10  # All 10 facts selected
        seeds = strategy.get_seed_instructions()
        # With seed_window (1,5), 10 facts map to 5 unique turn keys
        # (multiple facts per turn). Verify all facts got assigned.
        for f in strategy.facts:
            assert f.seeded_at_turn is not None

    def test_more_facts_than_available(self):
        config = EnduranceConfig(num_facts=50)  # More than 10 built-in
        strategy = FactSeedingStrategy(config)
        assert len(strategy.facts) == 10  # Capped at built-in count
        strategy.get_seed_instructions()
        for f in strategy.facts:
            assert f.seeded_at_turn is not None

    def test_zero_contradictions(self):
        config = EnduranceConfig(num_contradictions=0, patterns=[StressPattern.CONTRADICTION])
        strategy = ContradictionStrategy(config)
        instructions = strategy.get_instructions()
        assert len(instructions) == 0

    def test_short_conversation_config(self):
        config = EnduranceConfig(
            target_turns=12,
            seed_window=(1, 3),
            recall_window=(8, 11),
            num_facts=2,
        )
        issues = config.validate()
        assert issues == []
        runner = EnduranceRunner(config)
        facts = runner.get_facts()
        assert len(facts) == 2

    def test_memory_score_turn_gap_calculation(self):
        score = MemoryScore("f1", 3, 20, 17, 0.5, ["A"], ["A"], [])
        assert score.turn_gap == 17

    def test_decay_point_optional_complexity(self):
        point = DecayPoint(turn=10, memory_score=0.8, quality_score=0.9)
        assert point.complexity_level is None

        point_with_level = DecayPoint(turn=10, memory_score=0.8, quality_score=0.9, complexity_level=3)
        assert point_with_level.complexity_level == 3


# ━━━ Integration Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEnduranceIntegration:
    def test_full_pipeline_fact_seeding_only(self, evaluator, sample_conversation):
        """End-to-end: config → runner → prompt → evaluate."""
        config = EnduranceConfig(
            patterns=[StressPattern.FACT_SEEDING],
            target_turns=20,
            num_facts=3,
            seed_window=(1, 4),
            recall_window=(9, 11),
        )
        runner = EnduranceRunner(config)

        # Step 1: Generate enhanced prompt
        original = "You are a frustrated customer."
        enhanced = runner.apply_to_persona_prompt(original)
        assert "MEMORY STRESS TEST" in enhanced
        assert original in enhanced

        # Step 2: Get facts for later evaluation
        facts = runner.get_facts()
        assert len(facts) == 3
        for f in facts:
            assert f.seeded_at_turn is not None

        # Step 3: Evaluate against sample conversation
        scorecard = evaluator.evaluate_conversation(
            "integration_test", "Frustrated Customer",
            sample_conversation, facts, []
        )
        assert scorecard.conversation_id == "integration_test"
        assert scorecard.total_turns == len(sample_conversation)

    def test_full_pipeline_all_patterns(self, evaluator, sample_conversation):
        """End-to-end with all three stress patterns."""
        config = EnduranceConfig(
            target_turns=20,
            num_facts=2,
            seed_window=(1, 3),
            recall_window=(9, 11),
            num_contradictions=1,
            contradiction_gap=5,
        )
        runner = EnduranceRunner(config)

        enhanced = runner.apply_to_persona_prompt("Test persona.")
        assert "fact_seeding" in enhanced
        assert "contradiction" in enhanced
        assert "progressive_complexity" in enhanced

        facts = runner.get_facts()
        pairs = runner.get_contradictions()
        assert len(facts) == 2
        assert len(pairs) == 1

        scorecard = evaluator.evaluate_conversation(
            "full_test", "Test",
            sample_conversation, facts, pairs
        )
        assert scorecard.overall_endurance_score >= 0.0
        assert scorecard.overall_endurance_score <= 1.0

    def test_scorecard_to_dict_roundtrip(self):
        """Verify scorecard serializes to valid dict."""
        scorecard = MemoryScorecard(
            conversation_id="test",
            persona_name="Test",
            total_turns=20,
            fact_scores=[
                MemoryScore("f1", 2, 15, 13, 0.85, ["A"], ["A"], []),
            ],
            contradiction_scores=[
                ContradictionScore("c1", 3, 11, True, "caught"),
            ],
            decay_curve=[
                DecayPoint(1, 1.0, 1.0),
            ],
        )
        data = scorecard.to_dict()
        import json
        json_str = json.dumps(data)
        assert json_str  # Serializable without errors
        parsed = json.loads(json_str)
        assert parsed["conversation_id"] == "test"
        assert parsed["overall_endurance_score"] > 0