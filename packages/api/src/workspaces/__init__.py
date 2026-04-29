"""Workspace lookup and creation — Turn 2 Step 2 (Category B, tenant-scoped).

Consumers import the Protocol and the record type from this module:

    from src.workspaces import WorkspaceRepository, WorkspaceRecord
"""
from __future__ import annotations

from src.db.domain import WorkspaceRecord
from src.workspaces.repository import (
    InMemoryWorkspaceRepository,
    PostgresWorkspaceRepository,
    WorkspaceRepository,
)

__all__ = [
    "WorkspaceRecord",
    "WorkspaceRepository",
    "InMemoryWorkspaceRepository",
    "PostgresWorkspaceRepository",
]
