"""Audit event repository — Protocol + InMemory + Postgres.

Slice 4 of FH-Tier-1 (signed off May 17, 2026).

Persistence layer for audit events. Two paths:

* **Pretenant path:** Events without tenant context (e.g., failed
  authentication before tenant resolution). Persisted via the
  ``audit_pretenant_insert(...)`` SECURITY DEFINER function (migration
  0008) which bypasses RLS via its owner's BYPASSRLS privilege. This
  is the ONLY supported persistence path for pretenant events — direct
  ORM INSERT under the ``app_user`` role is rejected by the
  ``ck_audit_tenant_required_or_pretenant`` CHECK constraint combined
  with the ``audit_events_insert`` RLS policy.

* **Tenant path:** Events with tenant context. Persisted via standard
  ORM INSERT under a ``tenant_scoped_session``; the RLS policy
  ``audit_events_insert`` evaluates
  ``tenant_id = current_setting('app.current_tenant_id')::uuid`` and
  rejects rows that don't match the session's tenant.

Session-kwarg contract (AM-3 + AM-4, post-AM-4 modern convention):

* ``session=None``  → repository opens its own session
  (``raw_admin_session`` for pretenant, ``tenant_scoped_session`` for
  tenant) and the context manager owns the transaction lifecycle.
* ``session=<provided>`` → caller has scoped the session appropriately;
  repository executes/flushes only, caller owns commit/rollback.

Protocol includes ``session`` in the signature (Slice 4 Phase B Q1,
locked May 17, 2026). This is the post-AM-4 default for new repositories.

Defense-in-depth layers for pretenant action allowlist:

  1. ``PretenantAction = Literal["auth.rejected"]`` (type system)
  2. ``PretenantAuditEvent.__post_init__`` (domain runtime guard)
  3. ``audit_pretenant_insert`` function IF-block (DB-level guard)
  4. ``ck_audit_tenant_required_or_pretenant`` CHECK constraint (DB)

If the function rejects the call (layer 3), this module surfaces the
DBAPIError as :class:`AuditEventInsertRejected` with HTTP 500 — reaching
the DB-level guard means the domain layer was bypassed, which is a
server-side bug, not normal user input.
"""
from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import APIError
from src.audit.context import (
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.db.session import raw_admin_session, tenant_scoped_session


__all__ = [
    "AuditEventInsertRejected",
    "AuditEventRepository",
    "InMemoryAuditEventRepository",
    "PostgresAuditEventRepository",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuditEventInsertRejected(APIError):
    """The ``audit_pretenant_insert`` function rejected the call.

    Raised when asyncpg surfaces a ``check_violation`` (SQLSTATE 23514)
    from the SECURITY DEFINER function — e.g., disallowed action,
    invalid actor_type.

    Note: this exception is defense-in-depth. The domain layer
    (:class:`PretenantAuditEvent.__post_init__`) is the authoritative
    validator; reaching the DB-level allowlist means the domain check
    was bypassed (test bypass, direct SQL, etc.), which is a server-side
    bug. ``http_status = 500`` reflects this — it is NOT a user input
    error to be returned as 400.
    """

    code = "audit_event_insert_rejected"
    http_status = 500


# ---------------------------------------------------------------------------
# Internal SQL
# ---------------------------------------------------------------------------


# Pretenant insert path: SECURITY DEFINER function (migration 0008).
# 9 parameters, all keyword-bound from PretenantAuditEvent.to_function_args().
#
# CAST(:x AS jsonb) and CAST(:x AS inet) are used instead of the shorthand
# ``::jsonb`` / ``::inet`` to avoid asyncpg parameter-binding ambiguity
# (``::`` can be misinterpreted by the bind-parser).
_PRETENANT_INSERT_SQL = sa.text("""
    SELECT audit_pretenant_insert(
        :action,
        :actor_id,
        :actor_type,
        :resource_type,
        :resource_id,
        :correlation_id,
        CAST(:details AS jsonb),
        CAST(:ip_address AS inet),
        :user_agent
    ) AS new_id
""")


def _is_check_violation(exc: DBAPIError) -> bool:
    """Return True if the underlying DB error is a check_violation.

    ``audit_pretenant_insert`` RAISEs with ``ERRCODE = 'check_violation'``
    for disallowed action / actor_type. asyncpg surfaces this as a
    DBAPIError where ``exc.orig.sqlstate == '23514'``.

    We check multiple attribute names (sqlstate, pgcode) and both the
    wrapped DBAPIError and the underlying ``orig`` because different
    driver wrapping layers expose SQLSTATE through different attributes.
    This is the preferred detection path; string matching is fallback.
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        if sqlstate == "23514":
            return True
    # SQLAlchemy may also expose the code on the wrapper
    code = getattr(exc, "code", None)
    if code == "23514":
        return True
    return False


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AuditEventRepository(Protocol):
    """Abstract audit event persistence contract.

    Both methods return the persisted row's UUID. ``session`` is part
    of the Protocol surface (post-AM-4 convention, Slice 4 Phase B Q1).
    """

    async def append_pretenant_event(
        self,
        event: PretenantAuditEvent,
        *,
        session: AsyncSession | None = None,
    ) -> uuid.UUID:
        ...

    async def append_tenant_event(
        self,
        event: TenantAuditEvent,
        *,
        session: AsyncSession | None = None,
    ) -> uuid.UUID:
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryAuditEventRepository:
    """In-memory audit event repository for dev composition and unit tests.

    Stores pretenant and tenant events in separate dicts keyed by the
    assigned UUID. The ``session`` kwarg is accepted for Protocol
    parity but ignored — in-memory storage has no transaction.

    Does NOT replicate DB-level constraints (action allowlists, RLS,
    FK to tenants/workspaces). The domain layer
    (:class:`PretenantAuditEvent` / :class:`TenantAuditEvent`
    ``__post_init__``) is the only validator that runs against in-memory
    storage.
    """

    def __init__(self) -> None:
        self._pretenant: dict[uuid.UUID, PretenantAuditEvent] = {}
        self._tenant: dict[uuid.UUID, TenantAuditEvent] = {}

    async def append_pretenant_event(
        self,
        event: PretenantAuditEvent,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — Protocol parity
    ) -> uuid.UUID:
        new_id = uuid.uuid4()
        self._pretenant[new_id] = event
        return new_id

    async def append_tenant_event(
        self,
        event: TenantAuditEvent,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — Protocol parity
    ) -> uuid.UUID:
        new_id = uuid.uuid4()
        self._tenant[new_id] = event
        return new_id


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresAuditEventRepository:
    """SQLAlchemy 2.x async implementation of :class:`AuditEventRepository`.

    Pretenant path: ``SELECT audit_pretenant_insert(...)`` via SQLAlchemy
    text-bind. The function (migration 0008) is owned by ``neondb_owner``
    with BYPASSRLS and runs SECURITY DEFINER, so the caller's session
    role doesn't affect correctness. For ``session=None``, we use
    ``raw_admin_session`` because pretenant events have no tenant
    context to scope to.

    Tenant path: ORM INSERT into the ``audit_events`` table. The
    ``audit_events_insert`` RLS policy checks
    ``tenant_id = current_setting('app.current_tenant_id')::uuid``;
    rows whose tenant_id doesn't match the session GUC are rejected by
    PostgreSQL with SQLSTATE 42501 (insufficient_privilege) wrapped as
    a SQLAlchemy ``ProgrammingError``. For ``session=None``, we open a
    ``tenant_scoped_session`` derived from ``event.context.tenant_id``.

    Mirrors the AM-4 + AM-3 pattern established by
    :class:`PostgresComparisonRepository` (Turn 2.6).
    """

    async def append_pretenant_event(
        self,
        event: PretenantAuditEvent,
        *,
        session: AsyncSession | None = None,
    ) -> uuid.UUID:
        if session is None:
            async with raw_admin_session() as s:
                return await self._exec_pretenant(s, event)
        return await self._exec_pretenant(session, event)

    async def _exec_pretenant(
        self,
        s: AsyncSession,
        event: PretenantAuditEvent,
    ) -> uuid.UUID:
        """Execute the SECURITY DEFINER function call.

        Maps SQLSTATE 23514 (check_violation) to :class:`AuditEventInsertRejected`.
        Falls back to string matching on the SQL function name if SQLSTATE
        detection fails (defense in depth, should rarely fire).

        Does not commit. For ``session=None``, the caller's
        ``raw_admin_session`` context manager handles commit on clean
        exit and rollback on exception. For ``session=<provided>``, the
        caller owns transaction lifecycle.
        """
        try:
            result = await s.execute(
                _PRETENANT_INSERT_SQL,
                event.to_function_args(),
            )
        except DBAPIError as exc:
            # Preferred path: SQLSTATE 23514 (check_violation) detection
            if _is_check_violation(exc):
                raise AuditEventInsertRejected(
                    f"audit_pretenant_insert rejected the call "
                    f"(SQLSTATE 23514 check_violation): {exc!s}"
                ) from exc
            # Fallback: string matching on function name
            if "audit_pretenant_insert" in str(exc):
                raise AuditEventInsertRejected(
                    f"audit_pretenant_insert rejected the call: {exc!s}"
                ) from exc
            raise
        return result.scalar_one()

    async def append_tenant_event(
        self,
        event: TenantAuditEvent,
        *,
        session: AsyncSession | None = None,
    ) -> uuid.UUID:
        if session is None:
            async with tenant_scoped_session(str(event.context.tenant_id)) as s:
                return await self._exec_tenant(s, event)
        return await self._exec_tenant(session, event)

    async def _exec_tenant(
        self,
        s: AsyncSession,
        event: TenantAuditEvent,
    ) -> uuid.UUID:
        """Execute ORM INSERT for a tenant audit event.

        ORM construction is inline (Slice 4 Phase B Q3): no separate
        ``audit_event_to_orm`` mapper. The construction is straightforward
        field copy from ``event`` + ``event.context``.

        Does not commit. For ``session=None``, the caller's
        ``tenant_scoped_session`` context manager handles commit on clean
        exit. For ``session=<provided>``, the caller owns commit.

        The ORM import is lazy (inside the method) to avoid pulling
        SQLAlchemy ORM symbols into the module's top-level namespace —
        symmetry with the lazy mapper pattern in
        :class:`PostgresComparisonRepository`.
        """
        from src.db.models import AuditEvent as AuditEventORM  # lazy import

        new_id = uuid.uuid4()
        orm = AuditEventORM(
            id=new_id,
            tenant_id=event.context.tenant_id,
            workspace_id=event.context.workspace_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            actor_id=event.context.actor_id,
            actor_type=event.context.actor_type,
            actor_display=event.context.actor_display,
            correlation_id=event.context.correlation_id,
            ip_address=event.context.ip_address,
            user_agent=event.context.user_agent,
            details=event.details,
        )
        s.add(orm)
        await s.flush()
        return new_id
