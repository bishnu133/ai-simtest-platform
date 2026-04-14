"""Results / dashboard domain models (Week 6a Turn 2, v1.2.2 §11, §S5)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import utcnow
from src.runs.models import RunStatus


class JudgeBreakdown(BaseModel):
    judge_name: str
    pass_count: int = 0
    fail_count: int = 0
    avg_score: float = 0.0


class FailurePattern(BaseModel):
    cluster_id: str
    label: str
    count: int
    sample_conversation_ids: list[str] = Field(default_factory=list)


class CoverageMetric(BaseModel):
    dimension: str  # e.g. "topic", "persona_type", "risk", "workflow"
    covered: int
    total: int

    @property
    def percentage(self) -> float:
        return (self.covered / self.total * 100.0) if self.total else 0.0


class RunOverview(BaseModel):
    run_id: str
    status: RunStatus
    pass_rate: float = 0.0
    total_conversations: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RunTimelineEvent(BaseModel):
    action: str
    actor_id: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunTimelineResponse(BaseModel):
    """Run lifecycle timeline (v1.2.2 §11.7 — transitional, audit-backed in 6a)."""

    run_id: str
    events: list[RunTimelineEvent]


class DashboardSummary(BaseModel):
    """Composite above-the-fold dashboard payload (v1.2.2 §S2 §11.6)."""

    model_config = ConfigDict(frozen=False)

    overview: RunOverview
    judges: list[JudgeBreakdown]
    top_failures: list[FailurePattern]
    coverage: list[CoverageMetric]

    # Provenance (v1.2.2 §11 strongly recommended #1)
    engine_version: str
    asset_versions_used: list[str] = Field(default_factory=list)
    run_status: RunStatus
    generated_at: datetime = Field(default_factory=utcnow)

    # Operational freshness (v1.2.2 §S5)
    data_source_version: str = "in_memory_v1"
    artifact_generated_at: datetime
