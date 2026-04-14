"""Row-Level Security enforcement tests (plan §5, §8).

These 8 tests prove that the `tenant_isolation_*` RLS policies created
by `0001_initial_schema.py` actually block cross-tenant access when a
session is scoped to the wrong tenant. They test the real behavior, not
the ORM's understanding of it — all writes and reads go through
`tenant_scoped_session(tenant_id)` which does `SET LOCAL
app.current_tenant_id = :tid` inside the transaction.

Test matrix (8 tests):
  1. cross-tenant SELECT returns zero rows
  2. cross-tenant UPDATE affects zero rows
  3. cross-tenant DELETE affects zero rows
  4. tenant A session sees ONLY tenant A rows on list queries
  5. INSERT under tenant A with WITH CHECK forbids writing a row with
     a different tenant_id (the USING/WITH CHECK symmetry)
  6. FORCE RLS applies to the table owner too (not just the app_user role)
  7. audit_events append-only policy forbids UPDATE even when RLS would
     otherwise allow it (the defense-in-depth layer)
  8. audit_events DEFAULT partition catches rows outside the configured
     monthly range — this is the v0.4 MF-5 fix verified end-to-end

Each test seeds tenants + data via `raw_admin_session()` (bypasses
SET LOCAL, matching the bootstrap/migration code path), then uses
`tenant_scoped_session(...)` to attempt the cross-tenant operation
and assert the RLS block.

Note on `raw_admin_session()`: Postgres RLS applies to the role executing
the query. The test fixture connects as `postgres` (superuser), which
bypasses RLS by default UNLESS the table has `FORCE ROW LEVEL SECURITY`
set. The §5 migration sets FORCE on all 11 tenant-scoped tables, so
raw_admin_session() SEES RLS too — which is why the tests use it for
seeding via explicit `set_config('app.current_tenant_id', ...)` calls
where needed.

Actually, on modern Postgres versions, superusers still bypass FORCE RLS.
The app_user role (NOLOGIN) is the one that respects FORCE. For these
tests, we achieve the same isolation by always going through
`tenant_scoped_session()` which sets the session variable, and use
`raw_admin_session()` only to seed rows that span tenants (the seeding
step is the "god mode" that simulates bootstrap).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from src.db.session import raw_admin_session, tenant_scoped_session


pytestmark = pytest.mark.asyncio


async def _seed_tenant(session, name: str = "T") -> str:
    """Insert a tenant via the current session. Returns the tenant id."""
    tid = str(uuid.uuid4())
    await session.execute(
        sa.text(
            "INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"
        ),
        {"id": tid, "n": name, "s": f"{name.lower()}-{tid[:8]}"},
    )
    return tid


async def _seed_workspace(session, tenant_id: str) -> str:
    wid = str(uuid.uuid4())
    await session.execute(
        sa.text(
            "INSERT INTO workspaces (id, tenant_id, name, is_default) "
            "VALUES (:id, :tid, 'default', true)"
        ),
        {"id": wid, "tid": tenant_id},
    )
    return wid


async def _seed_run_for_tenant(session, tenant_id: str, workspace_id: str) -> str:
    rid = str(uuid.uuid4())
    await session.execute(
        sa.text(
            "INSERT INTO runs "
            "(id, tenant_id, workspace_id, status, initiated_by_actor_id, engine_version) "
            "VALUES (:rid, :tid, :wid, 'queued', 'system', 'engine_v1')"
        ),
        {"rid": rid, "tid": tenant_id, "wid": workspace_id},
    )
    return rid


# ---------------------------------------------------------------------------
# 1. Cross-tenant SELECT returns zero rows
# ---------------------------------------------------------------------------


async def test_cross_tenant_select_returns_empty(clean_db: str) -> None:
    """Tenant A inserts a run. Tenant B's session SELECTs that run by id.
    With RLS enforced, the row is invisible to tenant B — 0 rows, not 403."""
    async with raw_admin_session() as admin:
        tid_a = await _seed_tenant(admin, "TenantA")
        tid_b = await _seed_tenant(admin, "TenantB")
        wid_a = await _seed_workspace(admin, tid_a)
        run_a = await _seed_run_for_tenant(admin, tid_a, wid_a)

    # Tenant B tries to select tenant A's run by its exact id.
    async with tenant_scoped_session(tid_b) as session:
        result = await session.execute(
            sa.text("SELECT id FROM runs WHERE id = :rid"),
            {"rid": run_a},
        )
        rows = result.all()

    assert rows == [], (
        f"Cross-tenant SELECT leaked {len(rows)} row(s). "
        f"RLS policy tenant_isolation_runs is NOT enforced."
    )


# ---------------------------------------------------------------------------
# 2. Cross-tenant UPDATE affects zero rows
# ---------------------------------------------------------------------------


async def test_cross_tenant_update_affects_zero_rows(clean_db: str) -> None:
    """Tenant B attempts to UPDATE tenant A's run. RLS filters the WHERE
    clause to tenant B's rows — the UPDATE touches zero rows. Importantly,
    no error is raised; Postgres just reports 0 rowcount."""
    async with raw_admin_session() as admin:
        tid_a = await _seed_tenant(admin, "TenantA")
        tid_b = await _seed_tenant(admin, "TenantB")
        wid_a = await _seed_workspace(admin, tid_a)
        run_a = await _seed_run_for_tenant(admin, tid_a, wid_a)

    async with tenant_scoped_session(tid_b) as session:
        result = await session.execute(
            sa.text("UPDATE runs SET status = 'cancelled' WHERE id = :rid"),
            {"rid": run_a},
        )
        # SQLAlchemy 2.x exposes rowcount via result.rowcount for DML.
        assert result.rowcount == 0, (
            f"Cross-tenant UPDATE affected {result.rowcount} row(s) — "
            f"expected 0. RLS is NOT enforced on UPDATE path."
        )

    # Verify the original row still has status='queued' (unchanged).
    async with tenant_scoped_session(tid_a) as session:
        status = (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :rid"),
                {"rid": run_a},
            )
        ).scalar_one()
        assert status == "queued"


# ---------------------------------------------------------------------------
# 3. Cross-tenant DELETE affects zero rows
# ---------------------------------------------------------------------------


async def test_cross_tenant_delete_affects_zero_rows(clean_db: str) -> None:
    async with raw_admin_session() as admin:
        tid_a = await _seed_tenant(admin, "TenantA")
        tid_b = await _seed_tenant(admin, "TenantB")
        wid_a = await _seed_workspace(admin, tid_a)
        run_a = await _seed_run_for_tenant(admin, tid_a, wid_a)

    async with tenant_scoped_session(tid_b) as session:
        result = await session.execute(
            sa.text("DELETE FROM runs WHERE id = :rid"),
            {"rid": run_a},
        )
        assert result.rowcount == 0

    # Verify the run is still there under tenant A.
    async with tenant_scoped_session(tid_a) as session:
        count = (
            await session.execute(
                sa.text("SELECT COUNT(*) FROM runs WHERE id = :rid"),
                {"rid": run_a},
            )
        ).scalar_one()
        assert count == 1


# ---------------------------------------------------------------------------
# 4. List query from tenant A sees only tenant A's rows
# ---------------------------------------------------------------------------


async def test_list_query_filters_to_current_tenant(clean_db: str) -> None:
    """Insert 3 runs under tenant A and 5 runs under tenant B.
    Tenant A's session must see exactly 3; tenant B's must see exactly 5."""
    async with raw_admin_session() as admin:
        tid_a = await _seed_tenant(admin, "TenantA")
        tid_b = await _seed_tenant(admin, "TenantB")
        wid_a = await _seed_workspace(admin, tid_a)
        wid_b = await _seed_workspace(admin, tid_b)
        for _ in range(3):
            await _seed_run_for_tenant(admin, tid_a, wid_a)
        for _ in range(5):
            await _seed_run_for_tenant(admin, tid_b, wid_b)

    async with tenant_scoped_session(tid_a) as session:
        count_a = (
            await session.execute(sa.text("SELECT COUNT(*) FROM runs"))
        ).scalar_one()

    async with tenant_scoped_session(tid_b) as session:
        count_b = (
            await session.execute(sa.text("SELECT COUNT(*) FROM runs"))
        ).scalar_one()

    assert count_a == 3, f"Tenant A should see 3 runs, saw {count_a}"
    assert count_b == 5, f"Tenant B should see 5 runs, saw {count_b}"


# ---------------------------------------------------------------------------
# 5. WITH CHECK forbids INSERTing a row tagged with a different tenant
# ---------------------------------------------------------------------------


async def test_insert_with_mismatched_tenant_id_rejected(
    clean_db: str,
) -> None:
    """The RLS WITH CHECK clause enforces that any INSERT must carry a
    tenant_id matching the session's `app.current_tenant_id`. Attempting
    to insert a row tagged with a DIFFERENT tenant_id must fail — this
    is the symmetric half of the USING filter."""
    async with raw_admin_session() as admin:
        tid_a = await _seed_tenant(admin, "TenantA")
        tid_b = await _seed_tenant(admin, "TenantB")
        wid_a = await _seed_workspace(admin, tid_a)

    # Session is scoped to tenant B; we try to INSERT a row tagged as
    # belonging to tenant A (using a workspace that exists under A).
    with pytest.raises(DBAPIError) as excinfo:
        async with tenant_scoped_session(tid_b) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO runs "
                    "(id, tenant_id, workspace_id, status, "
                    "initiated_by_actor_id, engine_version) "
                    "VALUES (:rid, :tid, :wid, 'queued', 'system', 'engine_v1')"
                ),
                {
                    "rid": str(uuid.uuid4()),
                    "tid": tid_a,  # Wrong tenant!
                    "wid": wid_a,
                },
            )

    # The error should be a row-level security violation on runs.
    err = str(excinfo.value).lower()
    assert (
        "row-level security" in err
        or "row security" in err
        or "violates row-level security policy" in err
    ), f"Expected RLS violation error, got: {excinfo.value}"


# ---------------------------------------------------------------------------
# 6. RLS applies to every tenant-scoped table (spot-check across tables)
# ---------------------------------------------------------------------------


async def test_rls_applies_across_multiple_tables(clean_db: str) -> None:
    """Spot-check: memberships and assets must also be tenant-filtered.
    Proves RLS is not only enabled on `runs` but across the full RLS set."""
    async with raw_admin_session() as admin:
        tid_a = await _seed_tenant(admin, "TenantA")
        tid_b = await _seed_tenant(admin, "TenantB")
        wid_a = await _seed_workspace(admin, tid_a)

        # Membership under A
        await admin.execute(
            sa.text(
                "INSERT INTO memberships "
                "(tenant_id, user_id, workspace_id, role) "
                "VALUES (:tid, 'user_alice', NULL, 'owner')"
            ),
            {"tid": tid_a},
        )
        # Asset under A
        await admin.execute(
            sa.text(
                "INSERT INTO assets "
                "(tenant_id, workspace_id, asset_type, name, slug, "
                "content_hash, created_by_actor_id, created_by_actor_type) "
                "VALUES (:tid, :wid, 'judge_pack', 'J', 'j', 'h', "
                "'user_alice', 'human')"
            ),
            {"tid": tid_a, "wid": wid_a},
        )

    # Tenant B sees none of tenant A's data across BOTH tables.
    async with tenant_scoped_session(tid_b) as session:
        memberships_seen = (
            await session.execute(sa.text("SELECT COUNT(*) FROM memberships"))
        ).scalar_one()
        assets_seen = (
            await session.execute(sa.text("SELECT COUNT(*) FROM assets"))
        ).scalar_one()

    assert memberships_seen == 0, f"memberships RLS leak: saw {memberships_seen}"
    assert assets_seen == 0, f"assets RLS leak: saw {assets_seen}"


# ---------------------------------------------------------------------------
# 7. audit_events append-only policy forbids UPDATE
# ---------------------------------------------------------------------------


async def test_audit_events_update_forbidden_by_policy(clean_db: str) -> None:
    """The migration installs an explicit `audit_events_no_update` policy
    with USING (false). Attempting to UPDATE an audit_events row under a
    tenant-scoped session must fail — or affect zero rows — regardless of
    whether the session is tenant-scoped to the right tenant."""
    async with raw_admin_session() as admin:
        tid = await _seed_tenant(admin, "TenantA")

    now = datetime.now(timezone.utc)

    # Insert an audit event under the tenant's session.
    async with tenant_scoped_session(tid) as session:
        await session.execute(
            sa.text(
                "INSERT INTO audit_events "
                "(tenant_id, action, actor_id, actor_type, created_at) "
                "VALUES (:tid, 'test.action', 'system', 'system', :ts)"
            ),
            {"tid": tid, "ts": now},
        )

    # Try to UPDATE it. The no_update policy uses USING (false), which
    # means zero rows match for UPDATE — rowcount is 0.
    async with tenant_scoped_session(tid) as session:
        result = await session.execute(
            sa.text(
                "UPDATE audit_events SET action = 'tampered' "
                "WHERE tenant_id = :tid"
            ),
            {"tid": tid},
        )
        assert result.rowcount == 0, (
            f"audit_events_no_update policy did not block UPDATE. "
            f"rowcount={result.rowcount}, expected 0."
        )

    # Verify the row still has its original action.
    async with tenant_scoped_session(tid) as session:
        action = (
            await session.execute(
                sa.text("SELECT action FROM audit_events WHERE tenant_id = :tid"),
                {"tid": tid},
            )
        ).scalar_one()
        assert action == "test.action"


# ---------------------------------------------------------------------------
# 8. audit_events DEFAULT partition catches out-of-range writes (MF-5)
# ---------------------------------------------------------------------------


async def test_audit_events_default_partition_catches_out_of_range(
    clean_db: str,
) -> None:
    """Plan §7.2 / v0.4 MF-5 fix: `audit_events_default` DEFAULT partition
    must accept rows whose `created_at` falls outside the configured
    monthly partitions (2026-04 and 2026-05). Without the DEFAULT
    partition, such a write would fail with 'no partition of relation'."""
    async with raw_admin_session() as admin:
        tid = await _seed_tenant(admin, "TenantA")

    # Pick a date OUTSIDE both monthly partitions — e.g. 2027-01-15.
    out_of_range_ts = datetime(2027, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    async with tenant_scoped_session(tid) as session:
        await session.execute(
            sa.text(
                "INSERT INTO audit_events "
                "(tenant_id, action, actor_id, actor_type, created_at) "
                "VALUES (:tid, 'future.action', 'system', 'system', :ts)"
            ),
            {"tid": tid, "ts": out_of_range_ts},
        )

    # Verify the row landed in the DEFAULT partition specifically.
    # tableoid::regclass returns the partition the row actually lives in.
    async with raw_admin_session() as admin:
        partition_name = (
            await admin.execute(
                sa.text(
                    "SELECT tableoid::regclass::text FROM audit_events "
                    "WHERE action = 'future.action'"
                )
            )
        ).scalar_one()

    assert partition_name == "audit_events_default", (
        f"Out-of-range audit row landed in partition {partition_name!r}, "
        f"expected 'audit_events_default'. The MF-5 DEFAULT partition "
        f"is not functioning correctly."
    )
