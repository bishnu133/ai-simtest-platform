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
