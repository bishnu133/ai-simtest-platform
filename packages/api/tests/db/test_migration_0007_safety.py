"""Safety tests for Alembic migration 0007_audit_iso_split_case.

Per FH-Tier-1 plan v0.3.4 (post Slice-0-Neon empirical investigation).

0007 attempted a per-command policy split with CASE-based WITH CHECK
for the pretenant carve-out. Local PG accepted the policy DDL; Neon
empirical verification (PB SL07.5) showed the CASE evaluation still
fails under app_user when tenant_id is NULL. 0008 supersedes the
pretenant carve-out with a SECURITY DEFINER function.

What 0007 still contributes (preserved by 0008):

  - The per-command policy split shape (audit_events_select FOR SELECT,
    audit_events_insert FOR INSERT) — 0008 keeps this shape, just
    replaces the CASE WITH CHECK with strict tenant-scoping.

  - audit_events_no_update FOR UPDATE RESTRICTIVE (immutability)
  - audit_events_no_delete FOR DELETE RESTRICTIVE (append-only)
  - FORCE ROW LEVEL SECURITY remains ON

The carve-out-via-CASE test is @pytest.mark.skip — that policy shape
is gone after 0008. Equivalent post-0008 assertion lives in
test_migration_0008_safety.py.

Convention: matches tests/db/test_rls_enforcement.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


async def test_0007_audit_events_per_command_policy_split():
    """The four expected per-command policies on audit_events exist.

    Survives 0008 unchanged — 0008 only changes the WITH CHECK body of
    audit_events_insert, not its existence or its command scope.
    """
    async with raw_admin_session() as session:
        result = await session.execute(sa.text(
            "SELECT polname, polpermissive, polcmd::text AS polcmd "
            "FROM pg_policy "
            "WHERE polrelid = 'audit_events'::regclass "
            "ORDER BY polname"
        ))
        rows = {r.polname: (r.polpermissive, r.polcmd) for r in result.all()}

        assert "audit_events_select" in rows, (
            f"Expected audit_events_select; got: {list(rows.keys())}"
        )
        assert rows["audit_events_select"] == (True, "r"), (
            f"Expected (permissive=True, cmd='r'); got {rows['audit_events_select']}"
        )

        assert "audit_events_insert" in rows
        assert rows["audit_events_insert"] == (True, "a")

        assert "audit_events_no_update" in rows
        assert rows["audit_events_no_update"] == (False, "w")

        assert "audit_events_no_delete" in rows
        assert rows["audit_events_no_delete"] == (False, "d")


async def test_0007_force_row_level_security_persisted():
    """audit_events still has rls=enabled and force=on after 0007 (and 0008).
    """
    async with raw_admin_session() as session:
        result = await session.execute(sa.text(
            "SELECT relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE relname = 'audit_events'"
        ))
        row = result.one()
        assert row.relrowsecurity is True, "rls should remain enabled"
        assert row.relforcerowsecurity is True, "FORCE rls should remain on"


@pytest.mark.skip(
    reason="0008 supersedes 0007's CASE-based audit_events_insert WITH CHECK with "
    "strict tenant-scoping + SECURITY DEFINER function (audit_pretenant_insert). "
    "PostgreSQL 16 rejects CASE-based WITH CHECK expressions involving row "
    "columns under app_user when NULL is involved (PB SL07.5 — Future-X). "
    "Equivalent post-0008 assertion: test_migration_0008_safety.py::"
    "test_0008_audit_events_insert_policy_restored_to_strict and ::"
    "test_0008_audit_pretenant_insert_function_exists_with_grant."
)
async def test_0007_audit_events_insert_with_case_pretenant_carve_out():
    """Original 0007-era assertion. Function preserved per sacred-test discipline;
    superseded by 0008."""
    pass


async def test_0007_downgrade_round_trips_to_0006_state():
    """Static analysis: 0007's downgrade restores 0006's state.

    Declared async-def to harmonize with module-level pytestmark.asyncio.
    """
    migration_path = (
        Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "0007_audit_iso_split_case.py"
    )
    assert migration_path.exists(), f"Migration file not found at {migration_path}"
    source = migration_path.read_text()

    downgrade_idx = source.find("def downgrade")
    assert downgrade_idx > 0, "downgrade function not found in migration file"
    downgrade_section = source[downgrade_idx:]

    # 0007's downgrade drops the split policies and restores the 0006-era
    # widened tenant_isolation_audit_events PERMISSIVE policy.
    assert "DROP POLICY audit_events_select" in downgrade_section
    assert "DROP POLICY audit_events_insert" in downgrade_section
    assert "CREATE POLICY tenant_isolation_audit_events" in downgrade_section
