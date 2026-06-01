"""
Core data models for AI SimTest.
All domain objects used across the platform.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# Enums
# ============================================================

class PersonaType(str, Enum):
    STANDARD = "standard"
    EDGE_CASE = "edge_case"
    ADVERSARIAL = "adversarial"


class TechnicalLevel(str, Enum):
    NOVICE = "novice"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"


class JudgmentLabel(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class SimulationStatus(str, Enum):
    PENDING = "pending"
    GENERATING_PERSONAS = "generating_personas"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    JUDGING = "judging"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================
# Persona Models
# ============================================================

class Persona(BaseModel):
    """A simulated user persona for testing."""
    id: str = Field(default_factory=lambda: f"persona_{uuid.uuid4().hex[:8]}")
    name: str
    age: int | None = None
    role: str
    technical_level: TechnicalLevel = TechnicalLevel.INTERMEDIATE
    goals: list[str]
    tone: str = "neutral"
    domain_knowledge: str = "intermediate"
    conversation_style: str = "balanced"
    special_characteristics: list[str] = Field(default_factory=list)
    persona_type: PersonaType = PersonaType.STANDARD
    adversarial_tactics: list[str] | None = None
    topics: list[str] = Field(default_factory=list)
    target_conversation_turns: int = 10
    system_prompt: str = ""

    @property
    def is_adversarial(self) -> bool:
        return self.persona_type == PersonaType.ADVERSARIAL


# ============================================================
# Conversation Models
# ============================================================

class Turn(BaseModel):
    """A single turn in a conversation."""
    id: str = Field(default_factory=lambda: f"turn_{uuid.uuid4().hex[:8]}")
    speaker: str
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None


class Conversation(BaseModel):
    """A complete multi-turn conversation."""
    id: str = Field(default_factory=lambda: f"conv_{uuid.uuid4().hex[:8]}")
    persona_id: str
    turns: list[Turn] = Field(default_factory=list)
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def bot_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.speaker == "bot"]

    @property
    def user_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.speaker == "user"]

    def format_history(self, max_turns: int | None = None) -> str:
        turns = self.turns[-max_turns:] if max_turns else self.turns
        lines = []
        for t in turns:
            role = "User" if t.speaker == "user" else "Bot"
            lines.append(f"{role}: {t.message}")
        return "\n".join(lines)


# ============================================================
# Judgment Models
# ============================================================

class JudgmentResult(BaseModel):
    """Result from a single judge evaluating a response."""
    judge_name: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    severity: Severity = Severity.INFO
    message: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class JudgedTurn(BaseModel):
    """A bot turn with all judge evaluations."""
    turn: Turn
    judgments: list[JudgmentResult] = Field(default_factory=list)
    overall_score: float = 0.0
    overall_label: JudgmentLabel = JudgmentLabel.PASS
    issues: list[str] = Field(default_factory=list)


class JudgedConversation(BaseModel):
    """A conversation with all turns judged."""
    conversation: Conversation
    persona: Persona
    judged_turns: list[JudgedTurn] = Field(default_factory=list)
    overall_score: float = 0.0
    failure_modes: list[str] = Field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.judged_turns:
            return 0.0
        passed = sum(1 for jt in self.judged_turns if jt.overall_label == JudgmentLabel.PASS)
        return passed / len(self.judged_turns)


# ============================================================
# Simulation Configuration
# ============================================================

class BotConfig(BaseModel):
    """Configuration for the target bot under test."""
    api_endpoint: str
    api_key: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    request_format: str = "openai"
    message_field: str = "messages"
    response_path: str = "choices.0.message.content"
    timeout_seconds: int = 30


class JudgeConfig(BaseModel):
    """Configuration for a single judge."""
    name: str
    enabled: bool = True
    weight: float = 0.25
    config: dict[str, Any] = Field(default_factory=dict)


class SimulationConfig(BaseModel):
    """Complete configuration for a simulation run."""
    id: str = Field(default_factory=lambda: f"sim_{uuid.uuid4().hex[:8]}")
    name: str = "Untitled Simulation"
    bot: BotConfig
    documentation: str = ""
    success_criteria: list[str] = Field(default_factory=list)

    num_personas: int = 20
    persona_types: dict[str, float] = Field(
        default_factory=lambda: {
            "standard": 0.70,
            "edge_case": 0.20,
            "adversarial": 0.10,
        }
    )

    max_turns_per_conversation: int = 15
    min_turns_per_conversation: int = 1
    conversations_per_persona: int = 1

    judges: list[JudgeConfig] = Field(default_factory=lambda: [
        JudgeConfig(name="grounding", weight=0.30),
        JudgeConfig(name="safety", weight=0.30),
        JudgeConfig(name="quality", weight=0.20),
        JudgeConfig(name="relevance", weight=0.20),
    ])

    max_parallel_conversations: int = 10
    require_persona_approval: bool = False

    # Score thresholds (configurable per simulation)
    pass_threshold: float = 0.7   # >= this = PASS
    warn_threshold: float = 0.5   # >= this = WARNING, < this = FAIL

    persona_generator_model: str = "gpt-4-turbo"
    user_simulator_model: str = "gpt-4-turbo"
    quality_judge_model: str = "gpt-4-turbo"


# ============================================================
# Report Models
# ============================================================

class FailurePattern(BaseModel):
    """A recurring failure pattern found across conversations."""
    pattern_name: str
    description: str
    frequency: int
    severity: Severity
    example_conversation_ids: list[str] = Field(default_factory=list)
    affected_persona_types: list[str] = Field(default_factory=list)


class ReportSummary(BaseModel):
    """High-level simulation summary."""
    simulation_id: str
    simulation_name: str
    total_personas: int
    total_conversations: int
    total_turns: int
    pass_rate: float
    average_score: float
    critical_failures: int
    warnings: int
    execution_time_seconds: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SimulationReport(BaseModel):
    """Complete simulation test report."""
    summary: ReportSummary
    failure_patterns: list[FailurePattern] = Field(default_factory=list)
    score_by_judge: dict[str, float] = Field(default_factory=dict)
    score_by_persona_type: dict[str, float] = Field(default_factory=dict)
    most_problematic_personas: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    judged_conversations: list[JudgedConversation] = Field(default_factory=list)


# ============================================================
# Simulation Run
# ============================================================

class SimulationRun(BaseModel):
    """Tracks the state of a simulation run."""
    id: str
    config: SimulationConfig
    status: SimulationStatus = SimulationStatus.PENDING
    personas: list[Persona] = Field(default_factory=list)
    conversations: list[Conversation] = Field(default_factory=list)
    report: SimulationReport | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: str | None = None