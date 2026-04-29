"""WorkspaceRepository — workspace lookup and creation within a tenant.

Used by Turn 3 auth middleware and bootstrap lifecycle (plan §6.4) to
discover or create the default workspace for a tenant.

Scope: tenant-scoped (tenant_id-only filter). Even though workspaces are
the thing that `ctx.workspace_id` names, a `WorkspaceRepository.get_by_id`
is scoped by `ctx.tenant_id` ONLY — using `ctx.workspace_id` as a filter
would be self-referential (the workspace_id being looked up IS the
workspace). Cross-tenant lookups raise `CrossTenantForbidden` per §5.7
info-leak guard.

Protocol contract:
  - get_by_id — lookup within tenant; cross-tenant probe raises 403
  - get_default_for_tenant — bootstrap first-use path
  - create — first-use workspace provisioning

The schema enforces "at most one default workspace per tenant" with a
partial unique index (Turn 1a migration, plan §7.2). A second insert
with is_default=True for the same tenant raises `DuplicateWorkspace`.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.api.errors import (
    CrossTenantForbidden,
    DuplicateWorkspace,
    WorkspaceNotFound,
)
from src.common.models import TenantContext, utcnow
from src.db.domain import WorkspaceRecord
from src.db.mappers.workspace import workspace_to_domain
from src.db.models import Workspace as WorkspaceORM
from src.db.session import raw_admin_session
from src.db.session_or_factory import use_session_or_admin

__all__ = [
    "WorkspaceRepository",
    "InMemoryWorkspaceRepository",
    "PostgresWorkspaceRepository",
]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class WorkspaceRepository(Protocol):
    """Public surface of the workspace repository.

    The optional ``session`` kwarg on every method (Turn 3 §5.0) lets
    callers (middleware/bootstrap) supply a pre-opened AsyncSession so
    multi-repo sequences run in one transaction.
    """

    async def get_by_id(
        self,
        ctx: TenantContext,
        workspace_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> WorkspaceRecord:
        """Lookup a workspace by id within the caller's tenant.

        Raises:
          * CrossTenantForbidden — workspace exists but in a different tenant
            (info-leak guard: do not distinguish this from NotFound to
            tenants that don't own the row)
          * WorkspaceNotFound — no workspace with this id in any tenant
        """
        ...

    async def get_default_for_tenant(
        self,
        tenant_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> WorkspaceRecord | None:
        """Return the is_default=true workspace for a tenant, or None.

        Used by bootstrap first-user flow and by Turn 3 middleware when
        a token claim doesn't carry an explicit workspace_id.
        """
        ...

    async def create(
        self,
        *,
        tenant_id: str,
        name: str,
        is_default: bool = False,
        session: AsyncSession | None = None,
    ) -> WorkspaceRecord:
        """Insert a new workspace row.

        Raises DuplicateWorkspace (409) if the `at most one default per
        tenant` partial unique index fires — i.e., trying to create a
        second is_default=True workspace for the same tenant.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory reference implementation
# ---------------------------------------------------------------------------


class InMemoryWorkspaceRepository:
    """Dict-backed reference implementation for unit tests and local dev."""

    def __init__(self) -> None:
        # workspace_id -> WorkspaceRecord
        self._by_id: dict[str, WorkspaceRecord] = {}

    async def get_by_id(
        self,
        ctx: TenantContext,
        workspace_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> WorkspaceRecord:
        record = self._by_id.get(workspace_id)
        if record is None:
            raise WorkspaceNotFound(f"Workspace {workspace_id} not found")
        if record.tenant_id != ctx.tenant_id:
            # §5.7 info-leak guard: cross-tenant probe → 403, not 404
            raise CrossTenantForbidden(
                f"Workspace {workspace_id} belongs to a different tenant"
            )
        return record

    async def get_default_for_tenant(
        self,
        tenant_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002
    ) -> WorkspaceRecord | None:
        for record in self._by_id.values():
            if record.tenant_id == tenant_id and record.is_default:
                return record
        return None

    async def create(
        self,
        *,
        tenant_id: str,
        name: str,
        is_default: bool = False,
        session: AsyncSession | None = None,  # noqa: ARG002
    ) -> WorkspaceRecord:
        if is_default:
            # Enforce the `at most one default per tenant` invariant in
            # memory the same way the partial unique index does in Postgres.
            for existing in self._by_id.values():
                if existing.tenant_id == tenant_id and existing.is_default:
                    raise DuplicateWorkspace(
                        f"Tenant {tenant_id} already has a default workspace",
                        details={
                            "tenant_id": tenant_id,
                            "conflict": "default_workspace",
                        },
                    )
        now = utcnow()
        record = WorkspaceRecord(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            is_default=is_default,
            created_at=now,
            updated_at=now,
        )
        self._by_id[record.id] = record
        return record


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresWorkspaceRepository:
    """SQLAlchemy 2.x async implementation.

    Uses `raw_admin_session()` — workspace lookups happen during Turn 3
    bootstrap BEFORE TenantContext is populated (bootstrap is creating
    the context from scratch). This matches the PostgresTenantRepository
    rationale.

    The `get_by_id` cross-tenant info-leak guard is implemented with a
    second query to distinguish `exists in another tenant` from
    `does not exist anywhere`. The two queries run inside a single
    session so the cost is two round trips on the probe path (rare) and
    one on the happy path.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        ctx: TenantContext,
        workspace_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> WorkspaceRecord:
        async with use_session_or_admin(session) as (s, _owns):
            # Primary: scoped to caller's tenant
            result = await s.execute(
                select(WorkspaceORM).where(
                    WorkspaceORM.id == workspace_id,
                    WorkspaceORM.tenant_id == ctx.tenant_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return workspace_to_domain(row)

            # Secondary: does it exist under a different tenant?
            # §5.7 info-leak guard — raise CrossTenantForbidden rather
            # than NotFound so existence probing across tenants is blocked.
            probe = await s.execute(
                select(WorkspaceORM.id).where(WorkspaceORM.id == workspace_id)
            )
            if probe.scalar_one_or_none() is not None:
                raise CrossTenantForbidden(
                    f"Workspace {workspace_id} belongs to a different tenant"
                )
            raise WorkspaceNotFound(f"Workspace {workspace_id} not found")

    async def get_default_for_tenant(
        self,
        tenant_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> WorkspaceRecord | None:
        async with use_session_or_admin(session) as (s, _owns):
            result = await s.execute(
                select(WorkspaceORM).where(
                    WorkspaceORM.tenant_id == tenant_id,
                    WorkspaceORM.is_default.is_(True),
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return workspace_to_domain(row)

    async def create(
        self,
        *,
        tenant_id: str,
        name: str,
        is_default: bool = False,
        session: AsyncSession | None = None,
    ) -> WorkspaceRecord:
        """Insert a new workspace row.

        "At most one default workspace per tenant" is enforced by the
        ``uq_workspaces_one_default_per_tenant`` partial unique index
        (Alembic revision 0002). A second insert with is_default=True for
        the same tenant raises IntegrityError, which this method maps to
        ``DuplicateWorkspace`` for the public API surface.

        See Turn 2.5 plan v0.2.1 §3.1 D-Mig + MF-5 for the rationale: the
        previous app-level check-then-insert raced under concurrent
        bootstrap. The DB partial unique index is now the source of truth.
        """
        async with use_session_or_admin(session) as (s, owns):
            orm = WorkspaceORM(
                tenant_id=tenant_id,
                name=name,
                is_default=is_default,
            )
            s.add(orm)
            try:
                await s.flush()
            except IntegrityError as exc:
                if owns:
                    await s.rollback()
                # The partial unique index fires when is_default=True
                # collides; report the conflict shape that triggered it.
                raise DuplicateWorkspace(
                    f"Tenant {tenant_id} already has a default workspace"
                    if is_default
                    else "Workspace unique constraint violated",
                    details={
                        "tenant_id": tenant_id,
                        "name": name,
                        "conflict": "default_workspace" if is_default else "name",
                    },
                ) from exc
            if owns:
                await s.commit()
                await s.refresh(orm)
            return workspace_to_domain(orm)

