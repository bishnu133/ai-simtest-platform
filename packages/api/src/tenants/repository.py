"""TenantRepository — tenant lookup by internal UUID or Clerk org ID.

Used by Turn 3 auth middleware to hydrate TenantContext from VerifiedClaims
and by bootstrap lifecycle (plan §6.4) to create tenant rows on first use.

Scope: tenant-scoped (tenant_id-only filter). No workspace dual-filter —
tenants ARE the top-level scope; there is nothing above to filter against.
Info-leak guard (§5.7 CrossWorkspaceForbidden) is not applicable here for
the same reason.

Protocol contract (Turn 2 plan §8 Step 2 recall):
  - get_by_id — middleware context hydration by internal UUID
  - get_by_clerk_org_id — provider→internal mapping for bootstrap
  - create — first-use tenant provisioning

Out-of-scope for Turn 2 (moved to Foundation Hardening #9):
  - list / update / delete / rename / plan change
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.api.errors import DuplicateTenant, TenantNotFound
from src.common.models import utcnow
from src.db.domain import TenantRecord
from src.db.mappers.tenant import tenant_to_domain
from src.db.models import Tenant as TenantORM
from src.db.session import raw_admin_session
from src.db.session_or_factory import use_session_or_admin

__all__ = [
    "TenantRepository",
    "InMemoryTenantRepository",
    "PostgresTenantRepository",
]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class TenantRepository(Protocol):
    """Public surface of the tenant repository.

    The optional ``session`` kwarg on every method (Turn 3 §5.0)
    lets callers (middleware/bootstrap) supply a pre-opened
    AsyncSession so multi-repo sequences run in one transaction.
    When omitted, implementations open their own session (Turn 2
    default). InMemory implementations accept and ignore the kwarg
    for API parity.
    """

    async def get_by_id(
        self,
        tenant_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> TenantRecord:
        """Lookup by internal UUID. Raises TenantNotFound if absent."""
        ...

    async def get_by_clerk_org_id(
        self,
        clerk_org_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> TenantRecord | None:
        """Lookup by provider org ID. Returns None if no tenant bound yet.

        Called by bootstrap on every login: None means first-use, create
        a new tenant; a record means existing tenant, proceed to
        membership resolution.
        """
        ...

    async def create(
        self,
        *,
        name: str,
        slug: str,
        clerk_org_id: str | None = None,
        plan_id: str = "free",
        settings: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> TenantRecord:
        """Insert a new tenant row.

        Raises DuplicateTenant (409) if `slug` or `clerk_org_id` collide.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory reference implementation
# ---------------------------------------------------------------------------


class InMemoryTenantRepository:
    """Dict-backed reference implementation for unit tests and local dev.

    Mirrors PostgresTenantRepository semantics for every method — same
    exceptions, same return shapes. Two tests parametrized across both
    implementations (see test_postgres_tenant_repository.py) verify the
    Protocol contract holds identically.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, TenantRecord] = {}
        self._by_slug: dict[str, str] = {}  # slug -> tenant_id
        self._by_clerk_org: dict[str, str] = {}  # clerk_org_id -> tenant_id

    async def get_by_id(
        self,
        tenant_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity with Postgres impl
    ) -> TenantRecord:
        record = self._by_id.get(tenant_id)
        if record is None:
            raise TenantNotFound(f"Tenant {tenant_id} not found")
        return record

    async def get_by_clerk_org_id(
        self,
        clerk_org_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002
    ) -> TenantRecord | None:
        tid = self._by_clerk_org.get(clerk_org_id)
        if tid is None:
            return None
        return self._by_id.get(tid)

    async def create(
        self,
        *,
        name: str,
        slug: str,
        clerk_org_id: str | None = None,
        plan_id: str = "free",
        settings: dict[str, Any] | None = None,
        session: AsyncSession | None = None,  # noqa: ARG002
    ) -> TenantRecord:
        if slug in self._by_slug:
            raise DuplicateTenant(
                f"Tenant with slug '{slug}' already exists",
                details={"slug": slug, "conflict": "slug"},
            )
        if clerk_org_id is not None and clerk_org_id in self._by_clerk_org:
            raise DuplicateTenant(
                f"Tenant with clerk_org_id '{clerk_org_id}' already exists",
                details={
                    "clerk_org_id": clerk_org_id,
                    "conflict": "clerk_org_id",
                },
            )
        now = utcnow()
        record = TenantRecord(
            id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            clerk_org_id=clerk_org_id,
            plan_id=plan_id,
            settings=dict(settings or {}),
            created_at=now,
            updated_at=now,
        )
        self._by_id[record.id] = record
        self._by_slug[slug] = record.id
        if clerk_org_id is not None:
            self._by_clerk_org[clerk_org_id] = record.id
        return record


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresTenantRepository:
    """SQLAlchemy 2.x async implementation against the Turn 1 schema.

    Uses `raw_admin_session()` rather than `tenant_scoped_session()`:
    tenants is the only non-RLS table in the schema (it's the join target
    for the RLS policies on the 11 other tables), and lookups happen
    BEFORE `TenantContext` exists — Turn 3 middleware calls
    `get_by_clerk_org_id` to discover the tenant_id in the first place.

    IntegrityError on `create` is caught and translated to
    DuplicateTenant; the error details distinguish slug vs. clerk_org_id
    conflicts for callers that need to present a targeted UI message.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """
        session_factory: optional override. If None, a fresh
        raw_admin_session() is opened per call. Passing a factory is
        useful for bootstrap paths that already hold a session.
        """
        self._session_factory = session_factory

    async def get_by_id(
        self,
        tenant_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> TenantRecord:
        async with use_session_or_admin(session) as (s, _owns):
            result = await s.execute(
                select(TenantORM).where(TenantORM.id == tenant_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise TenantNotFound(f"Tenant {tenant_id} not found")
            return tenant_to_domain(row)

    async def get_by_clerk_org_id(
        self,
        clerk_org_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> TenantRecord | None:
        async with use_session_or_admin(session) as (s, _owns):
            result = await s.execute(
                select(TenantORM).where(TenantORM.clerk_org_id == clerk_org_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return tenant_to_domain(row)

    async def create(
        self,
        *,
        name: str,
        slug: str,
        clerk_org_id: str | None = None,
        plan_id: str = "free",
        settings: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> TenantRecord:
        async with use_session_or_admin(session) as (s, owns):
            orm = TenantORM(
                name=name,
                slug=slug,
                clerk_org_id=clerk_org_id,
                plan_id=plan_id,
                settings=dict(settings or {}),
            )
            s.add(orm)
            try:
                await s.flush()
            except IntegrityError as exc:
                if owns:
                    await s.rollback()
                # Distinguish which unique constraint fired for better UX
                err_text = str(exc.orig).lower() if exc.orig else str(exc).lower()
                if "clerk_org_id" in err_text:
                    raise DuplicateTenant(
                        f"Tenant with clerk_org_id '{clerk_org_id}' already exists",
                        details={
                            "clerk_org_id": clerk_org_id,
                            "conflict": "clerk_org_id",
                        },
                    ) from exc
                if "slug" in err_text:
                    raise DuplicateTenant(
                        f"Tenant with slug '{slug}' already exists",
                        details={"slug": slug, "conflict": "slug"},
                    ) from exc
                # Unknown unique-constraint violation — surface as generic
                raise DuplicateTenant(
                    "Tenant unique constraint violated",
                    details={"slug": slug, "clerk_org_id": clerk_org_id},
                ) from exc
            if owns:
                await s.commit()
                await s.refresh(orm)
            return tenant_to_domain(orm)
