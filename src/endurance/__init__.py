"""
Context Endurance / Memory Stress Testing (P2 #10).

Tests how well chatbots maintain context, remember facts, and handle
contradictions over long multi-turn conversations.

Three stress patterns:
1. Fact Seeding — Plant facts early, verify recall later
2. Contradiction Injection — State opposing facts, check if bot notices
3. Progressive Complexity — Escalate complexity, measure quality decay

Components:
- EnduranceConfig: Configuration for stress test parameters
- StressPattern (enum): The three test patterns
- MemoryFact: A fact planted during conversation with recall metadata
- FactSeedingStrategy: Generates seeding and recall instructions
- ContradictionStrategy: Generates contradiction pairs
- ProgressiveComplexityStrategy: Generates escalating complexity prompts
- EnduranceRunner: Orchestrates stress test prompt injection into personas
- MemoryScorecard: Per-conversation memory retention analysis with decay curve
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ━━━ Enums ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class StressPattern(str, enum.Enum):
    """Types of memory stress testing."""

    FACT_SEEDING = "fact_seeding"
    CONTRADICTION = "contradiction"
    PROGRESSIVE_COMPLEXITY = "progressive_complexity"


# ━━━ Data Models ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class MemoryFact:
    """
    A fact planted during conversation for later recall verification.

    Attributes:
        fact_id: Unique identifier (e.g., "order_number", "city")
        category: Grouping (e.g., "personal", "order", "preference")
        seed_text: The text the user says to plant the fact
        recall_prompt: The question to ask later to verify recall
        expected_in_response: Keywords/phrases that should appear if bot remembers
        seeded_at_turn: Turn number where fact was planted
        recalled_at_turn: Turn number where recall was attempted (set later)
        recall_score: 0.0-1.0 score for how well the bot recalled (set later)
    """

    fact_id: str
    category: str
    seed_text: str
    recall_prompt: str
    expected_in_response: List[str]
    seeded_at_turn: Optional[int] = None
    recalled_at_turn: Optional[int] = None
    recall_score: Optional[float] = None


@dataclass
class ContradictionPair:
    """
    A pair of contradictory statements for testing bot awareness.

    Attributes:
        pair_id: Unique identifier
        original_statement: First thing the user says
        contradicting_statement: The contradictory follow-up
        expected_bot_behavior: What a good bot should do (notice the contradiction)
        original_turn: Turn where original was stated
        contradiction_turn: Turn where contradiction is injected
        bot_noticed: Whether the bot caught the contradiction (set later)
    """

    pair_id: str
    original_statement: str
    contradicting_statement: str
    expected_bot_behavior: str
    original_turn: Optional[int] = None
    contradiction_turn: Optional[int] = None
    bot_noticed: Optional[bool] = None


@dataclass
class ComplexityLevel:
    """
    A complexity level in progressive escalation.

    Attributes:
        level: 1-5 complexity (1=simple, 5=very complex)
        description: What this level entails
        instruction: Instruction for the user simulator at this level
        turns: Which turns use this level
    """

    level: int
    description: str
    instruction: str
    turns: List[int] = field(default_factory=list)


@dataclass
class EnduranceConfig:
    """
    Configuration for memory stress testing.

    Attributes:
        patterns: Which stress patterns to apply (default: all three)
        target_turns: Total conversation length for stress test (default: 30)
        num_facts: Number of facts to seed (for fact_seeding pattern)
        seed_window: Turn range for planting facts (e.g., turns 1-5)
        recall_window: Turn range for recalling facts (e.g., turns 15-25)
        num_contradictions: Number of contradiction pairs to inject
        contradiction_gap: Minimum turns between original and contradiction
        complexity_levels: Number of escalation levels (default: 5)
    """

    patterns: List[StressPattern] = field(
        default_factory=lambda: [
            StressPattern.FACT_SEEDING,
            StressPattern.CONTRADICTION,
            StressPattern.PROGRESSIVE_COMPLEXITY,
        ]
    )
    target_turns: int = 30
    num_facts: int = 5
    seed_window: Tuple[int, int] = (1, 5)
    recall_window: Tuple[int, int] = (15, 25)
    num_contradictions: int = 3
    contradiction_gap: int = 8
    complexity_levels: int = 5

    def validate(self) -> List[str]:
        """Validate configuration, return list of issues."""
        issues = []
        if self.target_turns < 10:
            issues.append("target_turns must be at least 10 for meaningful stress testing")
        if self.seed_window[1] >= self.recall_window[0]:
            issues.append("seed_window end must be before recall_window start")
        if self.recall_window[1] > self.target_turns:
            issues.append("recall_window end exceeds target_turns")
        if self.num_facts < 1:
            issues.append("num_facts must be at least 1")
        if self.contradiction_gap < 3:
            issues.append("contradiction_gap must be at least 3")
        return issues


# ━━━ Fact Libraries ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# Built-in facts for seeding — domain-agnostic facts that work with any bot
BUILT_IN_FACTS: List[MemoryFact] = [
    MemoryFact(
        fact_id="user_name",
        category="personal",
        seed_text="By the way, my name is Marcus Henderson.",
        recall_prompt="Sorry, can you remind me — what's my name? I want to make sure you have it right.",
        expected_in_response=["Marcus", "Henderson"],
    ),
    MemoryFact(
        fact_id="order_number",
        category="order",
        seed_text="I'm calling about order number TXN-88421. Can you look into that for me?",
        recall_prompt="Going back to the order I mentioned earlier — what was the order number again?",
        expected_in_response=["TXN-88421", "88421"],
    ),
    MemoryFact(
        fact_id="city",
        category="personal",
        seed_text="I'm based in Portland, Oregon. Does that affect anything?",
        recall_prompt="You should have my location from earlier in our chat. Where did I say I was located?",
        expected_in_response=["Portland", "Oregon"],
    ),
    MemoryFact(
        fact_id="email",
        category="contact",
        seed_text="You can reach me at marcus.h@example.com if needed.",
        recall_prompt="Do you still have my email from earlier? What was it?",
        expected_in_response=["marcus.h@example.com", "marcus"],
    ),
    MemoryFact(
        fact_id="preference",
        category="preference",
        seed_text="I prefer email communication over phone calls, just so you know.",
        recall_prompt="How did I say I prefer to be contacted?",
        expected_in_response=["email"],
    ),
    MemoryFact(
        fact_id="deadline",
        category="urgency",
        seed_text="I need this resolved by Friday the 15th — it's time-sensitive.",
        recall_prompt="When did I say I need this resolved by? The deadline I mentioned earlier.",
        expected_in_response=["Friday", "15th"],
    ),
    MemoryFact(
        fact_id="product",
        category="order",
        seed_text="This is about the wireless noise-canceling headphones I bought last week.",
        recall_prompt="What product was I asking about? Can you recall?",
        expected_in_response=["headphones", "wireless", "noise-canceling"],
    ),
    MemoryFact(
        fact_id="account_type",
        category="account",
        seed_text="I'm on the Premium Plus plan, which I upgraded to in January.",
        recall_prompt="Which plan or account level did I mention I'm on?",
        expected_in_response=["Premium", "Plus"],
    ),
    MemoryFact(
        fact_id="issue_count",
        category="history",
        seed_text="This is the third time I've had this same issue. Very frustrating.",
        recall_prompt="How many times did I say I've experienced this problem?",
        expected_in_response=["third", "three", "3"],
    ),
    MemoryFact(
        fact_id="budget",
        category="preference",
        seed_text="My budget for the upgrade is around $150, give or take.",
        recall_prompt="What budget range did I mention for this?",
        expected_in_response=["150", "$150"],
    ),
]


# Built-in contradiction pairs
BUILT_IN_CONTRADICTIONS: List[ContradictionPair] = [
    ContradictionPair(
        pair_id="color_preference",
        original_statement="I specifically want the blue version. Blue is my favorite color.",
        contradicting_statement="I told you I wanted the red one from the start. I've always preferred red.",
        expected_bot_behavior="Bot should notice the contradiction — user previously said blue, now claims red.",
    ),
    ContradictionPair(
        pair_id="delivery_address",
        original_statement="Ship it to my home address — 42 Oak Street, Apartment 7B.",
        contradicting_statement="Wait, I already gave you the address — it's 99 Pine Avenue, right?",
        expected_bot_behavior="Bot should flag the mismatch — previous address was 42 Oak Street, not 99 Pine Avenue.",
    ),
    ContradictionPair(
        pair_id="timeline",
        original_statement="I bought this about two weeks ago. Within the return window hopefully.",
        contradicting_statement="Like I said, I got this six months ago. Is it still under warranty?",
        expected_bot_behavior="Bot should note the time discrepancy — user first said two weeks, now says six months.",
    ),
    ContradictionPair(
        pair_id="quantity",
        original_statement="I ordered three of them — one for me and two as gifts.",
        contradicting_statement="I only ordered one, remember? Just the single item for myself.",
        expected_bot_behavior="Bot should notice quantity contradiction — three vs one.",
    ),
    ContradictionPair(
        pair_id="contact_pref",
        original_statement="Please don't call me. I hate phone calls. Email only.",
        contradicting_statement="Why hasn't anyone called me yet? I specifically asked for a phone callback!",
        expected_bot_behavior="Bot should flag the contradiction — user previously rejected phone calls.",
    ),
]


# Progressive complexity instructions
BUILT_IN_COMPLEXITY_LEVELS: List[ComplexityLevel] = [
    ComplexityLevel(
        level=1,
        description="Simple, single-topic questions",
        instruction=(
            "Ask a simple, straightforward question about one topic. "
            "Use short sentences. Be direct."
        ),
    ),
    ComplexityLevel(
        level=2,
        description="Follow-up questions with references to previous answers",
        instruction=(
            "Ask a follow-up question that references something the bot said earlier. "
            "Build on the previous answer. Use phrases like 'you mentioned...' or 'going back to...'."
        ),
    ),
    ComplexityLevel(
        level=3,
        description="Multi-part questions combining two topics",
        instruction=(
            "Ask a question that combines TWO different topics from the conversation. "
            "For example: 'Given what you said about X, how does that affect Y?' "
            "Make the bot connect different parts of the discussion."
        ),
    ),
    ComplexityLevel(
        level=4,
        description="Complex conditional questions with constraints",
        instruction=(
            "Ask a complex question with conditions and constraints. "
            "Reference multiple prior facts. Use hypotheticals: "
            "'If I change X, but keep Y the same, and considering Z you mentioned...' "
            "The bot needs to track multiple variables."
        ),
    ),
    ComplexityLevel(
        level=5,
        description="Synthesis questions requiring full conversation recall",
        instruction=(
            "Ask the bot to synthesize or summarize the ENTIRE conversation so far. "
            "Request: 'Can you summarize everything we've discussed and give me a final recommendation?' "
            "Or: 'Based on ALL the details I've shared, what's your overall assessment?' "
            "This forces maximum context recall."
        ),
    ),
]


# ━━━ Strategy Classes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class FactSeedingStrategy:
    """
    Generates turn-by-turn instructions for fact seeding and recall.
    """

    def __init__(self, config: EnduranceConfig, facts: Optional[List[MemoryFact]] = None):
        self.config = config
        self.facts = (facts or BUILT_IN_FACTS)[: config.num_facts]

    def get_seed_instructions(self) -> Dict[int, str]:
        """
        Returns {turn_number: instruction} for seeding turns.
        Distributes facts evenly across the seed window.
        """
        seed_start, seed_end = self.config.seed_window
        num_facts = len(self.facts)
        if num_facts == 0:
            return {}

        # Distribute facts evenly across seed window
        seed_range = seed_end - seed_start + 1
        step = max(1, seed_range // num_facts)

        instructions: Dict[int, str] = {}
        for i, fact in enumerate(self.facts):
            turn = seed_start + (i * step)
            turn = min(turn, seed_end)  # Don't exceed window
            fact.seeded_at_turn = turn
            instructions[turn] = (
                f"MEMORY SEED: Naturally work this information into your message: "
                f"{fact.seed_text}\n"
                f"Make it feel conversational, not forced."
            )
        return instructions

    def get_recall_instructions(self) -> Dict[int, str]:
        """
        Returns {turn_number: instruction} for recall turns.
        Distributes recall prompts across the recall window.
        """
        recall_start, recall_end = self.config.recall_window
        num_facts = len(self.facts)
        if num_facts == 0:
            return {}

        recall_range = recall_end - recall_start + 1
        step = max(1, recall_range // num_facts)

        instructions: Dict[int, str] = {}
        for i, fact in enumerate(self.facts):
            turn = recall_start + (i * step)
            turn = min(turn, recall_end)
            fact.recalled_at_turn = turn
            instructions[turn] = (
                f"MEMORY RECALL: Ask the bot to recall a fact you shared earlier.\n"
                f"Use this prompt naturally: {fact.recall_prompt}\n"
                f"Expected: Bot should mention: {', '.join(fact.expected_in_response)}"
            )
        return instructions

    def get_all_instructions(self) -> Dict[int, str]:
        """Merge seed and recall instructions."""
        merged = {}
        merged.update(self.get_seed_instructions())
        merged.update(self.get_recall_instructions())
        return merged


class ContradictionStrategy:
    """
    Generates turn-by-turn instructions for contradiction injection.
    """

    def __init__(
        self, config: EnduranceConfig, pairs: Optional[List[ContradictionPair]] = None
    ):
        self.config = config
        self.pairs = (pairs or BUILT_IN_CONTRADICTIONS)[: config.num_contradictions]

    def get_instructions(self) -> Dict[int, str]:
        """
        Returns {turn_number: instruction} for contradiction turns.
        Places originals in early turns, contradictions after the gap.
        """
        instructions: Dict[int, str] = {}
        num_pairs = len(self.pairs)
        if num_pairs == 0:
            return {}

        # Place originals in first third of conversation
        original_zone_end = self.config.target_turns // 3
        original_step = max(1, original_zone_end // num_pairs)

        for i, pair in enumerate(self.pairs):
            original_turn = 2 + (i * original_step)  # Start at turn 2
            contradiction_turn = original_turn + self.config.contradiction_gap

            # Ensure contradiction fits within conversation
            if contradiction_turn >= self.config.target_turns:
                contradiction_turn = self.config.target_turns - 2

            pair.original_turn = original_turn
            pair.contradiction_turn = contradiction_turn

            instructions[original_turn] = (
                f"CONTRADICTION SEED: State this clearly in your message:\n"
                f'"{pair.original_statement}"\n'
                f"Make it sound natural."
            )
            instructions[contradiction_turn] = (
                f"CONTRADICTION INJECT: Now contradict what you said earlier.\n"
                f'Say: "{pair.contradicting_statement}"\n'
                f"Say it confidently as if this is what you always said.\n"
                f"WATCH: A good bot should notice this contradicts your earlier statement."
            )
        return instructions


class ProgressiveComplexityStrategy:
    """
    Generates turn-by-turn instructions for escalating complexity.
    """

    def __init__(
        self,
        config: EnduranceConfig,
        levels: Optional[List[ComplexityLevel]] = None,
    ):
        self.config = config
        self.levels = (levels or BUILT_IN_COMPLEXITY_LEVELS)[: config.complexity_levels]

    def get_instructions(self) -> Dict[int, str]:
        """
        Returns {turn_number: instruction} with escalating complexity.
        Divides conversation into zones, one per complexity level.
        """
        instructions: Dict[int, str] = {}
        num_levels = len(self.levels)
        if num_levels == 0:
            return {}

        turns_per_level = max(1, self.config.target_turns // num_levels)

        for i, level in enumerate(self.levels):
            zone_start = i * turns_per_level + 1
            zone_end = min((i + 1) * turns_per_level, self.config.target_turns)
            level.turns = list(range(zone_start, zone_end + 1))

            for turn in level.turns:
                instructions[turn] = (
                    f"COMPLEXITY LEVEL {level.level}/5 — {level.description}:\n"
                    f"{level.instruction}"
                )
        return instructions


# ━━━ Endurance Runner ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class EnduranceRunner:
    """
    Orchestrates memory stress testing by generating enriched persona
    system prompts with turn-by-turn stress instructions.

    Architecture: Same non-breaking integration as Scenario Templates.
    Modifies persona system_prompt — zero changes to conversation simulator.
    """

    def __init__(self, config: Optional[EnduranceConfig] = None):
        self.config = config or EnduranceConfig()
        self._strategies: Dict[StressPattern, Any] = {}
        self._initialize_strategies()

    def _initialize_strategies(self):
        """Create strategy instances for each enabled pattern."""
        for pattern in self.config.patterns:
            if pattern == StressPattern.FACT_SEEDING:
                self._strategies[pattern] = FactSeedingStrategy(self.config)
            elif pattern == StressPattern.CONTRADICTION:
                self._strategies[pattern] = ContradictionStrategy(self.config)
            elif pattern == StressPattern.PROGRESSIVE_COMPLEXITY:
                self._strategies[pattern] = ProgressiveComplexityStrategy(self.config)

    def get_all_instructions(self) -> Dict[int, List[str]]:
        """
        Merge instructions from all active strategies.
        Returns {turn_number: [list of instructions for that turn]}.
        """
        merged: Dict[int, List[str]] = {}
        for pattern, strategy in self._strategies.items():
            if pattern == StressPattern.FACT_SEEDING:
                instructions = strategy.get_all_instructions()
            else:
                instructions = strategy.get_instructions()

            for turn, instruction in instructions.items():
                if turn not in merged:
                    merged[turn] = []
                merged[turn].append(instruction)
        return merged

    def apply_to_persona_prompt(self, original_prompt: str) -> str:
        """
        Inject stress test instructions into a persona's system prompt.

        Returns the enhanced prompt with embedded turn-by-turn instructions.
        """
        all_instructions = self.get_all_instructions()
        if not all_instructions:
            return original_prompt

        # Build instruction block
        lines = [
            "",
            "=" * 60,
            "MEMORY STRESS TEST INSTRUCTIONS",
            "=" * 60,
            "",
            f"This conversation is a MEMORY STRESS TEST with {self.config.target_turns} turns.",
            "Follow the turn-specific instructions below EXACTLY.",
            "Between instructed turns, continue the conversation naturally.",
            "",
            "Active stress patterns: " + ", ".join(
                p.value for p in self.config.patterns
            ),
            "",
        ]

        # Add turn-by-turn instructions
        for turn in sorted(all_instructions.keys()):
            instructions = all_instructions[turn]
            lines.append(f"--- TURN {turn} ---")
            for inst in instructions:
                lines.append(inst)
            lines.append("")

        # General guidelines
        lines.extend([
            "--- GENERAL GUIDELINES ---",
            "• Between instructed turns, maintain natural conversation flow.",
            "• Stay in character as the persona throughout.",
            "• Don't reveal that this is a test to the bot.",
            "• If a fact recall question gets a wrong answer, note it but continue.",
            "• The goal is to test the BOT's memory, not your own.",
            "",
            "=" * 60,
            "END MEMORY STRESS TEST INSTRUCTIONS",
            "=" * 60,
        ])

        return original_prompt + "\n".join(lines)

    def get_facts(self) -> List[MemoryFact]:
        """Get the facts being used for seeding (if fact_seeding is active)."""
        strategy = self._strategies.get(StressPattern.FACT_SEEDING)
        if isinstance(strategy, FactSeedingStrategy):
            return strategy.facts
        return []

    def get_contradictions(self) -> List[ContradictionPair]:
        """Get the contradiction pairs (if contradiction pattern is active)."""
        strategy = self._strategies.get(StressPattern.CONTRADICTION)
        if isinstance(strategy, ContradictionStrategy):
            return strategy.pairs
        return []

    def get_complexity_levels(self) -> List[ComplexityLevel]:
        """Get complexity levels (if progressive_complexity is active)."""
        strategy = self._strategies.get(StressPattern.PROGRESSIVE_COMPLEXITY)
        if isinstance(strategy, ProgressiveComplexityStrategy):
            return strategy.levels
        return []

    def get_min_turns(self) -> int:
        """Return the minimum turns required for this stress configuration."""
        return self.config.target_turns


# ━━━ Memory Scorecard ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class MemoryScore:
    """Score for a single memory recall attempt."""

    fact_id: str
    seeded_at_turn: int
    recalled_at_turn: int
    turn_gap: int
    recall_score: float  # 0.0-1.0
    expected_keywords: List[str]
    found_keywords: List[str]
    missing_keywords: List[str]


@dataclass
class ContradictionScore:
    """Score for a single contradiction detection attempt."""

    pair_id: str
    original_turn: int
    contradiction_turn: int
    bot_noticed: bool
    evidence: str  # What in the bot's response indicates detection


@dataclass
class DecayPoint:
    """A single point on the memory decay curve."""

    turn: int
    memory_score: float  # 0.0-1.0 average recall at this point
    quality_score: float  # Quality judge score at this turn if available
    complexity_level: Optional[int] = None


@dataclass
class MemoryScorecard:
    """
    Comprehensive memory analysis for a single conversation.

    Provides:
    - Per-fact recall scores
    - Contradiction detection rates
    - Decay curve (memory score over turn number)
    - Degradation point (turn where memory drops below threshold)
    """

    conversation_id: str
    persona_name: str
    total_turns: int

    # Fact recall
    fact_scores: List[MemoryScore] = field(default_factory=list)

    # Contradiction detection
    contradiction_scores: List[ContradictionScore] = field(default_factory=list)

    # Decay curve
    decay_curve: List[DecayPoint] = field(default_factory=list)

    @property
    def fact_recall_rate(self) -> float:
        """Percentage of facts successfully recalled (score > 0.5)."""
        if not self.fact_scores:
            return 0.0
        recalled = sum(1 for s in self.fact_scores if s.recall_score > 0.5)
        return recalled / len(self.fact_scores)

    @property
    def average_recall_score(self) -> float:
        """Average recall score across all facts."""
        if not self.fact_scores:
            return 0.0
        return sum(s.recall_score for s in self.fact_scores) / len(self.fact_scores)

    @property
    def contradiction_detection_rate(self) -> float:
        """Percentage of contradictions noticed by the bot."""
        if not self.contradiction_scores:
            return 0.0
        detected = sum(1 for s in self.contradiction_scores if s.bot_noticed)
        return detected / len(self.contradiction_scores)

    @property
    def degradation_turn(self) -> Optional[int]:
        """
        The turn where memory score first drops below 0.5.
        Returns None if memory never degrades.
        """
        for point in self.decay_curve:
            if point.memory_score < 0.5:
                return point.turn
        return None

    @property
    def overall_endurance_score(self) -> float:
        """
        Combined endurance score (0.0-1.0):
        - 50% weight on fact recall rate
        - 30% weight on contradiction detection
        - 20% weight on average decay curve stability
        """
        recall_weight = 0.5
        contradiction_weight = 0.3
        stability_weight = 0.2

        recall = self.fact_recall_rate if self.fact_scores else 0.5
        contradiction = self.contradiction_detection_rate if self.contradiction_scores else 0.5
        stability = self._calculate_stability()

        return (
            recall * recall_weight
            + contradiction * contradiction_weight
            + stability * stability_weight
        )

    def _calculate_stability(self) -> float:
        """
        Calculate memory stability score from decay curve.
        1.0 = no decay, 0.0 = complete memory loss.
        """
        if not self.decay_curve:
            return 0.5
        scores = [p.memory_score for p in self.decay_curve]
        return sum(scores) / len(scores)

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "conversation_id": self.conversation_id,
            "persona_name": self.persona_name,
            "total_turns": self.total_turns,
            "fact_recall_rate": round(self.fact_recall_rate, 4),
            "average_recall_score": round(self.average_recall_score, 4),
            "contradiction_detection_rate": round(self.contradiction_detection_rate, 4),
            "degradation_turn": self.degradation_turn,
            "overall_endurance_score": round(self.overall_endurance_score, 4),
            "fact_scores": [
                {
                    "fact_id": s.fact_id,
                    "seeded_at_turn": s.seeded_at_turn,
                    "recalled_at_turn": s.recalled_at_turn,
                    "turn_gap": s.turn_gap,
                    "recall_score": round(s.recall_score, 4),
                    "expected_keywords": s.expected_keywords,
                    "found_keywords": s.found_keywords,
                    "missing_keywords": s.missing_keywords,
                }
                for s in self.fact_scores
            ],
            "contradiction_scores": [
                {
                    "pair_id": s.pair_id,
                    "original_turn": s.original_turn,
                    "contradiction_turn": s.contradiction_turn,
                    "bot_noticed": s.bot_noticed,
                    "evidence": s.evidence,
                }
                for s in self.contradiction_scores
            ],
            "decay_curve": [
                {
                    "turn": p.turn,
                    "memory_score": round(p.memory_score, 4),
                    "quality_score": round(p.quality_score, 4),
                    "complexity_level": p.complexity_level,
                }
                for p in self.decay_curve
            ],
        }


# ━━━ Memory Evaluator ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class MemoryEvaluator:
    """
    Evaluates a completed conversation against seeded facts and contradictions.
    Produces a MemoryScorecard.
    """

    def evaluate_conversation(
        self,
        conversation_id: str,
        persona_name: str,
        turns: List[Dict[str, str]],  # [{"speaker": "user/bot", "message": "..."}]
        facts: List[MemoryFact],
        contradictions: List[ContradictionPair],
    ) -> MemoryScorecard:
        """
        Analyze a completed conversation for memory retention.

        Args:
            conversation_id: Unique conversation identifier
            persona_name: Name of the persona
            turns: List of turn dicts with 'speaker' and 'message'
            facts: Facts that were seeded
            contradictions: Contradictions that were injected

        Returns:
            MemoryScorecard with detailed analysis
        """
        scorecard = MemoryScorecard(
            conversation_id=conversation_id,
            persona_name=persona_name,
            total_turns=len(turns),
        )

        # Score fact recalls
        for fact in facts:
            if fact.recalled_at_turn is not None and fact.seeded_at_turn is not None:
                score = self._score_fact_recall(fact, turns)
                scorecard.fact_scores.append(score)

        # Score contradiction detection
        for pair in contradictions:
            if pair.contradiction_turn is not None:
                score = self._score_contradiction(pair, turns)
                scorecard.contradiction_scores.append(score)

        # Build decay curve from fact scores
        scorecard.decay_curve = self._build_decay_curve(scorecard, turns)

        return scorecard

    def _score_fact_recall(
        self, fact: MemoryFact, turns: List[Dict[str, str]]
    ) -> MemoryScore:
        """Score how well the bot recalled a specific fact."""
        # Find the bot's response after the recall turn
        recall_turn_idx = (fact.recalled_at_turn or 0) * 2  # Approximate index
        bot_response = ""

        # Search for bot response near the recall turn
        for i, turn in enumerate(turns):
            if turn.get("speaker") == "bot" and i >= (recall_turn_idx - 2):
                # Check if the preceding user message was the recall prompt
                if i > 0:
                    prev = turns[i - 1].get("message", "")
                    # Fuzzy match — check if recall prompt keywords appear
                    recall_keywords = fact.recall_prompt.lower().split()[:5]
                    if any(kw in prev.lower() for kw in recall_keywords):
                        bot_response = turn.get("message", "")
                        break

        # If no exact match found, use the bot response closest to recall turn
        if not bot_response and recall_turn_idx < len(turns):
            for i in range(max(0, recall_turn_idx - 2), min(len(turns), recall_turn_idx + 3)):
                if turns[i].get("speaker") == "bot":
                    bot_response = turns[i].get("message", "")
                    break

        # Score based on keyword presence
        bot_response_lower = bot_response.lower()
        found = [kw for kw in fact.expected_in_response if kw.lower() in bot_response_lower]
        missing = [kw for kw in fact.expected_in_response if kw.lower() not in bot_response_lower]

        if len(fact.expected_in_response) > 0:
            score = len(found) / len(fact.expected_in_response)
        else:
            score = 0.0

        return MemoryScore(
            fact_id=fact.fact_id,
            seeded_at_turn=fact.seeded_at_turn or 0,
            recalled_at_turn=fact.recalled_at_turn or 0,
            turn_gap=(fact.recalled_at_turn or 0) - (fact.seeded_at_turn or 0),
            recall_score=score,
            expected_keywords=fact.expected_in_response,
            found_keywords=found,
            missing_keywords=missing,
        )

    def _score_contradiction(
        self, pair: ContradictionPair, turns: List[Dict[str, str]]
    ) -> ContradictionScore:
        """Score whether the bot noticed a contradiction."""
        # Find bot response after the contradiction turn
        contradiction_idx = (pair.contradiction_turn or 0) * 2
        bot_response = ""

        for i in range(max(0, contradiction_idx - 1), min(len(turns), contradiction_idx + 3)):
            if turns[i].get("speaker") == "bot":
                bot_response = turns[i].get("message", "")
                break

        # Check for contradiction awareness signals
        awareness_signals = [
            "earlier you",
            "previously",
            "you mentioned",
            "you said",
            "before you",
            "contradiction",
            "different from",
            "inconsistent",
            "changed",
            "originally",
            "first you",
            "but earlier",
            "doesn't match",
            "mismatch",
            "discrepancy",
            "conflicting",
            "not what you",
            "that's not what",
            "wait,",
            "hold on",
            "actually, you",
            "i notice",
            "just to clarify",
            "slight difference",
        ]

        response_lower = bot_response.lower()
        noticed = any(signal in response_lower for signal in awareness_signals)

        return ContradictionScore(
            pair_id=pair.pair_id,
            original_turn=pair.original_turn or 0,
            contradiction_turn=pair.contradiction_turn or 0,
            bot_noticed=noticed,
            evidence=bot_response[:200] if noticed else "No contradiction awareness detected.",
        )

    def _build_decay_curve(
        self, scorecard: MemoryScorecard, turns: List[Dict[str, str]]
    ) -> List[DecayPoint]:
        """
        Build a memory decay curve from fact recall scores.
        Creates interpolated points between recall events.
        """
        if not scorecard.fact_scores:
            return []

        points: List[DecayPoint] = []

        # Start with perfect memory
        points.append(DecayPoint(turn=1, memory_score=1.0, quality_score=1.0))

        # Add points from actual recall events
        for score in sorted(scorecard.fact_scores, key=lambda s: s.recalled_at_turn):
            points.append(
                DecayPoint(
                    turn=score.recalled_at_turn,
                    memory_score=score.recall_score,
                    quality_score=score.recall_score,  # Approximate
                )
            )

        # Add final point
        if len(turns) > 0:
            last_score = scorecard.fact_scores[-1].recall_score if scorecard.fact_scores else 0.5
            points.append(
                DecayPoint(
                    turn=len(turns) // 2,  # Approximate turn count
                    memory_score=last_score,
                    quality_score=last_score,
                )
            )

        return sorted(points, key=lambda p: p.turn)