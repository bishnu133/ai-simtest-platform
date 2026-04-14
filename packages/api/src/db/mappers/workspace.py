"""Workspace domain ↔ ORM mapper."""
from __future__ import annotations

from src.db.domain import WorkspaceRecord
from src.db.models import Workspace


def workspace_to_domain(row: Workspace) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        name=row.name,
        is_default=bool(row.is_default),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def workspace_to_orm(record: WorkspaceRecord) -> Workspace:
    return Workspace(
        id=record.id,
        tenant_id=record.tenant_id,
        name=record.name,
        is_default=record.is_default,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
