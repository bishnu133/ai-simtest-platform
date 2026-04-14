"""Membership domain ↔ ORM mapper.

Handles the §6.5 dual-membership shape: `workspace_id=None` is a
tenant-level row, a UUID value is a workspace-level row. The mapper is
agnostic to which shape a particular row represents; callers decide
which shape to create.
"""
from __future__ import annotations

from typing import cast

from src.db.domain import MembershipRecord, Role
from src.db.models import Membership


def membership_to_domain(row: Membership) -> MembershipRecord:
    return MembershipRecord(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        user_id=row.user_id,
        # workspace_id is nullable — preserve None for tenant-level rows.
        workspace_id=str(row.workspace_id) if row.workspace_id is not None else None,
        role=cast(Role, row.role),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def membership_to_orm(record: MembershipRecord) -> Membership:
    return Membership(
        id=record.id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        workspace_id=record.workspace_id,
        role=record.role,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
