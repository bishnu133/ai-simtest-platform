"""Safety tests for Alembic migration 0008_audit_pretenant_fn.

Per FH-Tier-1 plan v0.3.4.

0008 is the working path for pretenant audit_events inserts. It supersedes
0005/0006/0007's WITH CHECK-based approaches (all of which were rejected
by PostgreSQL 16's policy framework under app_user — see Future-X in the
plan amendment and the module docstring of 0008_audit_pretenant_fn.py).

Three artifacts under test:

  1. audit_events_insert RLS policy — restored to strict tenant-scoping
     (no pretenant carve-out in WITH CHECK).

  2. audit_pretenant_insert(...) SECURITY DEFINER function — accepts
     allowlisted pretenant inserts and bypasses RLS via its owner's
     BYPASSRLS privilege.

  3. EXECUTE grant on the function to app_user.

Convention: matches tests/db/test_rls_enforcement.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


async def test_0008_audit_events_insert_policy_restored_to_strict():
    """audit_events_insert WITH CHECK is strict tenant-scoping only.

    No CASE expression, no pretenant carve-out. Pretenant inserts must
    go through audit_pretenant_insert() instead.
    """
    async with raw_admin_session() as session:
        result = await session.execute(sa.text(
            "SELECT pg_get_expr(polwithcheck, polrelid) AS with_check "
            "FROM pg_policy "
            "WHERE polrelid = 'audit_events'::regclass "
            "  AND polname = 'audit_events_insert'"
        ))
        row = result.one_or_none()
        assert row is not None, "audit_events_insert policy not found"

        with_check = row.with_check.lower()
        assert "case" not in with_check, (
            f"Expected no CASE in WITH CHECK after 0008; got: {row.with_check!r}"
        )
        assert "tenant_id" in with_check
        assert "current_setting" in with_check
        assert "app.current_tenant_id" in with_check


async def test_0008_audit_pretenant_insert_function_exists_with_grant():
    """The audit_pretenant_insert function exists, is SECURITY DEFINER,
    and has EXECUTE granted to app_user.
    """
    async with raw_admin_session() as session:
        # Function exists with SECURITY DEFINER (prosecdef=true)
        result = await session.execute(sa.text(
            "SELECT prosecdef, pronargs "
            "FROM pg_proc "
            "WHERE proname = 'audit_pretenant_insert' "
            "  AND pronamespace = 'public'::regnamespace"
        ))
        row = result.one_or_none()
        assert row is not None, "audit_pretenant_insert function not found in public schema"
        assert row.prosecdef is True, "audit_pretenant_insert must be SECURITY DEFINER"
        assert row.pronargs == 9, (
            f"Expected 9 parameters (action, actor_id, actor_type, resource_type, "
            f"resource_id, correlation_id, details, ip_address, user_agent); got {row.pronargs}"
        )

        # app_user has EXECUTE grant
        grant_result = await session.execute(sa.text(
            "SELECT has_function_privilege("
            "  'app_user', "
            "  'public.audit_pretenant_insert(text, text, text, text, text, text, jsonb, inet, text)', "
            "  'EXECUTE'"
            ") AS can_execute"
        ))
        assert grant_result.scalar_one() is True, (
            "app_user should have EXECUTE on audit_pretenant_insert"
        )


async def test_0008_app_user_can_call_audit_pretenant_insert():
    """End-to-end: app_user invokes audit_pretenant_insert and the row persists
    with tenant_id=NULL.

    This is the cleaner equivalent of test_0006_app_user_can_insert_auth_rejected_with_sentinel_guc.
    Distinct test function retained (no rename across files) for unambiguous
    diagnostics if either path regresses.
    """
    async with raw_admin_session() as session:
        await session.execute(sa.text("SET LOCAL ROLE app_user"))

        result = await session.execute(
            sa.text(
                "SELECT audit_pretenant_insert("
                ":action, :actor_id, :actor_type, "
                ":resource_type, :resource_id, :correlation_id"
                ") AS new_id"
            ),
            {
                "action": "auth.rejected",
                "actor_id": "test_0008_e2e_app_user_call",
                "actor_type": "system",
                "resource_type": "auth",
                "resource_id": "session",
                "correlation_id": "test_0008_corr_id",
            },
        )
        new_id = result.scalar_one()
        assert new_id is not None

        # Verify the row was inserted; reset to admin to bypass RLS for the read.
        await session.execute(sa.text("RESET ROLE"))
        verify = await session.execute(
            sa.text(
                "SELECT action, tenant_id, actor_id, actor_type, "
                "       resource_type, resource_id, correlation_id "
                "FROM audit_events WHERE id = :id"
            ),
            {"id": new_id},
        )
        row = verify.one()
        assert row.action == "auth.rejected"
        assert row.tenant_id is None
        assert row.actor_id == "test_0008_e2e_app_user_call"
        assert row.actor_type == "system"
        assert row.resource_type == "auth"
        assert row.resource_id == "session"
        assert row.correlation_id == "test_0008_corr_id"


async def test_0008_audit_pretenant_insert_rejects_disallowed_action():
    """The function's action allowlist (currently just 'auth.rejected') is enforced.

    Calling with action='auth.accepted' raises check_violation. This is
    defense-in-depth: even though only app_user (via EXECUTE grant) can
    call the function, the allowlist prevents an authorized caller from
    abusing the pretenant path for non-pretenant actions.
    """
    async with raw_admin_session() as session:
        await session.execute(sa.text("SET LOCAL ROLE app_user"))

        with pytest.raises((InternalError, ProgrammingError, DBAPIError)) as excinfo:
            await session.execute(
                sa.text(
                    "SELECT audit_pretenant_insert("
                    ":action, :actor_id, :actor_type"
                    ")"
                ),
                {
                    "action": "auth.accepted",
                    "actor_id": "test_0008_should_be_blocked",
                    "actor_type": "human",
                },
            )

        err_text = str(excinfo.value).lower()
        assert (
            "not in pretenant allowlist" in err_text
            or "check_violation" in err_text
            or "auth.accepted" in err_text
        ), (
            f"Expected function-level allowlist rejection; got: {str(excinfo.value)[:300]}"
        )


async def test_0008_downgrade_round_trips_to_0007_state():
    """Static analysis: 0008's downgrade drops the function and restores
    0007's CASE-based audit_events_insert.

    Declared async-def to harmonize with module-level pytestmark.asyncio.
    """
    migration_path = (
        Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "0008_audit_pretenant_fn.py"
    )
    assert migration_path.exists(), f"Migration file not found at {migration_path}"
    source = migration_path.read_text()

    downgrade_idx = source.find("def downgrade")
    assert downgrade_idx > 0, "downgrade function not found"
    downgrade_section = source[downgrade_idx:]

    assert "DROP FUNCTION IF EXISTS audit_pretenant_insert" in downgrade_section
    assert "DROP POLICY audit_events_insert" in downgrade_section
    assert "CREATE POLICY audit_events_insert" in downgrade_section
    assert "CASE" in downgrade_section, (
        "Downgrade must restore 0007's CASE-based WITH CHECK"
    )
    assert "WHEN tenant_id IS NULL" in downgrade_section
