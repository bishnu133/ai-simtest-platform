"""Safety tests for Alembic migration 0005_audit_nullable_tenant.

Per FH-Tier-1 plan v0.3 §4.4 + v0.3.3 + v0.3.4.

Migration 0005 introduced three artifacts:
  1. audit_events.tenant_id nullable
  2. ck_audit_tenant_required_or_pretenant CHECK constraint
  3. audit_events_pretenant_insert RLS policy (PARALLEL PERMISSIVE)

Migration 0006 supersedes artifact #3 — the parallel PERMISSIVE policy
did not, in practice, allow pretenant INSERTs under app_user despite
documented PG semantics (see 0006 module docstring for full context).
0006 drops audit_events_pretenant_insert and folds the carve-out into
tenant_isolation_audit_events directly.

This test file accordingly verifies only the artifacts that survive
post-0006: the nullable column (test 1) and the CHECK constraint
(test 1). 0006's policy widening is verified in
tests/db/test_migration_0006_safety.py.

Convention: matches tests/db/test_rls_enforcement.py and
tests/db/test_migration_safety.py — uses `raw_admin_session()` from
`src.db.session` as the async context manager, with module-level
`pytestmark = pytest.mark.asyncio`.

Note on partitioned audit_events:
  - CHECK constraints attached to the parent table are replicated by
    Postgres to every partition in pg_constraint. With the audit_events
    parent + 3 partitions (default + monthly), pg_constraint contains
    4 identical rows for the ck_audit_tenant_required_or_pretenant
    constraint. Test 1 verifies all copies have identical definitions
    rather than counting rows.

Note on roles:
  - The CHECK constraint applies regardless of role (it's a TABLE
    constraint, not an RLS policy). So `raw_admin_session()` (which
    connects as the Postgres superuser per conftest) exercises CHECK
    behavior correctly in tests 2 and 3.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


async def test_0005_upgrade_idempotent():
    """Post-upgrade DDL state for 0005's surviving artifacts is correct.

    Verifies the two artifacts that survive 0006: the nullable
    tenant_id column and the CHECK constraint (replicated across
    parent + partitions). The 0005-era audit_events_pretenant_insert
    policy is dropped by 0006 and is verified separately in
    test_migration_0006_safety.py.
    """
    async with raw_admin_session() as session:
        # 1. Column nullable
        result = await session.execute(sa.text(
            "SELECT is_nullable, data_type "
            "FROM information_schema.columns "
            "WHERE table_name = 'audit_events' AND column_name = 'tenant_id'"
        ))
        row = result.one()
        assert row.is_nullable == "YES", (
            f"tenant_id should be nullable after 0005; got is_nullable={row.is_nullable}"
        )
        assert row.data_type == "uuid"

        # 2. CHECK constraint present on parent + each partition.
        #    Postgres replicates parent CHECK to all partitions, producing
        #    one pg_constraint row per (parent + partitions). All rows must
        #    have identical definitions.
        result = await session.execute(sa.text(
            "SELECT pg_get_constraintdef(oid) AS definition "
            "FROM pg_constraint "
            "WHERE conname = 'ck_audit_tenant_required_or_pretenant'"
        ))
        rows = result.all()
        assert len(rows) >= 1, (
            "Expected at least 1 CHECK constraint row (parent + partition copies); "
            "got 0"
        )
        definitions = {row.definition for row in rows}
        assert len(definitions) == 1, (
            "Expected identical CHECK definition across parent + partitions; "
            f"got {len(definitions)} distinct definitions: {definitions}"
        )
        definition = next(iter(definitions))
        assert "tenant_id IS NOT NULL" in definition, (
            f"CHECK definition missing tenant_id IS NOT NULL clause: {definition}"
        )
        assert "auth.rejected" in definition, (
            f"CHECK definition missing auth.rejected allowance: {definition}"
        )


async def test_0005_check_blocks_disallowed_null_tenant():
    """INSERT with action != 'auth.rejected' and tenant_id=NULL must violate CHECK.

    Uses action='auth.accepted' as the disallowed case. The CHECK constraint
    applies at the TABLE level — independent of role — so raw_admin_session
    exercises it correctly.
    """
    async with raw_admin_session() as session:
        with pytest.raises((IntegrityError, sa.exc.DBAPIError)) as excinfo:
            await session.execute(sa.text(
                "INSERT INTO audit_events (action, actor_id, actor_type, tenant_id) "
                "VALUES ('auth.accepted', 'test_blocked_null_tenant', 'human', NULL)"
            ))
            await session.flush()

        err_text = str(excinfo.value)
        assert "ck_audit_tenant_required_or_pretenant" in err_text, (
            f"Expected CHECK constraint name in error; got: {err_text[:300]}"
        )


async def test_0005_check_allows_auth_rejected_null_tenant():
    """INSERT with action='auth.rejected' and tenant_id=NULL must succeed under admin.

    Admin bypasses RLS, so the RLS policies on audit_events are not
    exercised here. What IS exercised: the CHECK constraint allows NULL
    tenant_id for 'auth.rejected'. End-to-end RLS-policy behavior under
    app_user is verified by test_migration_0006_safety.py.

    The inserted row is intentionally retained per the audit_events_no_delete
    RESTRICTIVE RLS policy. A stable marker tags it for inspection.
    """
    async with raw_admin_session() as session:
        result = await session.execute(
            sa.text(
                "INSERT INTO audit_events (action, actor_id, actor_type, tenant_id) "
                "VALUES ('auth.rejected', :marker, 'system', NULL) "
                "RETURNING id, tenant_id, action, actor_id, actor_type"
            ),
            {"marker": "test_0005_allowed_auth_rejected"},
        )
        row = result.one()

        assert row.tenant_id is None, f"Expected NULL tenant_id, got {row.tenant_id!r}"
        assert row.action == "auth.rejected"
        assert row.actor_type == "system"
        assert row.actor_id == "test_0005_allowed_auth_rejected"


async def test_0005_downgrade_fails_loud_with_null_rows():
    """Static analysis: 0005's downgrade preserves fail-loud semantics.

    Asserts the migration file's downgrade body contains the SET NOT NULL
    step (which fails when NULL rows exist) plus the DROP POLICY and
    DROP CONSTRAINT steps in the correct order.

    Declared async-def to harmonize with module-level pytestmark.asyncio
    (the body is sync but pytest-asyncio AUTO mode accepts an async wrapper).
    """
    migration_path = (
        Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "0005_audit_events_nullable_tenant.py"
    )
    assert migration_path.exists(), f"Migration file not found at {migration_path}"
    source = migration_path.read_text()

    downgrade_idx = source.find("def downgrade")
    assert downgrade_idx > 0, "downgrade function not found in migration file"
    downgrade_section = source[downgrade_idx:]

    assert "DROP POLICY IF EXISTS audit_events_pretenant_insert" in downgrade_section, (
        "downgrade missing DROP POLICY for audit_events_pretenant_insert"
    )
    assert "DROP CONSTRAINT IF EXISTS ck_audit_tenant_required_or_pretenant" in downgrade_section, (
        "downgrade missing DROP CONSTRAINT for ck_audit_tenant_required_or_pretenant"
    )
    assert "ALTER COLUMN tenant_id SET NOT NULL" in downgrade_section, (
        "downgrade missing ALTER COLUMN ... SET NOT NULL (provides fail-loud behavior)"
    )

    assert "fail-loud" in source.lower() or "fail loud" in source.lower(), (
        "migration should document the intentional fail-loud downgrade behavior"
    )
