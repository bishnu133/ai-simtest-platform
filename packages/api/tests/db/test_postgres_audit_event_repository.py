"""Postgres integration tests for :mod:`src.audit.repository`.

Slice 4 of FH-Tier-1. Real Postgres required.

**Patch v0.1 (Slice 4 Drift 1):** 10 DB-using tests now request the
``clean_db`` fixture (defined in ``tests/db/conftest.py``). This brings
in the migration chain (``clean_db`` → ``migrated_db`` → ``pg_instance``)
and TRUNCATEs the 12 data tables before each test for isolation.

  Why ``clean_db`` and not ``migrated_db``:
    * ``clean_db`` TRUNCATEs ``tenants``, ``workspaces``, ``audit_events``,
      etc. before each test, giving full isolation between tests.
    * ``_ensure_test_tenants_and_workspaces()`` then re-seeds the FK
      targets idempotently — matches the existing project convention
      used by comparison/run/dashboard repository tests.

  Test 1 (Protocol conformance) does NOT request ``clean_db`` because
  it doesn't touch the database — instantiating the repository is
  stateless. Keeping its signature bare avoids paying TRUNCATE cost
  for a no-DB test.

Coverage:

* Protocol conformance for ``PostgresAuditEventRepository``
* Pretenant path
    - ``session=None`` opens ``raw_admin_session`` and persists a row
      with ``tenant_id IS NULL``
    - ``session=<provided>`` uses caller's session; caller rollback
      removes the row (proves AM-3 transaction ownership)
    - Returned UUID matches the persisted row's id
    - SECURITY DEFINER function rejection (SQLSTATE 23514) is mapped
      to :class:`AuditEventInsertRejected`
    - ``details`` dict round-trips through JSONB serialization
* Tenant path
    - ``session=None`` opens ``tenant_scoped_session`` derived from
      ``event.context.tenant_id``
    - ``session=<provided>`` uses caller's session; caller rollback
      removes the row
    - ``tenant_id`` and ``workspace_id`` come from event context
    - Cross-tenant INSERT under a mismatched session GUC is rejected
      by the ``audit_events_insert`` RLS policy
    - Returned UUID matches the persisted row's id

Test function names are sacred from Slice 4 close forward.
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from src.audit.context import (
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.audit.repository import (
    AuditEventInsertRejected,
    AuditEventRepository,
    PostgresAuditEventRepository,
)
from src.db.session import raw_admin_session, tenant_scoped_session


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


# Deterministic UUIDs for test tenants/workspaces. Re-seeded per test
# (because ``clean_db`` TRUNCATEs ``tenants`` and ``workspaces`` between
# tests) via :func:`_ensure_test_tenants_and_workspaces`.
_TEST_TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_TEST_TENANT_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
_TEST_WORKSPACE_A = uuid.UUID("33333333-3333-3333-3333-333333333333")
_TEST_WORKSPACE_B = uuid.UUID("44444444-4444-4444-4444-444444444444")


async def _ensure_test_tenants_and_workspaces() -> None:
    """Seed test tenants and workspaces. Idempotent (INSERT ON CONFLICT).

    Called at the top of each DB-using test. After ``clean_db`` TRUNCATEs
    ``tenants`` and ``workspaces``, this re-seeds the FK targets needed
    by ``audit_events.tenant_id`` / ``workspace_id``. The ON CONFLICT
    clause is defensive — it would only fire if the same UUIDs were
    seeded earlier within the same test (which we don't do).
    """
    async with raw_admin_session() as s:
        await s.execute(
            sa.text("""
                INSERT INTO tenants (id, name, slug)
                VALUES
                    (:tid_a, 'Slice4 Test Tenant A', 'slice4-tenant-a'),
                    (:tid_b, 'Slice4 Test Tenant B', 'slice4-tenant-b')
                ON CONFLICT (id) DO NOTHING
            """),
            {"tid_a": _TEST_TENANT_A, "tid_b": _TEST_TENANT_B},
        )
        await s.execute(
            sa.text("""
                INSERT INTO workspaces (id, tenant_id, name, slug, is_default)
                VALUES
                    (:wid_a, :tid_a, 'Slice4 Workspace A', 'slice4-ws-a', true),
                    (:wid_b, :tid_b, 'Slice4 Workspace B', 'slice4-ws-b', true)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "wid_a": _TEST_WORKSPACE_A,
                "tid_a": _TEST_TENANT_A,
                "wid_b": _TEST_WORKSPACE_B,
                "tid_b": _TEST_TENANT_B,
            },
        )


def _marker(phase: str) -> str:
    """Return a unique actor_id marker for row identification."""
    return f"sl4_{phase}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Test 1 — Protocol conformance (no DB)
# ---------------------------------------------------------------------------


async def test_postgres_audit_event_repository_implements_protocol():
    """``PostgresAuditEventRepository`` passes isinstance against the Protocol.

    Stateless instantiation; does not touch the database. No ``clean_db``
    fixture is requested because no DB is needed — keeps the test fast.
    """
    repo = PostgresAuditEventRepository()
    assert isinstance(repo, AuditEventRepository)


# ---------------------------------------------------------------------------
# Test 2–6 — Pretenant path
# ---------------------------------------------------------------------------


async def test_append_pretenant_event_session_none_persists_null_tenant_row(
    clean_db: str,
):
    """``append_pretenant_event`` with ``session=None`` opens its own session
    and persists a row with ``tenant_id IS NULL``.

    Verifies the function path: ``SELECT audit_pretenant_insert(...)``
    invokes the SECURITY DEFINER function, which inserts with hardcoded
    NULL tenant_id (per migration 0008 design).
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("pre_none")
    event = PretenantAuditEvent(
        actor_id=marker,
        correlation_id="sl4_corr_pre_none",
    )

    new_id = await repo.append_pretenant_event(event)

    assert isinstance(new_id, uuid.UUID)

    # Verify the row was persisted with tenant_id IS NULL.
    # Use raw_admin_session for unfiltered read across all tenants.
    async with raw_admin_session() as s:
        result = await s.execute(
            sa.text("""
                SELECT tenant_id, action, actor_id, actor_type, correlation_id
                FROM audit_events
                WHERE id = :id
            """),
            {"id": new_id},
        )
        row = result.one()
        assert row.tenant_id is None
        assert row.action == "auth.rejected"
        assert row.actor_id == marker
        assert row.actor_type == "system"  # PretenantAuditEvent default
        assert row.correlation_id == "sl4_corr_pre_none"


async def test_append_pretenant_event_session_provided_uses_caller_session(
    clean_db: str,
):
    """When ``session`` is provided, repository does NOT commit.

    Caller rollback removes the row. This is the AM-3 transaction
    ownership contract proven empirically — the repository must
    execute/flush only, never commit on a caller-provided session.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("pre_rollback")
    event = PretenantAuditEvent(actor_id=marker)

    # Open a session, call the repo, verify the row is visible within
    # the same transaction, then explicitly roll back.
    async with raw_admin_session() as caller_session:
        new_id = await repo.append_pretenant_event(
            event, session=caller_session
        )

        # Row visible within the caller's transaction (no commit yet)
        result = await caller_session.execute(
            sa.text("SELECT id FROM audit_events WHERE id = :id"),
            {"id": new_id},
        )
        assert result.scalar_one() == new_id

        # Caller explicitly rolls back. If the repository had committed,
        # this rollback would be a no-op and the row would persist.
        await caller_session.rollback()

    # Re-open a fresh session and verify the row is gone.
    # Repository did not commit → caller's rollback removed the row.
    async with raw_admin_session() as verify:
        result = await verify.execute(
            sa.text("SELECT id FROM audit_events WHERE id = :id"),
            {"id": new_id},
        )
        assert result.scalar_one_or_none() is None


async def test_append_pretenant_event_returns_uuid_matching_persisted_row(
    clean_db: str,
):
    """The UUID returned from ``audit_pretenant_insert()`` matches the
    persisted row's ``id`` column.

    Verifies the function's RETURN value plumbing: SQL function returns
    the new row's UUID, ``result.scalar_one()`` extracts it, and that
    UUID is the row's primary key.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("pre_uuid")
    event = PretenantAuditEvent(actor_id=marker)

    returned_id = await repo.append_pretenant_event(event)

    async with raw_admin_session() as s:
        result = await s.execute(
            sa.text(
                "SELECT id FROM audit_events WHERE actor_id = :marker"
            ),
            {"marker": marker},
        )
        persisted_id = result.scalar_one()

    assert returned_id == persisted_id


async def test_append_pretenant_event_disallowed_action_raises_db_error(
    monkeypatch,
    clean_db: str,
):
    """SECURITY DEFINER function rejects disallowed actions; the
    repository maps the SQLSTATE 23514 to :class:`AuditEventInsertRejected`.

    The domain layer (``PretenantAuditEvent.__post_init__``) is the
    primary guard against disallowed actions. We bypass it via
    ``monkeypatch`` to simulate a hypothetical domain-layer bypass and
    verify the DB-level defense-in-depth catches the call.

    The function (migration 0008) raises with
    ``ERRCODE = 'check_violation'`` for any action other than
    ``'auth.rejected'``. The repository's ``_is_check_violation`` helper
    detects SQLSTATE 23514 and surfaces :class:`AuditEventInsertRejected`.
    """
    await _ensure_test_tenants_and_workspaces()

    # Disable the domain-layer guard so we can construct an event with
    # a disallowed action. This simulates a hypothetical bypass; in
    # production code, the domain guard would catch this first.
    monkeypatch.setattr(
        PretenantAuditEvent, "__post_init__", lambda self: None
    )

    repo = PostgresAuditEventRepository()
    # 'auth.accepted' is NOT in PRETENANT_ACTION_ALLOWLIST
    event = PretenantAuditEvent(action="auth.accepted")

    with pytest.raises(AuditEventInsertRejected) as excinfo:
        await repo.append_pretenant_event(event)

    # Verify the exception's message references the function name
    # (defense-in-depth string check on top of the type assertion)
    assert "audit_pretenant_insert" in str(excinfo.value)


async def test_append_pretenant_event_serializes_details_via_jsonb_cast(
    clean_db: str,
):
    """``event.details`` (dict) is serialized via ``json.dumps`` in
    :meth:`PretenantAuditEvent.to_function_args` and then ``CAST(:details
    AS jsonb)`` in the SQL.

    Verifies the round-trip: dict → JSON string → JSONB column →
    SQLAlchemy/asyncpg deserialization → dict. The dict equality
    assertion fails if any layer drops or mangles the values.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("pre_details")
    test_details = {
        "reason": "invalid_jwt",
        "attempt_count": 5,
        "nested": {"key": "value", "list": [1, 2, 3]},
    }
    event = PretenantAuditEvent(actor_id=marker, details=test_details)

    new_id = await repo.append_pretenant_event(event)

    async with raw_admin_session() as s:
        result = await s.execute(
            sa.text("SELECT details FROM audit_events WHERE id = :id"),
            {"id": new_id},
        )
        persisted_details = result.scalar_one()

    # Round-trip equality — dict → JSON → JSONB → dict preserves structure
    assert persisted_details == test_details


# ---------------------------------------------------------------------------
# Test 7–11 — Tenant path
# ---------------------------------------------------------------------------


async def test_append_tenant_event_session_none_opens_tenant_scoped_session(
    clean_db: str,
):
    """``append_tenant_event`` with ``session=None`` opens a
    ``tenant_scoped_session`` derived from ``event.context.tenant_id``.

    Verifies the row is inserted under the correct tenant scope: the
    session GUC ``app.current_tenant_id`` is set to the event's tenant,
    and the RLS WITH CHECK permits the INSERT.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("ten_none")
    ctx = AuditContext(
        tenant_id=_TEST_TENANT_A,
        actor_id=marker,
        actor_type="human",
        workspace_id=_TEST_WORKSPACE_A,
    )
    event = TenantAuditEvent(action="run.created", context=ctx)

    new_id = await repo.append_tenant_event(event)

    assert isinstance(new_id, uuid.UUID)

    async with raw_admin_session() as s:
        result = await s.execute(
            sa.text("""
                SELECT tenant_id, workspace_id, action, actor_id
                FROM audit_events
                WHERE id = :id
            """),
            {"id": new_id},
        )
        row = result.one()
        assert row.tenant_id == _TEST_TENANT_A
        assert row.workspace_id == _TEST_WORKSPACE_A
        assert row.action == "run.created"
        assert row.actor_id == marker


async def test_append_tenant_event_session_provided_uses_caller_session(
    clean_db: str,
):
    """When session is provided for tenant event, repository does NOT commit.

    Same AM-3 transaction ownership proof as the pretenant variant.
    Caller's ``tenant_scoped_session`` is used; explicit rollback within
    the context manager removes the row, confirming the repository only
    flushes (no commit).
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("ten_rollback")
    ctx = AuditContext(
        tenant_id=_TEST_TENANT_A,
        actor_id=marker,
        actor_type="human",
        workspace_id=_TEST_WORKSPACE_A,
    )
    event = TenantAuditEvent(action="run.created", context=ctx)

    async with tenant_scoped_session(str(_TEST_TENANT_A)) as caller_session:
        new_id = await repo.append_tenant_event(
            event, session=caller_session
        )

        # Visible within the caller's transaction
        result = await caller_session.execute(
            sa.text("SELECT id FROM audit_events WHERE id = :id"),
            {"id": new_id},
        )
        assert result.scalar_one() == new_id

        # Explicit caller rollback
        await caller_session.rollback()

    # Re-open admin session and verify the row is gone
    async with raw_admin_session() as verify:
        result = await verify.execute(
            sa.text("SELECT id FROM audit_events WHERE id = :id"),
            {"id": new_id},
        )
        assert result.scalar_one_or_none() is None


async def test_append_tenant_event_persists_tenant_id_and_workspace_id_from_context(
    clean_db: str,
):
    """tenant_id and workspace_id come from ``event.context``, not from
    the session GUC or any inference.

    Verifies the inline ORM construction (Slice 4 Phase B Q3) wires
    ``event.context.tenant_id`` and ``event.context.workspace_id``
    correctly into the AuditEvent ORM object's columns.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("ten_ctx")
    ctx = AuditContext(
        tenant_id=_TEST_TENANT_A,
        actor_id=marker,
        actor_type="human",
        workspace_id=_TEST_WORKSPACE_A,
    )
    event = TenantAuditEvent(
        action="comparison.created",
        context=ctx,
        resource_type="comparison",
        resource_id="cmp_abc_123",
    )

    new_id = await repo.append_tenant_event(event)

    async with raw_admin_session() as s:
        result = await s.execute(
            sa.text("""
                SELECT tenant_id, workspace_id, resource_type, resource_id
                FROM audit_events
                WHERE id = :id
            """),
            {"id": new_id},
        )
        row = result.one()
        assert row.tenant_id == _TEST_TENANT_A
        assert row.workspace_id == _TEST_WORKSPACE_A
        assert row.resource_type == "comparison"
        assert row.resource_id == "cmp_abc_123"


async def test_append_tenant_event_wrong_tenant_session_blocked_by_rls(
    clean_db: str,
):
    """If the caller-provided session is scoped to TENANT_B but the event
    has TENANT_A's tenant_id, the ``audit_events_insert`` RLS policy
    rejects the INSERT.

    Defense in depth: even if the application layer makes a mistake
    (constructing an event with the wrong tenant_id, or routing to the
    wrong tenant_scoped_session), RLS prevents persistence under a
    mismatched session scope.

    Expected error: ``new row violates row-level security policy``
    (PG SQLSTATE 42501 / insufficient_privilege), wrapped by SQLAlchemy
    as a DBAPIError.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("ten_rls")

    # Event claims tenant A
    ctx_a = AuditContext(
        tenant_id=_TEST_TENANT_A,
        actor_id=marker,
        actor_type="human",
        workspace_id=_TEST_WORKSPACE_A,
    )
    event = TenantAuditEvent(action="run.created", context=ctx_a)

    # Session scoped to tenant B → RLS WITH CHECK fails because
    # event.tenant_id (A) != current_setting('app.current_tenant_id') (B)
    with pytest.raises(DBAPIError) as excinfo:
        async with tenant_scoped_session(
            str(_TEST_TENANT_B)
        ) as wrong_session:
            await repo.append_tenant_event(event, session=wrong_session)

    # Verify the rejection is specifically RLS, not some unrelated error
    assert "row-level security policy" in str(excinfo.value).lower()


async def test_append_tenant_event_returns_uuid_matching_persisted_row(
    clean_db: str,
):
    """The UUID returned by ``append_tenant_event`` matches the persisted
    row's ``id`` column.

    For the tenant path the UUID is allocated by the repository (not by
    PG default), so this verifies the ORM ``id=new_id`` assignment
    propagates to the actual INSERT statement and back to the caller.
    """
    await _ensure_test_tenants_and_workspaces()

    repo = PostgresAuditEventRepository()
    marker = _marker("ten_uuid")
    ctx = AuditContext(
        tenant_id=_TEST_TENANT_A,
        actor_id=marker,
        actor_type="human",
        workspace_id=_TEST_WORKSPACE_A,
    )
    event = TenantAuditEvent(action="run.created", context=ctx)

    returned_id = await repo.append_tenant_event(event)

    async with raw_admin_session() as s:
        result = await s.execute(
            sa.text(
                "SELECT id FROM audit_events WHERE actor_id = :marker"
            ),
            {"marker": marker},
        )
        persisted_id = result.scalar_one()

    assert returned_id == persisted_id
