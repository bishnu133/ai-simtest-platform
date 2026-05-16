"""SQLAlchemy ORM models for the Postgres + Auth Wiring Sprint.

Table inventory per plan §7.0 — exactly 12 tables:

  1.  tenants                — root, no RLS
  2.  workspaces              — RLS
  3.  memberships             — RLS, PARTIAL UNIQUE INDEXES (§7.1)
  4.  service_accounts        — RLS, reserved (no code in Turn 1)
  5.  api_keys                — RLS, reserved (no code in Turn 1)
  6.  assets                  — RLS
  7.  runs                    — RLS, 11-state status enum persisted (§2.7)
  8.  conversation_summaries  — RLS
  9.  dashboard_artifacts     — RLS
  10. comparisons             — RLS
  11. idempotency_keys        — RLS
  12. audit_events            — RLS, PARTITIONED + DEFAULT partition (§7.2)

The partial unique indexes on `memberships` are the schema-level guarantee
from v0.5.1 MF-1: Postgres treats NULL as distinct in full-tuple unique
constraints, so a single `UNIQUE(tenant_id, user_id, workspace_id)` would
permit duplicate tenant-level rows. Two partial indexes fix this.

Note on `audit_events`: ORM declares the parent partitioned table only;
monthly partitions + the DEFAULT partition are created in raw SQL inside
the Alembic migration, because SQLAlchemy's declarative layer doesn't
model PARTITION BY natively.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float as sa_Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


# ---------------------------------------------------------------------------
# Constants reused across models
# ---------------------------------------------------------------------------

# Role ladder (plan §6.6.1). Mirrors src/api/deps.py::_ROLE_ORDER with the
# addition of `owner` at the top per plan §6.5 bootstrap semantics.
_ROLE_CHECK = "role IN ('owner','admin','member','viewer','service_account')"

# Run status — plan §2.7. In-code RunStatus has 5 states; persisted column
# carries the 11-state enum from v2 §6.1 so forward expansion doesn't require
# a migration.
_RUN_STATUS_CHECK = (
    "status IN ("
    "'queued','provisioning','running','finalizing',"
    "'completed','failed','cancelled',"
    "'waiting_input','paused','expired','unknown'"
    ")"
)

_ACTOR_TYPE_CHECK = "actor_type IN ('human','service_account','system','support')"

_ASSET_STATUS_CHECK = "status IN ('draft','approved','deprecated')"

_ASSET_TYPE_CHECK = (
    "asset_type IN ("
    "'judge_pack','policy_pack','scenario_pack','workflow_pack',"
    "'dataset','bot_profile','scorecard'"
    ")"
)


# ---------------------------------------------------------------------------
# 1. tenants — root table, no RLS
# ---------------------------------------------------------------------------


class Tenant(Base):
    """Root tenant row. Not RLS-protected because it is the join target
    from memberships; access control happens through memberships, not RLS.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # NB Drift 1.5 (Turn 2.7): index/uniqueness for clerk_org_id is declared
    # in __table_args__ below as the partial unique index
    # `uq_tenants_clerk_org_id` so the model matches migration 0001 exactly.
    # Previously this column had `unique=True, index=True` which produced an
    # auto-named non-partial index `ix_tenants_clerk_org_id` — that was a
    # silent drift from the migration's partial form
    # (postgresql_where = "clerk_org_id IS NOT NULL") and was caught by
    # `alembic check` in Drift 1.
    clerk_org_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    plan_id: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'free'")
    )
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Partial unique index — matches migration 0001's
        # `uq_tenants_clerk_org_id`. Postgres treats NULL as distinct in
        # unique constraints, so a non-partial unique on clerk_org_id
        # would forbid more than one tenant with NULL clerk_org_id.
        # The partial WHERE clause restricts uniqueness to non-NULL values.
        Index(
            "uq_tenants_clerk_org_id",
            "clerk_org_id",
            unique=True,
            postgresql_where=text("clerk_org_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# 2. workspaces — RLS
# ---------------------------------------------------------------------------


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_workspaces_tenant_id", "tenant_id"),
        # At most one default workspace per tenant. Race-safe enforcement
        # via partial unique index — Alembic revision 0002. Mirrored here
        # in __table_args__ so SQLAlchemy metadata stays in sync (prevents
        # autogenerate from re-emitting the index as a "drop"). See
        # Turn 2.5 plan v0.2.1 §3.1 D-Mig + MF-5.
        Index(
            "uq_workspaces_one_default_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )


# ---------------------------------------------------------------------------
# 3. memberships — RLS + PARTIAL UNIQUE INDEXES (§7.1 — the MF-1 fix)
# ---------------------------------------------------------------------------
#
# memberships authorization model:
#
#   workspace_id IS NULL     → tenant-level membership (authorizes org-wide actions).
#   workspace_id IS NOT NULL → workspace-level membership.
#
# A user may have:
#   - exactly ZERO or ONE tenant-level row per (tenant_id, user_id),
#     enforced by uq_memberships_tenant_level (partial unique, WHERE workspace_id IS NULL)
#   - exactly ZERO or ONE workspace-level row per (tenant_id, user_id, workspace_id),
#     enforced by uq_memberships_workspace_level (partial unique, WHERE workspace_id IS NOT NULL)
#
# Why partial indexes, not a single UNIQUE(tenant_id, user_id, workspace_id):
#   Postgres treats NULL as distinct in unique constraints, which means a single
#   full-tuple unique constraint would permit DUPLICATE tenant-level rows for the
#   same (tenant_id, user_id) pair. That would break authz. Partial indexes force
#   tenant-level rows to be unique on (tenant_id, user_id) alone.
# ---------------------------------------------------------------------------


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # user_id is a provider_user_id (Clerk sub) per plan §2.6 identity contract.
    # VARCHAR(255), NOT a FK to a users table (no users table in S1/S2).
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(_ROLE_CHECK, name="role"),
        # §7.1 partial unique indexes — tenant-level rows (workspace_id IS NULL)
        Index(
            "uq_memberships_tenant_level",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
        ),
        # §7.1 partial unique indexes — workspace-level rows (workspace_id IS NOT NULL)
        Index(
            "uq_memberships_workspace_level",
            "tenant_id",
            "user_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        # Lookup indexes for authz queries (plan §6.6.1 get_actor_role)
        Index(
            "ix_memberships_lookup_workspace",
            "tenant_id",
            "user_id",
            "workspace_id",
        ),
        Index(
            "ix_memberships_lookup_tenant",
            "tenant_id",
            "user_id",
            postgresql_where=text("workspace_id IS NULL"),
        ),
        # Drift 1.5 (Turn 2.7): table comment matches migration 0001's
        # `COMMENT ON TABLE memberships IS '...'` block (line 236-249).
        # Without this dict in __table_args__, `alembic check` reports a
        # `remove_table_comment` operation because the live DB has the
        # comment but the model didn't declare it.
        # The string MUST match migration 0001's COMMENT body byte-for-byte
        # (modulo whitespace handling) or autogen will flag a comment diff.
        {
            "comment": (
                "Memberships authorization model. "
                "workspace_id IS NULL means tenant-level membership (org-wide actions). "
                "workspace_id IS NOT NULL means workspace-level membership. "
                "A user may have one tenant-level row AND N workspace-level rows. "
                "Enforced by two partial unique indexes (uq_memberships_tenant_level, "
                "uq_memberships_workspace_level) rather than a single full-tuple unique "
                "constraint because Postgres treats NULL as distinct in unique constraints, "
                "which would permit duplicate tenant-level rows and break authz. "
                "See plan v0.5.1 §7.1."
            ),
        },
    )


# ---------------------------------------------------------------------------
# 4. service_accounts — RLS, reserved
# ---------------------------------------------------------------------------


class ServiceAccount(Base):
    """Reserved for F-series service-account auth. No code in Turn 1."""

    __tablename__ = "service_accounts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    public_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_service_accounts_tenant_id", "tenant_id"),
    )


# ---------------------------------------------------------------------------
# 5. api_keys — RLS, reserved
# ---------------------------------------------------------------------------


class ApiKey(Base):
    """Reserved for F-series API key auth. No code in Turn 1."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    service_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Store only a hash; plaintext never hits the DB.
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_api_keys_tenant_id", "tenant_id"),
    )


# ---------------------------------------------------------------------------
# 6. assets — RLS (schema stub; service layer already exists in-memory)
# ---------------------------------------------------------------------------


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Type, naming, description
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    # Versioning + lifecycle
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'draft'")
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # Storage tier + content — one of inline_content or payload_* per storage_tier
    storage_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'inline'")
    )
    inline_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payload_backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Governance actors — plan §2.6 identity contract: VARCHAR(255) for
    # provider_user_id / reserved system actors, never a FK to a users table.
    created_by_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_display: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by_actor_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    approved_by_actor_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    approved_by_display: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    changelog: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    # Lineage (for clones / new versions)
    cloned_from: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Metadata
    tags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(_ASSET_TYPE_CHECK, name="asset_type"),
        CheckConstraint(_ASSET_STATUS_CHECK, name="status"),
        CheckConstraint(
            "storage_tier IN ('inline','object')", name="storage_tier"
        ),
        CheckConstraint(
            "created_by_actor_type IN ('human','service_account','system','support')",
            name="created_by_actor_type",
        ),
        # Tenant-scoped uniqueness on (slug, asset_type, version) — distinct
        # version ladders per asset type, so judge_pack:greeting@v1 and
        # policy_pack:greeting@v1 can coexist.
        Index(
            "uq_assets_tenant_slug_type_version",
            "tenant_id",
            "workspace_id",
            "asset_type",
            "slug",
            "version",
            unique=True,
        ),
        Index("ix_assets_tenant_workspace", "tenant_id", "workspace_id"),
        Index("ix_assets_tenant_type", "tenant_id", "asset_type"),
    )


# ---------------------------------------------------------------------------
# 7. runs — RLS, persisted status uses the 11-state enum (§2.7)
# ---------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    initiated_by_actor_id: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Service-generated JSONB — NOT user-writable, so NOT SafeJSONB (plan §2.8).
    asset_versions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    judge_scores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    run_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(_RUN_STATUS_CHECK, name="status"),
        Index("ix_runs_tenant_workspace", "tenant_id", "workspace_id"),
        Index("ix_runs_tenant_status", "tenant_id", "status"),
        Index("ix_runs_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# 8. conversation_summaries — RLS
# ---------------------------------------------------------------------------


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Persona / bot context — mirrors ConversationSummary domain model.
    persona_id: Mapped[str] = mapped_column(String(255), nullable=False)
    persona_name: Mapped[str] = mapped_column(String(255), nullable=False)
    persona_type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'standard'")
    )
    # Verdict + scores
    verdict: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    pass_rate: Mapped[float] = mapped_column(
        sa_Float(), nullable=False, server_default=text("0.0")
    )
    turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Aggregated judge scores — service-computed, not user-writable (§2.8).
    judge_scores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Failure metadata (if any)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Reference to full transcript in object storage (R2 or local)
    transcript_backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    transcript_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transcript_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # User-writable metadata — Turn 2 wraps in SafeJSONB at API boundary.
    tags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('pass','fail','error','pending')",
            name="verdict",
        ),
        CheckConstraint(
            "pass_rate >= 0.0 AND pass_rate <= 1.0",
            name="pass_rate",
        ),
        Index("ix_conv_sum_tenant_workspace", "tenant_id", "workspace_id"),
        Index("ix_conv_sum_run_id", "run_id"),
    )


# ---------------------------------------------------------------------------
# 9. dashboard_artifacts — RLS
# ---------------------------------------------------------------------------


class DashboardArtifact(Base):
    __tablename__ = "dashboard_artifacts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # All fields are service-computed projections (plan §2.8) — NOT SafeJSONB.
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_dashboard_artifacts_run_id", "run_id"),
        Index(
            "ix_dashboard_artifacts_tenant_workspace",
            "tenant_id",
            "workspace_id",
        ),
    )


# ---------------------------------------------------------------------------
# 10. comparisons — RLS
# ---------------------------------------------------------------------------


class Comparison(Base):
    __tablename__ = "comparisons"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    left_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    right_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    initiated_by_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    engine_version: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'engine_v1'")
    )
    # User-writable config — Turn 2 wraps in SafeJSONB at the API boundary.
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Service-generated results — NOT user-writable per §2.8.
    # `regression_signals` is a JSONB list of typed RegressionSignal objects
    # serialized via the comparison mapper (see src/db/mappers/comparison.py).
    regression_signals: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    left_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    right_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metric_deltas: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Reserved for heavy artifacts (plan §M2 — always None in 6a)
    result_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Verdict + comparison profile
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comparison_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="status",
        ),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('regression','improvement','neutral','inconclusive')",
            name="verdict",
        ),
        Index("ix_comparisons_tenant_workspace", "tenant_id", "workspace_id"),
        Index("ix_comparisons_left_run_id", "left_run_id"),
        Index("ix_comparisons_right_run_id", "right_run_id"),
    )


# ---------------------------------------------------------------------------
# 11. idempotency_keys — RLS
# ---------------------------------------------------------------------------


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_idempotency_keys_workspace_id_workspaces",
        ),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_idempotency_keys_tenant_workspace_key",
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_idempotency_keys_expires_at",
            "expires_at",
        ),
    )


# ---------------------------------------------------------------------------
# 12. audit_events — RLS, PARTITIONED (monthly + DEFAULT) — §7.2
# ---------------------------------------------------------------------------
#
# The ORM declares only the column shape. Partitioning, monthly partitions,
# and the DEFAULT partition are created in raw SQL inside the Alembic
# migration because SQLAlchemy's declarative layer doesn't model PARTITION BY.
#
# Note: composite PK (id, created_at) is required because Postgres demands
# the partition key be part of the primary key.
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )
    tenant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_display: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id"),
        nullable=True,
    )
    # Service-generated JSONB (plan §2.8) — NOT SafeJSONB.
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    asset_versions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        primary_key=True,  # composite PK for partition key
    )

    __table_args__ = (
        CheckConstraint(_ACTOR_TYPE_CHECK, name="actor_type"),
        # Lookup indexes — applied to every partition automatically.
        Index(
            "ix_audit_events_tenant_time",
            "tenant_id",
            "created_at",
        ),
        Index("ix_audit_events_action", "action", "created_at"),
        Index(
            "ix_audit_events_correlation",
            "correlation_id",
            postgresql_where=text("correlation_id IS NOT NULL"),
        ),
        # The parent table itself is declared partitioned. SQLAlchemy 2.0 lets
        # us pass this as a dialect option on the table.
        {"postgresql_partition_by": "RANGE (created_at)"},
    )


# ---------------------------------------------------------------------------
# Public model registry — ordered for deterministic creation / introspection
# ---------------------------------------------------------------------------

ALL_MODELS = (
    Tenant,
    Workspace,
    Membership,
    ServiceAccount,
    ApiKey,
    Asset,
    Run,
    ConversationSummary,
    DashboardArtifact,
    Comparison,
    IdempotencyKey,
    AuditEvent,
)

__all__ = [
    "Base",
    "Tenant",
    "Workspace",
    "Membership",
    "ServiceAccount",
    "ApiKey",
    "Asset",
    "Run",
    "ConversationSummary",
    "DashboardArtifact",
    "Comparison",
    "IdempotencyKey",
    "AuditEvent",
    "ALL_MODELS",
]
