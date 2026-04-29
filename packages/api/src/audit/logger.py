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
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

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
    # Week 6a Turn 2 additions — read-sensitive audit events (v1.2.2 §11.8)
    CONVERSATION_TRANSCRIPT_VIEWED = "conversation.transcript_viewed"
    CONVERSATION_DOWNLOAD_URL_ISSUED = "conversation.download_url_issued"
    # Week 6a Turn 3 additions — comparison audit events (v1.2.2 §11.8)
    COMPARISON_CREATED = "comparison.created"
    COMPARISON_VIEWED = "comparison.viewed"

    # Tenant/workspace
    TENANT_CREATED = "tenant.created"
    WORKSPACE_CREATED = "workspace.created"
    USER_INVITED = "user.invited"

    # Turn 3 additions — auth lifecycle events (plan §8.5 mandatory
    # audit events). Emission wired by TenantContextMiddleware and the
    # bootstrap lifecycle in src/auth/.
    #   AUTH_ACCEPTED: credentials verified and membership resolved
    #   AUTH_REJECTED: 401 — missing/invalid credentials
    #   AUTH_MEMBERSHIP_DENIED: 403 — credentials ok but no role mapping
    #                           (missing provider_org_role or unknown value)
    #   AUTH_BOOTSTRAP_CREATED_TENANT: first-use tenant provisioning
    AUTH_ACCEPTED = "auth.accepted"
    AUTH_REJECTED = "auth.rejected"
    AUTH_MEMBERSHIP_DENIED = "auth.membership_denied"
    AUTH_BOOTSTRAP_CREATED_TENANT = "auth.bootstrap_created_tenant"
    # Turn 4 — bootstrap detected a structural state inconsistency
    # (e.g. tenant row present but missing default workspace, or retry
    # budget exhausted). Distinct from AUTH_REJECTED (which is for
    # credential-shaped failures) and AUTH_MEMBERSHIP_DENIED (which is
    # for membership-row absence).
    AUTH_TENANT_STATE_INVALID = "auth.tenant_state_invalid"

    # Turn 2 additions — secret-rejection event codes (constants only).
    # Per Turn 2 plan v0.6 §5.2, Turn 2 ships these as constants only;
    # no emission logic, no handler wiring. The Turn 3 FastAPI exception
    # handler for SecretLeakDetected / ValidationError consumes these
    # constants when it wires runtime audit emission. See
    # src/secrets/denylist.py::SecretLeakDetected for the validator that
    # surfaces these conditions at the API boundary as HTTP 422.
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

    In-memory implementation for testing; production uses the `audit_events`
    PG table. The interface is identical so swapping is transparent.
    """

    def __init__(self):
        self._events: list[AuditEvent] = []

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

    def query(
        self,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditEvent]:
        """Query audit events (for tests and compliance exports)."""
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

    def clear(self) -> None:
        """Reset the in-memory log. Test-only."""
        self._events.clear()


# Module-level singleton. Services import this directly.
audit_logger = AuditLogger()
