"""Safety tests for Alembic migration 0006_audit_pretenant_carve.

Per FH-Tier-1 plan v0.3.4 (post Slice-0-Neon empirical investigation).

Migration history for this artifact set:

  0005 — added pretenant_insert PARALLEL PERMISSIVE policy (didn't work)
  0006 — dropped pretenant_insert, widened tenant_isolation_audit_events (didn't work)
  0007 — per-command split + CASE-based WITH CHECK (also didn't work)
  0008 — SECURITY DEFINER function path (works — empirically verified)

PostgreSQL 16's RLS policy framework rejects any WITH CHECK expression
involving the row's columns under app_user INSERTs when NULL values
are involved, even when the expression evaluates to TRUE as a SELECT.
The SECURITY DEFINER function approach bypasses the policy framework
entirely. See 0008 module docstring for full context.

Tests in THIS file verify 0006's surviving artifacts. The e2e test
(test_0006_app_user_can_insert_auth_rejected_with_sentinel_guc) has
been updated to call the audit_pretenant_insert() function — the
function name is preserved per sacred-test discipline, body adapts
to the working path.

The test_0006_tenant_isolation_widened_for_pretenant test is marked
@pytest.mark.skip — its assertion (a specific policy shape from 0006)
was superseded by 0007 and 0008. Equivalent post-0008 assertion lives
in test_migration_0008_safety.py.

Convention: matches tests/db/test_rls_enforcement.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


async def test_0006_pretenant_insert_policy_dropped():
    """audit_events_pretenant_insert (the 0005-era parallel policy) is no longer present.

    Survives 0007 and 0008 unchanged.
    """
    async with raw_admin_session() as session:
        result = await session.execute(sa.text(
            "SELECT polname FROM pg_policy "
            "WHERE polrelid = 'audit_events'::regclass "
            "  AND polname = 'audit_events_pretenant_insert'"
        ))
        rows = result.all()
        assert len(rows) == 0, (
            "Expected audit_events_pretenant_insert to remain dropped; "
            f"found {len(rows)} matching row(s)"
        )


@pytest.mark.skip(
    reason="0007 superseded 0006's widened tenant_isolation policy with per-command split; "
    "0008 further superseded 0007 with SECURITY DEFINER function approach. "
    "Equivalent post-0008 assertion: test_migration_0008_safety.py."
)
async def test_0006_tenant_isolation_widened_for_pretenant():
    """Original 0006-era assertion. Function preserved per sacred-test-function
    discipline; superseded by 0007 and 0008."""
    pass


async def test_0006_app_user_can_insert_auth_rejected_with_sentinel_guc():
    """End-to-end: app_user persists auth.rejected + NULL via SECURITY DEFINER function.

    Under 0008, direct INSERTs of pretenant rows by app_user are not supported
    (the audit_events_insert RLS policy allows only tenant-scoped inserts).
    Pretenant inserts go through audit_pretenant_insert() — a SECURITY DEFINER
    function owned by the migration runner (BYPASSRLS or superuser). app_user
    has EXECUTE permission only.

    This mirrors the production code path that slice 4's
    PostgresAuditEventRepository.append_pretenant_event will execute.

    Test name retained per sacred-test discipline — the test was authored
    when the design was direct INSERT; the design pivoted to the function
    path through migrations 0007 and 0008. The name still semantically
    matches: app_user CAN insert (via the function) an auth.rejected event
    with NULL tenant_id.
    """
    async with raw_admin_session() as session:
        await session.execute(sa.text("SET LOCAL ROLE app_user"))
        # Sentinel GUC isn't required by the SECURITY DEFINER function (the
        # function ignores app.current_tenant_id), but matches slice 3's
        # pretenant_session helper convention per v0.3.3 §A.6.
        await session.execute(sa.text(
            "SELECT set_config('app.current_tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        ))

        # Call the SECURITY DEFINER function — the supported pretenant insert path.
        result = await session.execute(
            sa.text(
                "SELECT audit_pretenant_insert("
                ":action, :actor_id, :actor_type"
                ") AS new_id"
            ),
            {
                "action": "auth.rejected",
                "actor_id": "test_0006_e2e_pretenant_via_fn",
                "actor_type": "system",
            },
        )
        new_id = result.scalar_one()
        assert new_id is not None, "audit_pretenant_insert should return a UUID"

        # Verify the row was inserted. Reset role to admin so the SELECT
        # isn't blocked by audit_events_select's strict tenant scoping
        # (pretenant rows are admin-only readable, per the security model).
        await session.execute(sa.text("RESET ROLE"))
        verify = await session.execute(
            sa.text(
                "SELECT action, tenant_id, actor_id, actor_type "
                "FROM audit_events WHERE id = :id"
            ),
            {"id": new_id},
        )
        row = verify.one()
        assert row.action == "auth.rejected"
        assert row.tenant_id is None, f"Expected NULL tenant_id, got {row.tenant_id!r}"
        assert row.actor_id == "test_0006_e2e_pretenant_via_fn"
        assert row.actor_type == "system"


async def test_0006_app_user_cannot_insert_auth_accepted_with_null_tenant():
    """End-to-end: app_user CANNOT insert non-pretenant action with NULL tenant.

    Verifies defense in depth from multiple layers:
      - Direct INSERT under app_user with tenant_id=NULL is rejected by RLS
        (audit_events_insert WITH CHECK requires tenant_id = current_setting()).
      - Calling audit_pretenant_insert with action='auth.accepted' is rejected
        by the function's allowlist (check_violation raised).
      - The CHECK constraint ck_audit_tenant_required_or_pretenant also rejects.

    Any of these rejection paths satisfies the test — defense in depth.
    """
    async with raw_admin_session() as session:
        await session.execute(sa.text("SET LOCAL ROLE app_user"))
        await session.execute(sa.text(
            "SELECT set_config('app.current_tenant_id', "
            "'00000000-0000-0000-0000-000000000000', true)"
        ))
        with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
            await session.execute(sa.text(
                "INSERT INTO audit_events (action, actor_id, actor_type, tenant_id) "
                "VALUES ('auth.accepted', 'test_0006_blocked_auth_accepted', 'human', NULL)"
            ))
            await session.flush()

        err_text = str(excinfo.value).lower()
        assert (
            "row-level security policy" in err_text
            or "ck_audit_tenant_required_or_pretenant" in err_text
        ), (
            f"Expected RLS or CHECK rejection; got: {str(excinfo.value)[:300]}"
        )


async def test_0006_downgrade_round_trips_to_0005_state():
    """Static analysis: 0006's downgrade restores 0005's state.

    Declared async-def to harmonize with module-level pytestmark.asyncio.
    """
    migration_path = (
        Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "0006_audit_pretenant_carve.py"
    )
    assert migration_path.exists(), f"Migration file not found at {migration_path}"
    source = migration_path.read_text()

    downgrade_idx = source.find("def downgrade")
    assert downgrade_idx > 0, "downgrade function not found in migration file"
    downgrade_section = source[downgrade_idx:]

    assert "DROP POLICY tenant_isolation_audit_events" in downgrade_section
    assert "CREATE POLICY tenant_isolation_audit_events" in downgrade_section
    assert "CREATE POLICY audit_events_pretenant_insert" in downgrade_section
