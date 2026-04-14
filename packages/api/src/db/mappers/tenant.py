"""Tenant domain ↔ ORM mapper."""
from __future__ import annotations

from src.db.domain import TenantRecord
from src.db.models import Tenant


def tenant_to_domain(row: Tenant) -> TenantRecord:
    return TenantRecord(
        id=str(row.id),
        name=row.name,
        slug=row.slug,
        clerk_org_id=row.clerk_org_id,
        plan_id=row.plan_id,
        settings=dict(row.settings or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def tenant_to_orm(record: TenantRecord) -> Tenant:
    return Tenant(
        id=record.id,
        name=record.name,
        slug=record.slug,
        clerk_org_id=record.clerk_org_id,
        plan_id=record.plan_id,
        settings=dict(record.settings),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
