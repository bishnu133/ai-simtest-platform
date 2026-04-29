"""MembershipRepository — plan §6.5 dual-membership shape.

`workspace_id IS NULL`     → tenant-level membership (authorizes org-wide actions)
`workspace_id IS NOT NULL` → workspace-level membership

A user may have:
  * exactly ZERO or ONE tenant-level row per (tenant_id, user_id),
    enforced by uq_memberships_tenant_level (partial unique index)
  * exactly ZERO or ONE workspace-level row per
    (tenant_id, user_id, workspace_id), enforced by
    uq_memberships_workspace_level (partial unique index)

Both indexes landed in Turn 1a per plan v0.5.1 §7.1 (the MF-1 fix). The
schema comment in `src/db/models.py::Membership` explains why partial
indexes are necessary rather than a single full-tuple unique constraint.

Protocol contract (Turn 2 plan §8 Step 2 recall):
  - get_role — primary lookup, used by plan §6.6.1 get_actor_role()
  - create — bootstrap + membership admission in Turn 3
  - list_for_user_in_tenant — reads both tenant-level and workspace-level
    rows for a user; used by the Turn 3 authz precedence resolver when
    it needs to see the full picture

Out-of-scope for Turn 2 (Foundation Hardening #9):
  - delete / role-change (tenant-level admin endpoints)
"""
from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.api.errors import DuplicateMembership
from src.common.models import utcnow
from src.db.domain import MembershipRecord, Role
from src.db.mappers.membership import membership_to_domain
from src.db.models import Membership as MembershipORM
from src.db.session import raw_admin_session
from src.db.session_or_factory import use_session_or_admin

__all__ = [
    "MembershipRepository",
    "InMemoryMembershipRepository",
    "PostgresMembershipRepository",
]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MembershipRepository(Protocol):
    """Public surface of the membership repository.

    The optional ``session`` kwarg on every method (Turn 3 §5.0) lets
    callers (middleware/bootstrap) supply a pre-opened AsyncSession so
    multi-repo sequences run in one transaction.
    """

    async def get_role(
        self,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        session: AsyncSession | None = None,
    ) -> str | None:
        """Return the role string for a specific membership row, or None.

        `workspace_id=None` queries the tenant-level row.
        `workspace_id=<uuid>` queries the workspace-level row.

        Called by plan §6.6.1 `get_actor_role()` in Turn 3:
          1. Try workspace-level first (ctx.workspace_id)
          2. Fall back to tenant-level (workspace_id=None)
          3. If both return None, the user has no membership → 403
        """
        ...

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        role: Role,
        session: AsyncSession | None = None,
    ) -> MembershipRecord:
        """Insert a membership row.

        Raises DuplicateMembership (409) when the partial unique
        indexes fire:
          * Inserting a second tenant-level row for the same
            (tenant_id, user_id) → uq_memberships_tenant_level violated
          * Inserting a second workspace-level row for the same
            (tenant_id, user_id, workspace_id) → uq_memberships_workspace_level
        """
        ...

    async def list_for_user_in_tenant(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> list[MembershipRecord]:
        """Return all rows for (tenant_id, user_id).

        Both tenant-level (workspace_id=None) and workspace-level rows
        are returned. Ordering: tenant-level rows first (if any), then
        workspace-level rows ordered by workspace_id for stability.

        Used by the Turn 3 authz precedence resolver when it needs the
        full set of memberships rather than a specific-scope role.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory reference implementation
# ---------------------------------------------------------------------------


class InMemoryMembershipRepository:
    """Dict-backed reference implementation for unit tests and local dev."""

    def __init__(self) -> None:
        # membership_id -> MembershipRecord
        self._by_id: dict[str, MembershipRecord] = {}

    async def get_role(
        self,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> str | None:
        for record in self._by_id.values():
            if (
                record.tenant_id == tenant_id
                and record.user_id == user_id
                and record.workspace_id == workspace_id
            ):
                return record.role
        return None

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        role: Role,
        session: AsyncSession | None = None,  # noqa: ARG002
    ) -> MembershipRecord:
        # Enforce the partial unique index semantics in memory the same
        # way Postgres enforces them at the DB layer.
        for existing in self._by_id.values():
            if (
                existing.tenant_id == tenant_id
                and existing.user_id == user_id
                and existing.workspace_id == workspace_id
            ):
                # Matching shape already exists — same-scope duplicate.
                if workspace_id is None:
                    raise DuplicateMembership(
                        f"Tenant-level membership already exists for user "
                        f"{user_id} in tenant {tenant_id}",
                        details={
                            "tenant_id": tenant_id,
                            "user_id": user_id,
                            "scope": "tenant_level",
                        },
                    )
                raise DuplicateMembership(
                    f"Workspace-level membership already exists for user "
                    f"{user_id} in workspace {workspace_id}",
                    details={
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "workspace_id": workspace_id,
                        "scope": "workspace_level",
                    },
                )
        now = utcnow()
        record = MembershipRecord(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        self._by_id[record.id] = record
        return record

    async def list_for_user_in_tenant(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session: AsyncSession | None = None,  # noqa: ARG002
    ) -> list[MembershipRecord]:
        rows = [
            r
            for r in self._by_id.values()
            if r.tenant_id == tenant_id and r.user_id == user_id
        ]
        # Deterministic order: tenant-level rows first, then workspace-level
        # rows ordered by workspace_id for stability.
        return sorted(
            rows,
            key=lambda r: (
                r.workspace_id is not None,  # False (tenant-level) sorts first
                r.workspace_id or "",
            ),
        )


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresMembershipRepository:
    """SQLAlchemy 2.x async implementation.

    Uses `raw_admin_session()` because Turn 3 `get_actor_role()` is
    called during middleware BEFORE the tenant-scoped session is
    established for the request body. Turn 3 will wrap the whole middleware
    chain in its own session if performance needs it; Turn 2 uses the
    simplest-working approach.

    Duplicate detection: IntegrityError is caught and translated into
    `DuplicateMembership` with details that distinguish tenant-level
    vs. workspace-level scope (useful for the 409 response body).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    async def get_role(
        self,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        session: AsyncSession | None = None,
    ) -> str | None:
        async with use_session_or_admin(session) as (s, _owns):
            # Build query with explicit IS NULL semantics for tenant-level
            # rows (SQL `= NULL` never matches).
            stmt = select(MembershipORM.role).where(
                MembershipORM.tenant_id == tenant_id,
                MembershipORM.user_id == user_id,
            )
            if workspace_id is None:
                stmt = stmt.where(MembershipORM.workspace_id.is_(None))
            else:
                stmt = stmt.where(MembershipORM.workspace_id == workspace_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str | None,
        role: Role,
        session: AsyncSession | None = None,
    ) -> MembershipRecord:
        async with use_session_or_admin(session) as (s, owns):
            orm = MembershipORM(
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=workspace_id,
                role=role,
            )
            s.add(orm)
            try:
                await s.flush()
            except IntegrityError as exc:
                if owns:
                    await s.rollback()
                # Distinguish which partial unique index fired for better UX
                err_text = str(exc.orig).lower() if exc.orig else str(exc).lower()
                if "uq_memberships_tenant_level" in err_text:
                    raise DuplicateMembership(
                        f"Tenant-level membership already exists for user "
                        f"{user_id} in tenant {tenant_id}",
                        details={
                            "tenant_id": tenant_id,
                            "user_id": user_id,
                            "scope": "tenant_level",
                        },
                    ) from exc
                if "uq_memberships_workspace_level" in err_text:
                    raise DuplicateMembership(
                        f"Workspace-level membership already exists for user "
                        f"{user_id} in workspace {workspace_id}",
                        details={
                            "tenant_id": tenant_id,
                            "user_id": user_id,
                            "workspace_id": workspace_id,
                            "scope": "workspace_level",
                        },
                    ) from exc
                # Unknown unique violation — surface generically
                raise DuplicateMembership(
                    "Membership unique constraint violated",
                    details={
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "workspace_id": workspace_id,
                    },
                ) from exc
            if owns:
                await s.commit()
                await s.refresh(orm)
            return membership_to_domain(orm)

    async def list_for_user_in_tenant(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> list[MembershipRecord]:
        async with use_session_or_admin(session) as (s, _owns):
            # ORDER BY: tenant-level (workspace_id IS NULL) first, then
            # workspace-level rows ordered by workspace_id. Using NULLS
            # FIRST is Postgres-explicit and stable regardless of default
            # null ordering.
            result = await s.execute(
                select(MembershipORM)
                .where(
                    MembershipORM.tenant_id == tenant_id,
                    MembershipORM.user_id == user_id,
                )
                .order_by(
                    MembershipORM.workspace_id.asc().nulls_first(),
                )
            )
            return [membership_to_domain(row) for row in result.scalars()]
