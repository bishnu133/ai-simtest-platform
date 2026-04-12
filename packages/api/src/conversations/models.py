"""Conversation storage models.

A conversation has two distinct representations:

  1. ConversationSummary — small, searchable, lives in PG
     Contains: verdicts, judge scores, turn count, persona name, metadata
     Used for: list views, dashboards, filtering, aggregation

  2. ConversationTranscript — large, turn-by-turn, lives in R2
     Contains: full message history, tool calls, retrieved context
     Used for: drill-down viewer, replay, export

This split is the core hybrid-storage pattern for Week 5: frequently
read metadata stays close to the query engine, large rarely-read
payloads live on cheap object storage with signed-URL access.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import utcnow
from src.storage.models import ObjectRef


ConversationVerdict = Literal["pass", "fail", "error", "pending"]


class Turn(BaseModel):
    """A single message exchange in a conversation transcript."""

    model_config = ConfigDict(frozen=True)

    turn_number: int = Field(ge=1)
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationTranscript(BaseModel):
    """Full turn-by-turn transcript. Stored in R2 as JSON."""

    conversation_id: str
    run_id: str
    persona_id: str
    turns: list[Turn]
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_context: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class ConversationSummary(BaseModel):
    """Searchable conversation metadata. Lives in PG.

    Carries a payload_ref pointing at the full transcript in R2. The
    transcript is fetched on demand (list views only need the summary).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Identity
    id: str
    tenant_id: str
    workspace_id: str
    run_id: str

    # Persona / bot context
    persona_id: str
    persona_name: str
    persona_type: str = "standard"

    # Verdict and scores
    verdict: ConversationVerdict = "pending"
    pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    turn_count: int = Field(default=0, ge=0)

    # Aggregated judge scores (keys are judge names, values are 0..1)
    judge_scores: dict[str, float] = Field(default_factory=dict)

    # Failure metadata (if any)
    failure_reason: str | None = None
    failure_category: str | None = None

    # Reference to the full transcript in R2
    transcript_ref: ObjectRef | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # Free-form tags for filtering
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def has_transcript(self) -> bool:
        return self.transcript_ref is not None
