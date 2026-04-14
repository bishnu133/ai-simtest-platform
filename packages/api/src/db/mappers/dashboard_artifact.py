"""DashboardArtifact mapper.

dashboard_artifacts is a precomputed projection cache — the payload
column holds a serialized DashboardSummary (or any other artifact
shape, keyed by artifact_type). It is NOT a queryable structured
entity, so the mapper is just a thin wrapper over the JSONB payload.

Turn 1b returns a small domain record that preserves the identity
fields and the payload dict; Turn 2 repositories will hydrate
DashboardSummary from the payload at the service layer as needed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DashboardArtifact


class DashboardArtifactRecord(BaseModel):
    """Thin domain shape around a dashboard_artifacts row."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    artifact_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


def dashboard_artifact_to_domain(row: DashboardArtifact) -> DashboardArtifactRecord:
    return DashboardArtifactRecord(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        workspace_id=str(row.workspace_id),
        run_id=str(row.run_id),
        artifact_type=row.artifact_type,
        payload=dict(row.payload or {}),
        created_at=row.created_at,
    )


def dashboard_artifact_to_orm(
    record: DashboardArtifactRecord,
) -> DashboardArtifact:
    return DashboardArtifact(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        run_id=record.run_id,
        artifact_type=record.artifact_type,
        payload=dict(record.payload),
        created_at=record.created_at,
    )
