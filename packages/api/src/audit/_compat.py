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

from src.api.errors import APIError
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


def to_tenant_audit_event_from_exc(
    exc: APIError,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> TenantAuditEvent:
    """Build a Slice 1 TenantAuditEvent from an APIError carrying
    tenant_id / workspace_id in exc.details.

    Used by Tier-1 middleware exception paths (M3, M4) where the
    exception has been enriched at its raise site to carry the tenant
    context that bootstrap resolved before raising. Per FH-S7.5 plan
    v0.2.1 §6.1 / §7.1 / §7.2:

      * M3 (middleware.py:301) — catches NotMemberOfTenant; tenant_id
        comes from src/auth/authz.py:154 enrichment.
      * M4 (middleware.py:317) — catches CrossTenantForbidden; tenant_id
        comes from raise-site enrichment in src/workspaces/repository.py
        (B.4 — lines 134 and 235).

    Pure-domain boundary: this function imports NO VerifiedClaims or
    auth-provider type. Call sites pass actor_id explicitly (typically
    ``actor_id=claims.user_id``), preserving the additive-only invariant
    of src/audit/_compat.py per FH-S7.5 plan §6.2.

    actor_type is hard-coded to ``"human"`` per FH-S7.5 plan §0.1 lock.
    Whether M3/M4/M6 should write under ``actor_type="system"`` is
    deferred to backlog item F-1 (FH-S7.5-Adjacent-ActorType-Semantics).

    Fail-loud contract (FH-S7.5 plan §6.1, risk R8):
      Missing or unparseable tenant_id raises ValueError rather than
      silently emitting a corrupt audit row with a sentinel. The
      error message names ``type(exc).__name__`` so middleware logs
      identify the upstream raise site whose details are malformed.
      A silently-emitted row with a fabricated tenant_id is strictly
      worse than a 500 with a clear stack trace — the latter is
      diagnosable; the former poisons the audit table.

    Args:
        exc: An APIError instance whose ``details`` dict carries
            ``tenant_id`` (required) and optionally ``workspace_id``.
        actor_id: Stable actor identifier — typically
            ``claims.user_id`` at the middleware call site.
        action: Audit action constant from ``src.audit.actions``.
            Must NOT be in PRETENANT_ACTION_ALLOWLIST (TenantAuditEvent
            __post_init__ enforces this).
        resource_type: Resource type string for the audit row.
        resource_id: Resource id string for the audit row.
        metadata: Optional details dict for the audit row. Copied
            defensively via ``dict(metadata)``.

    Returns:
        A constructed TenantAuditEvent ready to pass to
        ``audit_logger.aemit_tenant_event_safe(...)``.

    Raises:
        ValueError: if ``exc.details["tenant_id"]`` is missing, empty,
            or not castable to UUID. Also raised if
            ``exc.details["workspace_id"]``, when present and truthy,
            is not castable to UUID.
    """
    tid_raw = exc.details.get("tenant_id")
    if not tid_raw:
        raise ValueError(
            f"to_tenant_audit_event_from_exc: {type(exc).__name__} "
            f"has no tenant_id in exc.details (got {tid_raw!r}); "
            f"the raise site must enrich exc.details with tenant_id "
            f"before raising (FH-S7.5 plan §6.1 / §8 raise-site enrichment)"
        )
    try:
        tid_uuid = UUID(str(tid_raw))
    except (ValueError, TypeError) as parse_exc:
        raise ValueError(
            f"to_tenant_audit_event_from_exc: {type(exc).__name__} "
            f"exc.details['tenant_id']={tid_raw!r} is not a valid UUID "
            f"({parse_exc})"
        ) from parse_exc

    ws_raw = exc.details.get("workspace_id")
    if ws_raw:
        try:
            ws_uuid: UUID | None = UUID(str(ws_raw))
        except (ValueError, TypeError) as parse_exc:
            raise ValueError(
                f"to_tenant_audit_event_from_exc: {type(exc).__name__} "
                f"exc.details['workspace_id']={ws_raw!r} is not a valid UUID "
                f"({parse_exc})"
            ) from parse_exc
    else:
        ws_uuid = None

    return TenantAuditEvent(
        action=action,
        context=AuditContext(
            tenant_id=tid_uuid,
            actor_id=actor_id,
            actor_type="human",  # §0.1 lock; F-1 backlog tracks system/human review
            workspace_id=ws_uuid,
            correlation_id=None,
        ),
        resource_type=resource_type,
        resource_id=resource_id,
        details=dict(metadata) if metadata else {},
    )
