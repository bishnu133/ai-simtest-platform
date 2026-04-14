"""Comparison domain models (Week 6a Turn 3, v1.2.2 §11.6, §M2)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import utcnow
from src.runs.models import RunStatus


class ComparisonStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RegressionSignal(BaseModel):
    """Typed regression signal (v1.2.2 §11 strongly recommended #4)."""

    type: str  # e.g. "pass_rate_drop", "judge_drift", "new_failure_cluster"
    severity: Literal["info", "warning", "critical"]
    metric: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MetricDelta(BaseModel):
    metric: str
    left: float
    right: float
    delta: float


class RunProvenance(BaseModel):
    run_id: str
    engine_version: str
    asset_versions_used: list[str] = Field(default_factory=list)
    run_status: RunStatus


class ComparisonRecord(BaseModel):
    """First-class comparison resource (v1.2.2 §M2 hybrid storage rule).

    Inline fields are the SINGLE SOURCE OF TRUTH for both
    GET /v1/comparisons/{id} and GET /v1/comparisons/{id}/regression-signals.
    `result_ref` is reserved for Week 6b/7 heavy diff artifacts and is
    None for all comparisons created in 6a.
    """

    model_config = ConfigDict(frozen=False)

    id: str
    workspace_id: str
    tenant_id: str
    left_run_id: str
    right_run_id: str
    status: ComparisonStatus = ComparisonStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    engine_version: str = "engine_v1"

    # Source-of-truth inline fields (populated in 6a)
    regression_signals: list[RegressionSignal] = Field(default_factory=list)
    left_provenance: RunProvenance | None = None
    right_provenance: RunProvenance | None = None

    # §11.6 reserved fields — null in 6a, populated by future compute engine
    verdict: Literal["regression", "improvement", "neutral", "inconclusive"] | None = None
    evidence_count: int | None = None
    metric_deltas: list[MetricDelta] | None = None
    comparison_profile: str | None = None

    # Reserved for heavy artifacts (always None in 6a per v1.2.2 §M2)
    result_ref: str | None = None


class CreateComparisonRequest(BaseModel):
    left_run_id: str
    right_run_id: str


class ComparisonListResponse(BaseModel):
    items: list[ComparisonRecord]
    next_cursor: str | None = None
    has_more: bool = False
