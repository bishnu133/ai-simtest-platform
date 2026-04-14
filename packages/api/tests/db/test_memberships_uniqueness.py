"""Sprint-gating tests for the memberships partial unique indexes (plan §8.1).

These three tests verify the v0.5.1 §7.1 fix at the DB layer — NOT through
a mock, NOT through a Python-side pre-check, but via a real IntegrityError
raised by Postgres when the partial unique indexes are violated.

The v0.5 plan had a schema-level correctness bug: a single
`UNIQUE(tenant_id, user_id, workspace_id)` constraint would permit
multiple rows with `(tenant_id, user_id, NULL)` because Postgres treats
NULL as distinct in full-tuple unique constraints. That would allow
duplicate tenant-level memberships and make authz non-deterministic.

The v0.5.1 fix replaces the single constraint with two partial unique
indexes:

    CREATE UNIQUE INDEX uq_memberships_tenant_level
        ON memberships (tenant_id, user_id)
        WHERE workspace_id IS NULL;

    CREATE UNIQUE INDEX uq_memberships_workspace_level
        ON memberships (tenant_id, user_id, workspace_id)
        WHERE workspace_id IS NOT NULL;

These tests prove both indexes are in place and working:

  1. test_duplicate_tenant_level_row_rejected — the bug case: two NULL
     rows for the same (tenant, user) must be rejected.
  2. test_tenant_level_and_workspace_level_rows_coexist — the valid
     dual-membership shape that bootstrap creates.
  3. test_duplicate_workspace_level_row_rejected — proves we did not
     weaken workspace-row uniqueness while fixing tenant-row uniqueness.
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


async def _seed_tenant_and_workspace(session) -> tuple[str, str]:
    """Create one tenant + one workspace. Returns (tenant_id, workspace_id)."""
    tid = str(uuid.uuid4())
    wid = str(uuid.uuid4())
    await session.execute(
        sa.text(
            "INSERT INTO tenants (id, name, slug) "
            "VALUES (:id, :name, :slug)"
        ),
        {"id": tid, "name": f"T-{tid[:8]}", "slug": f"t-{tid[:8]}"},
    )
    await session.execute(
        sa.text(
            "INSERT INTO workspaces (id, tenant_id, name, is_default) "
            "VALUES (:id, :tid, 'default', true)"
        ),
        {"id": wid, "tid": tid},
    )
    await session.commit()
    return tid, wid


async def _insert_membership(
    session,
    *,
    tenant_id: str,
    user_id: str,
    workspace_id: str | None,
    role: str = "owner",
) -> None:
    """Insert a membership row via raw SQL, NOT via an ON CONFLICT path —
    because these tests are specifically testing what happens WITHOUT the
    ON CONFLICT clause, i.e. the raw index-enforced guarantee.
    """
    await session.execute(
        sa.text(
            "INSERT INTO memberships (tenant_id, user_id, workspace_id, role) "
            "VALUES (:tid, :uid, :wid, :role)"
        ),
        {
            "tid": tenant_id,
            "uid": user_id,
            "wid": workspace_id,
            "role": role,
        },
    )


# ---------------------------------------------------------------------------
# 1. The bug case: duplicate tenant-level rows must be rejected
# ---------------------------------------------------------------------------


async def test_duplicate_tenant_level_row_rejected(clean_db: str) -> None:
    """Insert one tenant-level membership (workspace_id = NULL). Attempt
    to insert a SECOND row with the same (tenant_id, user_id) and
    workspace_id = NULL. Expect IntegrityError naming the partial index
    `uq_memberships_tenant_level`.

    This is the v0.5 → v0.5.1 MF-1 regression guard. If it passes, the
    partial unique index is doing its job.
    """
    async with raw_admin_session() as session:
        tid, _wid = await _seed_tenant_and_workspace(session)

    # First row: succeeds.
    async with raw_admin_session() as session:
        await _insert_membership(
            session,
            tenant_id=tid,
            user_id="user_alice",
            workspace_id=None,
            role="owner",
        )

    # Second row with identical (tenant_id, user_id, NULL): must fail.
    with pytest.raises(IntegrityError) as excinfo:
        async with raw_admin_session() as session:
            await _insert_membership(
                session,
                tenant_id=tid,
                user_id="user_alice",
                workspace_id=None,
                role="admin",  # different role, same key → still rejected
            )

    # The error message must reference the specific partial index by name,
    # so failures in the field are immediately actionable.
    err_text = str(excinfo.value).lower()
    assert "uq_memberships_tenant_level" in err_text, (
        f"Expected IntegrityError to name the partial unique index "
        f"'uq_memberships_tenant_level', got: {excinfo.value}"
    )

    # And the original row must still be the only one.
    async with raw_admin_session() as session:
        count = (
            await session.execute(
                sa.text(
                    "SELECT COUNT(*) FROM memberships "
                    "WHERE tenant_id = :tid AND user_id = 'user_alice' "
                    "AND workspace_id IS NULL"
                ),
                {"tid": tid},
            )
        ).scalar_one()
        assert count == 1


# ---------------------------------------------------------------------------
# 2. The valid dual-membership shape must be allowed
# ---------------------------------------------------------------------------


async def test_tenant_level_and_workspace_level_rows_coexist(
    clean_db: str,
) -> None:
    """Plan §6.5 bootstrap creates TWO rows per user:
       - one tenant-level (workspace_id = NULL)
       - one workspace-level (workspace_id = UUID)

    Both must succeed. After inserting both, the membership table must
    contain exactly 2 rows for that user under that tenant.

    This test protects against an overcorrection: if someone "fixed" the
    uniqueness by adding a stricter full-key unique constraint that
    rejects the NULL+UUID combination, bootstrap would start failing.
    """
    async with raw_admin_session() as session:
        tid, wid = await _seed_tenant_and_workspace(session)

    async with raw_admin_session() as session:
        # Tenant-level row
        await _insert_membership(
            session,
            tenant_id=tid,
            user_id="user_bob",
            workspace_id=None,
            role="owner",
        )
        # Workspace-level row for the same (tenant, user)
        await _insert_membership(
            session,
            tenant_id=tid,
            user_id="user_bob",
            workspace_id=wid,
            role="owner",
        )

    async with raw_admin_session() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT workspace_id, role FROM memberships "
                    "WHERE tenant_id = :tid AND user_id = 'user_bob' "
                    "ORDER BY workspace_id NULLS FIRST"
                ),
                {"tid": tid},
            )
        ).all()

    assert len(rows) == 2, f"Expected 2 rows for user_bob, got {len(rows)}"

    # Row 0: tenant-level (workspace_id is None)
    assert rows[0][0] is None
    assert rows[0][1] == "owner"
    # Row 1: workspace-level (workspace_id matches). asyncpg returns
    # uuid.UUID objects for UUID columns; cast to str for comparison.
    assert str(rows[1][0]) == wid
    assert rows[1][1] == "owner"


# ---------------------------------------------------------------------------
# 3. Workspace-level uniqueness must still be enforced
# ---------------------------------------------------------------------------


async def test_duplicate_workspace_level_row_rejected(
    clean_db: str,
) -> None:
    """Insert one workspace-level row for (tenant, user, workspace_A).
    Attempt a SECOND row with the exact same three values. Expect
    IntegrityError naming `uq_memberships_workspace_level`.

    This proves the fix didn't accidentally weaken workspace-row
    uniqueness while strengthening tenant-row uniqueness.
    """
    async with raw_admin_session() as session:
        tid, wid = await _seed_tenant_and_workspace(session)

    async with raw_admin_session() as session:
        await _insert_membership(
            session,
            tenant_id=tid,
            user_id="user_carol",
            workspace_id=wid,
            role="member",
        )

    with pytest.raises(IntegrityError) as excinfo:
        async with raw_admin_session() as session:
            await _insert_membership(
                session,
                tenant_id=tid,
                user_id="user_carol",
                workspace_id=wid,
                role="viewer",  # different role, same (tid, uid, wid) key → rejected
            )

    err_text = str(excinfo.value).lower()
    assert "uq_memberships_workspace_level" in err_text, (
        f"Expected IntegrityError to name "
        f"'uq_memberships_workspace_level', got: {excinfo.value}"
    )

    # Only one row should remain.
    async with raw_admin_session() as session:
        count = (
            await session.execute(
                sa.text(
                    "SELECT COUNT(*) FROM memberships "
                    "WHERE tenant_id = :tid AND user_id = 'user_carol' "
                    "AND workspace_id = :wid"
                ),
                {"tid": tid, "wid": wid},
            )
        ).scalar_one()
        assert count == 1
