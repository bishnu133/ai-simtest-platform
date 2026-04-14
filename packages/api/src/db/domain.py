"""Domain models for tables that don't have a shipped Week 6a equivalent.

Turn 1a created ORM models for all 12 tables in plan §7.0, but only 6 of
them (assets, runs, conversation_summaries, dashboard_artifacts,
comparisons, idempotency_keys) have domain-layer Pydantic models already
in the shipped code. The other 6 (tenants, workspaces, memberships,
service_accounts, api_keys, audit_events) are platform-side tables that
the sprint is standing up for the first time.

This module holds small frozen Pydantic models for those platform-side
tables, so the Turn 1b mappers have a real domain target (not a raw
dict) and Turn 2's repositories have a typed shape to return from their
read methods.

These models follow the plan §2.6 identity contract:
  - Every *_id column is a str (UUID-as-string at the domain layer)
  - Every actor_id / user_id is a str (Clerk sub or reserved system ID)
  - Every timestamp is a timezone-aware datetime
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import utcnow


# ---------------------------------------------------------------------------
# tenants / workspaces / memberships — the identity/tenancy core
# ---------------------------------------------------------------------------


class TenantRecord(BaseModel):
    """Durable tenant row. Created by bootstrap on first login (plan §6.4).

    `clerk_org_id` is nullable because the dev tenant seeded by
    scripts/seed_dev_tenant.py does not have a Clerk org behind it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    slug: str
    clerk_org_id: str | None = None
    plan_id: str = "free"
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class WorkspaceRecord(BaseModel):
    """Workspace row. Every tenant has at least one, flagged is_default."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    name: str
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


# Local Role type — matches src/api/deps.py::Role plus 'owner' (plan §6.5
# adds 'owner' at the top of the ladder). Turn 3 will expand the shipped
# deps.py Role literal to include 'owner'; for Turn 1b we just mirror the
# full 5-role ladder here so mappers round-trip cleanly.
Role = Literal["viewer", "member", "admin", "owner", "service_account"]


class MembershipRecord(BaseModel):
    """Membership row — plan §6.5 dual-membership shape.

    `workspace_id is None` means tenant-level membership; a UUID value
    means workspace-level. Role resolution (plan §6.6.1) prefers
    workspace-level over tenant-level.

    `user_id` is a provider_user_id (Clerk sub) per plan §2.6 — a string,
    not a FK to a users table.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    user_id: str
    workspace_id: str | None
    role: Role
    created_at: datetime
    updated_at: datetime

    @property
    def is_tenant_level(self) -> bool:
        return self.workspace_id is None

    @property
    def is_workspace_level(self) -> bool:
        return self.workspace_id is not None


# ---------------------------------------------------------------------------
# service_accounts / api_keys — reserved in this sprint, shapes stubbed
# ---------------------------------------------------------------------------


class ServiceAccountRecord(BaseModel):
    """Reserved for F-series service-account auth."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    workspace_id: str | None
    name: str
    public_id: str
    created_at: datetime


class ApiKeyRecord(BaseModel):
    """Reserved for F-series API key auth. Hash-only — no plaintext storage."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    workspace_id: str | None
    service_account_id: str | None
    key_hash: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None = None


# ---------------------------------------------------------------------------
# audit_events — persisted form of the Week 6a AuditEvent dataclass
# ---------------------------------------------------------------------------


class AuditEventRecord(BaseModel):
    """Durable audit event row.

    Mirrors src/audit/logger.py::AuditEvent but adds persistence-only
    fields (created_at must be set, actor_type is mandatory, etc.).
    The in-memory `audit_logger` singleton used in Week 6a tests does
    not go through this shape; only Turn 2's Postgres-backed audit
    writer does.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    action: str
    resource_type: str | None
    resource_id: str | None
    actor_id: str
    actor_type: Literal["human", "service_account", "system", "support"]
    actor_display: str | None
    workspace_id: str | None
    details: dict[str, Any] = Field(default_factory=dict)
    asset_versions: dict[str, Any] | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    correlation_id: str | None = None
    created_at: datetime
