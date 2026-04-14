"""Tests for `src.db.session` — the tenant-scoped session primitive (plan §5).

These are the five Turn 1 session tests from the plan §8 test plan. They
verify the fail-closed contract (no tenant → no DB access), the SET LOCAL
wiring (Postgres actually sees the current_setting), the clean-up invariant
(tenant setting clears automatically on transaction end, even across the
pooled connection), and the parameterized-binding guarantee (no SQL
injection path from the tenant_id argument).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from src.db.session import (
    raw_admin_session,
    tenant_scoped_session,
)


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 1. Fail-closed: empty/None tenant_id raises BEFORE any DB work
# ---------------------------------------------------------------------------


async def test_empty_tenant_id_raises_value_error(migrated_db: str) -> None:
    """A None, empty, or whitespace-only tenant_id must fail-closed at the
    session primitive — before any connection is acquired, before any SQL
    runs. This is plan §5 "missing tenant → no DB access" enforced at the
    app layer on top of the Postgres-layer fail-closed guarantee.
    """
    for bad_value in (None, "", "   ", "\t\n"):
        with pytest.raises(ValueError, match="tenant_id"):
            async with tenant_scoped_session(bad_value) as _:  # type: ignore[arg-type]
                pytest.fail(
                    f"tenant_scoped_session should have raised ValueError "
                    f"for bad value {bad_value!r} but yielded a session"
                )


# ---------------------------------------------------------------------------
# 2. SET LOCAL wires the tenant into current_setting
# ---------------------------------------------------------------------------


async def test_set_local_wires_current_tenant_setting(
    clean_db: str,
) -> None:
    """Inside a tenant-scoped session, `current_setting('app.current_tenant_id')`
    must return the tenant_id we passed in. This is the SET LOCAL contract.
    """
    # Use a real tenant UUID from a seed row so RLS policies don't reject us.
    async with raw_admin_session() as admin:
        result = await admin.execute(
            sa.text(
                "INSERT INTO tenants (name, slug) "
                "VALUES ('Test Tenant', 'test-tenant') RETURNING id"
            )
        )
        tenant_uuid = str(result.scalar_one())

    async with tenant_scoped_session(tenant_uuid) as session:
        row = await session.execute(
            sa.text("SELECT current_setting('app.current_tenant_id', true)")
        )
        value = row.scalar_one()
        assert value == tenant_uuid, (
            f"Expected app.current_tenant_id = {tenant_uuid!r}, got {value!r}"
        )


# ---------------------------------------------------------------------------
# 3. SET LOCAL is transaction-scoped — setting does NOT leak across sessions
# ---------------------------------------------------------------------------


async def test_set_local_clears_automatically_between_sessions(
    clean_db: str,
) -> None:
    """Because SET LOCAL is bound to the current transaction, the tenant
    setting must be cleared when the transaction ends. A fresh session on
    a pooled connection must see an empty current_setting until its own
    SET LOCAL runs. This is the critical no-leak guarantee of plan §5.
    """
    async with raw_admin_session() as admin:
        result = await admin.execute(
            sa.text(
                "INSERT INTO tenants (name, slug) "
                "VALUES ('Tenant One', 'tenant-one') RETURNING id"
            )
        )
        tenant_uuid = str(result.scalar_one())

    # First session — sets the tenant and reads it back to confirm.
    async with tenant_scoped_session(tenant_uuid) as session:
        value = (
            await session.execute(
                sa.text("SELECT current_setting('app.current_tenant_id', true)")
            )
        ).scalar_one()
        assert value == tenant_uuid

    # Second session — raw admin, NO SET LOCAL. The setting must be unset.
    # `current_setting(..., true)` returns SQL NULL (→ Python None) when the
    # setting has never been configured on this connection/transaction.
    async with raw_admin_session() as admin:
        value = (
            await admin.execute(
                sa.text("SELECT current_setting('app.current_tenant_id', true)")
            )
        ).scalar_one()
        assert value in (None, ""), (
            f"Expected unset current_setting after transaction ended, got {value!r}. "
            f"This means SET LOCAL leaked across sessions — RLS is compromised."
        )


# ---------------------------------------------------------------------------
# 4. Rollback on exception — commits do not happen on error paths
# ---------------------------------------------------------------------------


async def test_session_rolls_back_on_exception(clean_db: str) -> None:
    """If an exception is raised inside the tenant_scoped_session block,
    all writes must be rolled back. The exception must propagate unchanged.
    """
    async with raw_admin_session() as admin:
        result = await admin.execute(
            sa.text(
                "INSERT INTO tenants (name, slug) "
                "VALUES ('Rollback Tenant', 'rollback-tenant') RETURNING id"
            )
        )
        tenant_uuid = str(result.scalar_one())

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        async with tenant_scoped_session(tenant_uuid) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO workspaces (tenant_id, name, is_default) "
                    "VALUES (:tid, 'Should not persist', false)"
                ),
                {"tid": tenant_uuid},
            )
            raise _Boom("simulated failure")

    # Verify the workspace did NOT persist.
    async with raw_admin_session() as admin:
        result = await admin.execute(
            sa.text("SELECT COUNT(*) FROM workspaces WHERE tenant_id = :tid"),
            {"tid": tenant_uuid},
        )
        assert result.scalar_one() == 0, (
            "Rollback failed — workspace persisted despite exception in block"
        )


# ---------------------------------------------------------------------------
# 5. Parameterized SET LOCAL is injection-safe
# ---------------------------------------------------------------------------


async def test_set_local_uses_parameter_binding_not_interpolation(
    clean_db: str,
) -> None:
    """The tenant_id argument must be passed through SQLAlchemy parameter
    binding, not string-interpolated into the SET statement.

    We prove this by passing a classic SQL-injection payload as the
    tenant_id:

        '; DROP TABLE tenants; --

    If this string were ever interpolated into the SET command, Postgres
    would execute `DROP TABLE tenants` and subsequent queries would fail
    with "relation does not exist". With proper parameter binding, the
    injection payload is treated as a literal string value for
    `app.current_tenant_id` — it is stored as-is, never executed.

    The assertion is structural: the tenants table still exists after the
    tenant_scoped_session block runs. This is the true invariant we care
    about; whether the block itself raises or not depends on whether any
    subsequent operation tries to cast the payload to uuid (which would
    fail cleanly), but the injection must NOT have been executed.
    """
    injection = "'; DROP TABLE tenants; --"

    # The injection payload is a non-empty string, so our fail-closed
    # validator accepts it and hands it to SET LOCAL as a bind parameter.
    # We do a harmless query inside; the injection (if it existed) would
    # run BEFORE this query, during SET LOCAL itself.
    try:
        async with tenant_scoped_session(injection) as session:
            await session.execute(sa.text("SELECT 1"))
    except Exception:  # noqa: BLE001
        # Any exception is fine — the parameter binding may cause
        # downstream failures (e.g., invalid uuid cast in an RLS policy).
        # What matters is whether the injection was EXECUTED.
        pass

    # Critical structural assertion: the tenants table still exists. If
    # SQL injection through tenant_id had succeeded, this query would
    # return 0 (relation dropped). Parameter binding guarantees 1.
    async with raw_admin_session() as admin:
        result = await admin.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'tenants'"
            )
        )
        assert result.scalar_one() == 1, (
            "tenants table was dropped — SQL injection through tenant_id "
            "succeeded. This is a critical security failure."
        )
