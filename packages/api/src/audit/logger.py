"""Append-only audit logger.

Every state-changing operation writes an immutable audit event. The audit
log is the backbone of the provenance and compliance story: given any run,
we can reconstruct exactly who did what with which asset versions.

Design principles:
  - Append-only: audit events are never updated or deleted
  - Tenant-scoped: every event carries tenant_id and workspace_id
  - Actor-stamped: every event carries ActorRef
  - Structured: action + resource_type + resource_id + metadata dict
  - Correlation-aware: events in the same request share correlation_id

Slice 5 additions (additive only, Option A locked at v0.3.4):
  - AuditLogger now accepts an AuditEventRepository at construction
    (default: InMemoryAuditEventRepository). Sync write() is unchanged.
  - Two async methods aemit_tenant_event / aemit_pretenant_event delegate
    to the repository for Tier-1 RLS-aware persistence.

Slice 6 additions (additive only):
  - bind_repository(repo) setter mutates self._audit_repository in-place.
    Used by app_factory._bind_services to swap PostgresAuditEventRepository
    into the module singleton when settings.use_postgres_audit_events=True.

Slice 7 additions (additive only):
  - aemit_tenant_event_safe / aemit_pretenant_event_safe — fail-open
    variants of the Slice 5 aemit_* methods. Suppress exceptions, log
    them, return None on failure. Used by Tier-1 auth-path call sites
    where audit must NOT break auth.
  - query_all_events — unified read across sync _events list AND the
    bound async repository (when InMemoryAuditEventRepository). Used by
    auth tests after the 8-site migration.
  - clear_all — reset BOTH sync _events AND async repository for test
    isolation across both paths.

  Slice 7 migrates 8 of 11 Tier-1 call sites. The other 3 (M3, M4, M6 —
  sentinel-context auth.membership_denied / auth.tenant_state_invalid)
  are deferred to FH-S7.5-Sentinel-Auth-Audit-Semantics because the
  PRETENANT_ACTION_ALLOWLIST only permits auth.rejected — collapsing
  semantically distinct actions into auth.rejected would lose dashboard-
  alert design intent.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.audit.context import PretenantAuditEvent, TenantAuditEvent
from src.audit.repository import AuditEventRepository, InMemoryAuditEventRepository
from src.common.models import ActorRef, TenantContext, utcnow

logger = logging.getLogger(__name__)


class AuditActions:
    """Canonical action names. All audit events use one of these."""

    # Asset lifecycle
    ASSET_CREATED = "asset.created"
    ASSET_UPDATED = "asset.updated"
    ASSET_VERSION_CREATED = "asset.version_created"
    ASSET_APPROVED = "asset.approved"
    ASSET_DEPRECATED = "asset.deprecated"
    ASSET_CLONED = "asset.cloned"
    ASSET_DELETED = "asset.deleted"
    ASSET_PAYLOAD_UPLOADED = "asset.payload_uploaded"
    ASSET_PAYLOAD_DOWNLOADED = "asset.payload_downloaded"

    # Run lifecycle
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    # Conversation storage
    CONVERSATION_STORED = "conversation.stored"
    CONVERSATION_FETCHED = "conversation.fetched"
    CONVERSATION_DELETED = "conversation.deleted"
    CONVERSATION_TRANSCRIPT_VIEWED = "conversation.transcript_viewed"
    CONVERSATION_DOWNLOAD_URL_ISSUED = "conversation.download_url_issued"
    COMPARISON_CREATED = "comparison.created"
    COMPARISON_VIEWED = "comparison.viewed"

    # Tenant/workspace
    TENANT_CREATED = "tenant.created"
    WORKSPACE_CREATED = "workspace.created"
    USER_INVITED = "user.invited"

    # Turn 3 additions — auth lifecycle events.
    AUTH_ACCEPTED = "auth.accepted"
    AUTH_REJECTED = "auth.rejected"
    AUTH_MEMBERSHIP_DENIED = "auth.membership_denied"
    AUTH_BOOTSTRAP_CREATED_TENANT = "auth.bootstrap_created_tenant"
    AUTH_TENANT_STATE_INVALID = "auth.tenant_state_invalid"

    # Turn 2 — secret-rejection event codes (constants only, no emission).
    ASSET_CREATE_REJECTED_SECRET_LEAK = "asset.create_rejected_secret_leak"
    RUN_CREATE_REJECTED_SECRET_LEAK = "run.create_rejected_secret_leak"
    COMPARISON_CREATE_REJECTED_SECRET_LEAK = "comparison.create_rejected_secret_leak"
    CONVERSATION_STORE_REJECTED_SECRET_LEAK = "conversation.store_rejected_secret_leak"


@dataclass
class AuditEvent:
    """Single audit event. Immutable once written."""

    event_id: str
    tenant_id: str
    workspace_id: str
    actor: ActorRef
    action: str
    resource_type: str
    resource_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    occurred_at: Any = None  # datetime, set in __post_init__

    def __post_init__(self):
        if self.occurred_at is None:
            self.occurred_at = utcnow()


class AuditLogger:
    """Append-only audit event logger.

    See module docstring for slice-by-slice surface evolution.
    """

    def __init__(
        self,
        repository: AuditEventRepository | None = None,
    ) -> None:
        self._events: list[AuditEvent] = []
        # Slice 5 Q1=B1 + Q4=N2: constructor injection with InMemory default.
        # Slice 6 uses bind_repository() to swap in Postgres for production.
        self._audit_repository: AuditEventRepository = (
            repository if repository is not None else InMemoryAuditEventRepository()
        )

    def bind_repository(self, repository: AuditEventRepository) -> None:
        """Replace the bound async repository in-place. Slice 6 Q1=B2."""
        self._audit_repository = repository

    def write(
        self,
        ctx: TenantContext,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Write a single audit event. Never raises — audit must not break business logic."""
        try:
            event = AuditEvent(
                event_id=str(uuid.uuid4()),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=metadata or {},
                correlation_id=ctx.correlation_id,
            )
            self._events.append(event)
            logger.info(
                "audit: %s %s/%s by %s in %s/%s",
                action,
                resource_type,
                resource_id,
                ctx.actor.actor_id,
                ctx.tenant_id,
                ctx.workspace_id,
            )
            return event
        except Exception as exc:  # pragma: no cover — defensive, never silently swallowed
            logger.error("audit write failed: %s", exc, exc_info=True)
            raise

    async def aemit_tenant_event(
        self,
        event: TenantAuditEvent,
        session: AsyncSession | None = None,
    ) -> UUID:
        """Slice 5: async persist a tenant-scoped audit event via the
        bound repository. Failure semantics Q2=F1: exceptions propagate.
        """
        return await self._audit_repository.append_tenant_event(event, session=session)

    async def aemit_pretenant_event(
        self,
        event: PretenantAuditEvent,
        session: AsyncSession | None = None,
    ) -> UUID:
        """Slice 5: async persist a pretenant audit event via the
        bound repository's SECURITY DEFINER path. Failure semantics
        Q2=F1: exceptions propagate.
        """
        return await self._audit_repository.append_pretenant_event(event, session=session)

    async def aemit_tenant_event_safe(
        self,
        event: TenantAuditEvent,
        session: AsyncSession | None = None,
    ) -> UUID | None:
        """Slice 7 Q4: fail-open variant of aemit_tenant_event.

        Suppresses all exceptions from the underlying repository, logs
        them via the module logger, and returns None on failure. On
        success returns the persisted row UUID.

        Used by Tier-1 auth-path call sites (M8 in middleware.py, B1/B2/B3
        in bootstrap.py) where audit must NOT break auth. A failed audit
        emit is a monitoring concern, not a request-failure concern.
        """
        try:
            return await self.aemit_tenant_event(event, session=session)
        except Exception:
            logger.exception(
                "audit aemit_tenant_event suppressed (action=%s, resource=%s/%s)",
                event.action,
                event.resource_type,
                event.resource_id,
            )
            return None

    async def aemit_pretenant_event_safe(
        self,
        event: PretenantAuditEvent,
        session: AsyncSession | None = None,
    ) -> UUID | None:
        """Slice 7 Q4: fail-open variant of aemit_pretenant_event.

        Suppresses all exceptions (including AuditEventInsertRejected
        from the SECURITY DEFINER function), logs them, and returns None.

        Used by Tier-1 pretenant call sites (M1, M2, M5, M7 in
        middleware.py) where audit must NOT break auth.
        """
        try:
            return await self.aemit_pretenant_event(event, session=session)
        except Exception:
            logger.exception(
                "audit aemit_pretenant_event suppressed (action=%s, actor=%s)",
                event.action,
                event.actor_id,
            )
            return None

    def query(
        self,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditEvent]:
        """Query the sync in-memory _events list only.

        Slice 5 sacred — signature and body byte-identical.

        Slice 7 note: callers that need events from BOTH sync and
        migrated async paths should use query_all_events() instead.
        """
        results = self._events
        if tenant_id:
            results = [e for e in results if e.tenant_id == tenant_id]
        if workspace_id:
            results = [e for e in results if e.workspace_id == workspace_id]
        if action:
            results = [e for e in results if e.action == action]
        if resource_id:
            results = [e for e in results if e.resource_id == resource_id]
        return results

    def query_all_events(
        self,
        action: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditEvent]:
        """Slice 7: unified read across sync (_events) and async
        (InMemoryAuditEventRepository) audit paths.

        Returns events from BOTH:
          * sync _events list (sync write() — unmigrated call sites)
          * bound async repository (aemit_*_safe — migrated call sites
            in middleware.py M1/M2/M5/M7/M8 and bootstrap.py B1/B2/B3)

        Async events are coerced to AuditEvent shape for type-compatible
        iteration. Pretenant events preserve resource_type/resource_id
        from the PretenantAuditEvent top-level fields (Slice 7.8 F-26);
        tenant_id/workspace_id remain empty because pretenant events have
        no tenant/workspace context.

        Returns [] from the async path when bound to a non-InMemory
        repository (e.g. PostgresAuditEventRepository). Tests that
        need durable-path verification should use the Slice 5/db tests
        which assert directly against PG rows.

        Used by tests/auth/*.py after the Slice 7 migration. Tests
        replace audit_logger.query(...) calls with
        audit_logger.query_all_events(...) — single sed substitution.
        """
        # Sync path: already in AuditEvent shape.
        results: list[AuditEvent] = list(self._events)

        # Async path: coerce TenantAuditEvent / PretenantAuditEvent to
        # AuditEvent shape so the return type is uniform.
        if isinstance(self._audit_repository, InMemoryAuditEventRepository):
            for tenant_event in self._audit_repository._tenant.values():
                results.append(
                    AuditEvent(
                        event_id=str(uuid.uuid4()),
                        tenant_id=str(tenant_event.context.tenant_id),
                        workspace_id=(
                            str(tenant_event.context.workspace_id)
                            if tenant_event.context.workspace_id
                            else ""
                        ),
                        actor=ActorRef(
                            actor_id=tenant_event.context.actor_id,
                            actor_type=tenant_event.context.actor_type,
                        ),
                        action=tenant_event.action,
                        resource_type=tenant_event.resource_type,
                        resource_id=tenant_event.resource_id,
                        metadata=dict(tenant_event.details),
                        correlation_id=tenant_event.context.correlation_id,
                    )
                )
            for pretenant_event in self._audit_repository._pretenant.values():
                results.append(
                    AuditEvent(
                        event_id=str(uuid.uuid4()),
                        tenant_id="",  # pretenant has no tenant_id
                        workspace_id="",  # pretenant has no workspace_id
                        actor=ActorRef(
                            actor_id=pretenant_event.actor_id,
                            actor_type=pretenant_event.actor_type,
                        ),
                        action=pretenant_event.action,
                        resource_type=pretenant_event.resource_type,
                        resource_id=pretenant_event.resource_id,
                        metadata=dict(pretenant_event.details),
                        correlation_id=pretenant_event.correlation_id,
                    )
                )

        # Apply filters — mirror existing query() filter semantics.
        if action:
            results = [e for e in results if e.action == action]
        if tenant_id:
            results = [e for e in results if e.tenant_id == tenant_id]
        if workspace_id:
            results = [e for e in results if e.workspace_id == workspace_id]
        if resource_id:
            results = [e for e in results if e.resource_id == resource_id]
        return results

    def clear(self) -> None:
        """Reset the in-memory log (sync _events list). Test-only.

        Slice 5 sacred — signature and body byte-identical.

        Slice 7 note: callers that need cross-path test isolation
        should use clear_all() to reset both sync and async storage.
        """
        self._events.clear()

    def clear_all(self) -> None:
        """Slice 7: reset BOTH sync _events AND async repository.

        Used by auth tests for cross-path isolation after the Slice 7
        migration. Equivalent to clear() + repository state reset.

        When bound to PostgresAuditEventRepository, only sync _events
        clears — the PG audit_events table is NOT truncated (production
        safety). Tests that need PG isolation should use the
        clean_db / migrated_db fixtures from tests/db/conftest.py.
        """
        self._events.clear()
        if isinstance(self._audit_repository, InMemoryAuditEventRepository):
            self._audit_repository._tenant.clear()
            self._audit_repository._pretenant.clear()


# Module-level singleton. Services import this directly.
audit_logger = AuditLogger()
