"""Run domain models for Week 6a (v1.2.2 §13).

A `RunRecord` is the durable representation of a single simulation run.
Week 6a stores these in an in-memory repository behind a protocol; real
Postgres lands later without changing this shape.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import utcnow


__all__ = [
    "RunStatus",
    "RunRecord",
    "RunSummary",
    "RUN_RESULT_SCHEMA_VERSION",
    "ResultStatus",
    "RunResult",
]


class RunStatus(str, Enum):
    """Run lifecycle states. Maps 1:1 to AuditActions.RUN_* codes."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


class RunRecord(BaseModel):
    """Durable run record. Owned by RunRepository."""

    model_config = ConfigDict(frozen=False)

    run_id: str
    tenant_id: str
    workspace_id: str
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    engine_version: str = "engine_v1"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunSummary(BaseModel):
    """Lightweight projection used in list endpoints."""

    run_id: str
    status: RunStatus
    created_at: datetime
    completed_at: datetime | None = None


RUN_RESULT_SCHEMA_VERSION = "1"


class ResultStatus(str, Enum):
    """Status of a run's ENGINE OUTPUT — distinct from RunStatus (run lifecycle).

    A run may be RunStatus.COMPLETED yet carry ResultStatus.PARTIAL or FAILED
    output; conversely a run may be RunStatus.FAILED with no RunResult at all.
    """

    PRODUCED = "produced"  # full, usable engine output
    PARTIAL = "partial"    # usable but incomplete (some judges/metrics missing)
    FAILED = "failed"      # RunResult exists but no usable output (NOT RunStatus.FAILED)


class RunResult(BaseModel):
    """Durable snapshot of a run's engine output. Separate from RunRecord
    (run lifecycle). Persistence lands in Engine-Integration Slice 2; an
    in-memory store backs dev/test now.

    Identity/version strings are constrained non-empty (min_length=1) to
    prevent silent key pollution in the in-memory and future DB stores.
    """

    model_config = ConfigDict(frozen=False)

    run_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    result_status: ResultStatus
    engine_version: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    schema_version: str = Field(default=RUN_RESULT_SCHEMA_VERSION, min_length=1)
    summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    judge_scores: dict[str, Any] = Field(default_factory=dict)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    raw_engine_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
