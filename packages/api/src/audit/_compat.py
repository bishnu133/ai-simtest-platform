"""Slice 7: pure-domain conversion helpers from sync TenantContext to
the Slice 1 async-path domain types (TenantAuditEvent / PretenantAuditEvent).

Used by the 8 migrated Tier-1 call sites in src/auth/middleware.py and
src/auth/bootstrap.py.

Pure-domain invariant (mirrors src/audit/context.py constraint):
  This module imports ONLY from:
    * Python stdlib
    * src.audit.context (Slice 1 domain types)
    * src.common.models (TenantContext, ActorRef — sync domain)

  It MUST NOT import from:
    * src.db.*, src.app_factory*, src.audit.logger, src.audit.repository
    * SQLAlchemy, asyncpg, pydantic-settings, FastAPI

  Adding such imports would create a runtime dependency cycle when
  src/auth/middleware.py imports these helpers at module load.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.audit.context import (
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.common.models import TenantContext


def to_tenant_audit_event(
    ctx: TenantContext,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> TenantAuditEvent:
    """Build a Slice 1 TenantAuditEvent from a sync TenantContext.

    Used at migrated Tier-1 tenant-path call sites:
      * M8 (middleware.py)        — auth.accepted
      * B1 (bootstrap.py)         — auth.membership_denied
      * B2 (bootstrap.py)         — auth.bootstrap_created_tenant
      * B3 (bootstrap.py)         — tenant.created

    Coerces string IDs in TenantContext to UUID objects required by
    AuditContext (Slice 1 sacred — domain uses UUID for tenant/workspace
    identity).

    Raises:
        ValueError: if ctx.tenant_id or ctx.workspace_id is not a valid
            UUID string. This indicates the caller has a malformed
            context — fail loud rather than emit a corrupt audit row.
    """
    return TenantAuditEvent(
        action=action,
        context=AuditContext(
            tenant_id=UUID(ctx.tenant_id),
            actor_id=ctx.actor.actor_id,
            actor_type=ctx.actor.actor_type,
            workspace_id=UUID(ctx.workspace_id) if ctx.workspace_id else None,
            correlation_id=ctx.correlation_id,
        ),
        resource_type=resource_type,
        resource_id=resource_id,
        details=dict(metadata) if metadata else {},
    )


def to_pretenant_audit_event(
    action: str,
    actor_id: str,
    actor_type: str,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> PretenantAuditEvent:
    """Build a Slice 1 PretenantAuditEvent.

    Used at migrated Tier-1 pretenant-path call sites:
      * M1 (middleware.py) — auth.rejected, missing bearer
      * M2 (middleware.py) — auth.rejected, provider.verify failed
      * M5 (middleware.py) — auth.rejected, workspace not found
      * M7 (middleware.py) — auth.rejected, generic bootstrap failure

    Per Slice 0/4 contract, action must be in PRETENANT_ACTION_ALLOWLIST
    (currently only "auth.rejected"). Slice 1's PretenantAuditEvent
    __post_init__ validator enforces this — passing a disallowed action
    raises ValueError at construction.

    PretenantAuditEvent has no tenant_id/workspace_id/resource_type/
    resource_id fields. Callers that want to preserve resource_type and
    resource_id semantics should fold them into the metadata dict.
    """
    return PretenantAuditEvent(
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        details=dict(metadata) if metadata else {},
        correlation_id=correlation_id,
    )
