"""AuditEvent domain ↔ ORM mapper.

Translates between the new `AuditEventRecord` (src/db/domain.py) and the
persisted audit_events row. The in-memory `audit_logger` singleton used
in Week 6a (src/audit/logger.py) uses its own dataclass shape; Turn 2
introduces a Postgres-backed audit writer that speaks AuditEventRecord.

Note: `actor_type` on the ORM is VARCHAR(20) with a CHECK constraint;
the domain uses a Literal. The mapper trusts the DB CHECK to reject
bad values on write and does a `cast` at read time.
"""
from __future__ import annotations

from typing import cast

from src.db.domain import AuditEventRecord
from src.db.models import AuditEvent


ActorType = cast(type, object)  # placeholder for clarity; Literal is enforced at domain layer


def audit_event_to_domain(row: AuditEvent) -> AuditEventRecord:
    return AuditEventRecord(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        actor_id=row.actor_id,
        actor_type=cast(
            "'human' | 'service_account' | 'system' | 'support'", row.actor_type
        ),
        actor_display=row.actor_display,
        workspace_id=str(row.workspace_id) if row.workspace_id is not None else None,
        details=dict(row.details or {}),
        asset_versions=dict(row.asset_versions) if row.asset_versions else None,
        ip_address=str(row.ip_address) if row.ip_address is not None else None,
        user_agent=row.user_agent,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
    )


def audit_event_to_orm(record: AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        id=record.id,
        tenant_id=record.tenant_id,
        action=record.action,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        actor_id=record.actor_id,
        actor_type=record.actor_type,
        actor_display=record.actor_display,
        workspace_id=record.workspace_id,
        details=dict(record.details),
        asset_versions=dict(record.asset_versions) if record.asset_versions else None,
        ip_address=record.ip_address,
        user_agent=record.user_agent,
        correlation_id=record.correlation_id,
        created_at=record.created_at,
    )
