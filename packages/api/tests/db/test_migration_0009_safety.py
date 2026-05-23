"""Safety tests for Alembic migration 0009_widen_pretenant_actions.

Per FH-Tier-1 Slice 7.5 plan v0.2.1, §9.2.4 + §10.2 B.1.

This migration completes the 4-layer coordination required to add
'auth.tenant_state_invalid' to the pretenant audit allowlist:

  Layer 3: audit_pretenant_insert function body IF-block widened
  Layer 4: ck_audit_tenant_required_or_pretenant CHECK constraint widened

Tests verify:
  1. Downgrade source contains symmetric reversal SQL — static analysis.
  2. EXECUTE grant on the function survives CREATE OR REPLACE.
  3. CHECK constraint name preserved across DROP+ADD.

Fixture convention (tests/db/conftest.py rule):
  Only ``_configure_engine`` is autouse. Tests that touch DB state must
  explicitly request ``migrated_db`` (session-scoped) or ``clean_db``
  (function-scoped). Tests #2 and #3 here request ``migrated_db`` to
  ensure migrations have been applied to the spawned Postgres subprocess
  before they query introspection tables. Test #1 is static analysis
  only and needs no fixture.

The e2e contract gates (function accepts auth.tenant_state_invalid,
function rejects auth.accepted, CHECK accepts widened action+NULL,
CHECK rejects other actions+NULL) live in
tests/db/test_postgres_audit_event_repository.py per plan §9.2.3 and
land at FH-S7.5 step B.6. Splitting per the established layering:
this file exercises migration artifacts via raw SQL; the repo file
exercises PostgresAuditEventRepository method behavior.

Convention: matches tests/db/test_migration_0006_safety.py — except
0006_safety has a latent bug (it does not request migrated_db, so it
only passes when run as part of a larger tests/db/ suite where some
other test triggers migrated_db first). Filed as FH-S7.5 backlog F-8.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


async def test_0009_downgrade_round_trips_to_0008_state():
    """Static analysis: 0009's downgrade restores 0008's state.

    Verifies the migration source contains the symmetric reversal SQL that
    brings the audit_pretenant_insert function body and
    ck_audit_tenant_required_or_pretenant CHECK constraint back to the
    0008-era narrow allowlist (action = 'auth.rejected' only).

    Reverse-order discipline: downgrade reverses Op 2 (CHECK) first, then
    Op 1 (function), avoiding any transient state where the function
    accepts an action that the CHECK would later reject.

    Pattern matches tests/db/test_migration_0006_safety.py::
    test_0006_downgrade_round_trips_to_0005_state.
    """
    migration_path = (
        Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "0009_widen_pretenant_actions.py"
    )
    assert migration_path.exists(), f"Migration file not found at {migration_path}"
    source = migration_path.read_text()

    downgrade_idx = source.find("def downgrade")
    assert downgrade_idx > 0, "downgrade function not found in migration file"
    downgrade_section = source[downgrade_idx:]

    # Reverse Op 2: DROP widened CHECK, ADD narrow CHECK.
    assert "DROP CONSTRAINT ck_audit_tenant_required_or_pretenant" in downgrade_section
    assert "ADD CONSTRAINT ck_audit_tenant_required_or_pretenant" in downgrade_section
    # The narrow 0005/0008-era CHECK uses `action = 'auth.rejected'`,
    # NOT `IN (...)`.
    assert "OR action = 'auth.rejected'" in downgrade_section, (
        "Expected downgrade to restore 0005/0008-era narrow CHECK "
        "(action = 'auth.rejected'); not found"
    )
    # The downgrade section must NOT contain the widened IN clause anywhere
    # (neither in CHECK nor in function IF-block).
    assert (
        "IN ('auth.rejected', 'auth.tenant_state_invalid')"
        not in downgrade_section
    ), (
        "Downgrade contains widened IN expression; "
        "expected only narrow 'auth.rejected' form"
    )

    # Reverse Op 1: CREATE OR REPLACE FUNCTION audit_pretenant_insert with
    # the 0008-era narrow IF block.
    assert (
        "CREATE OR REPLACE FUNCTION audit_pretenant_insert" in downgrade_section
    )
    assert "p_action <> 'auth.rejected'" in downgrade_section, (
        "Expected downgrade to restore narrow function IF block "
        "(p_action <> 'auth.rejected'); not found"
    )


async def test_0009_function_grants_preserved_after_replace(migrated_db: str):
    """CREATE OR REPLACE FUNCTION preserves the EXECUTE grant to app_user.

    Per 0008's GRANT EXECUTE statement, app_user is the only role with
    permission to call audit_pretenant_insert. 0009 uses REPLACE (not
    DROP+CREATE) specifically to preserve this grant. Verifies via
    Postgres's has_function_privilege() introspection.

    Without this preservation, app_user would lose access and all
    pretenant audit emissions from the application would fail with a
    permission error.

    Requests the session-scoped ``migrated_db`` fixture explicitly per
    the tests/db/ convention: only ``_configure_engine`` is autouse, so
    tests that touch DB state must opt into migration application. See
    tests/db/conftest.py:332.
    """
    del migrated_db  # fixture used for side-effect only (migration application)
    async with raw_admin_session() as session:
        result = await session.execute(sa.text(
            "SELECT has_function_privilege("
            "  'app_user', "
            "  'audit_pretenant_insert(text, text, text, text, text, "
            "text, jsonb, inet, text)', "
            "  'EXECUTE'"
            ") AS has_execute"
        ))
        has_execute = result.scalar_one()
        assert has_execute is True, (
            "Expected app_user to retain EXECUTE on audit_pretenant_insert "
            "after 0009 CREATE OR REPLACE; got False (grant was lost)"
        )


async def test_0009_check_constraint_name_unchanged(migrated_db: str):
    """ck_audit_tenant_required_or_pretenant CHECK constraint exists on
    audit_events with its original name after 0009's DROP+ADD operation.

    Downstream tooling and prior migrations (0006/0007/0008 docstrings)
    reference this constraint by name. Preserving the name across 0009's
    necessary DROP+ADD (Postgres has no in-place ALTER for CHECK) is the
    contract.

    Requests the session-scoped ``migrated_db`` fixture explicitly per
    the tests/db/ convention (see test above).
    """
    del migrated_db  # fixture used for side-effect only (migration application)
    async with raw_admin_session() as session:
        result = await session.execute(sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'audit_events'::regclass "
            "  AND conname = 'ck_audit_tenant_required_or_pretenant' "
            "  AND contype = 'c'"  # 'c' = CHECK constraint
        ))
        rows = result.all()
        assert len(rows) == 1, (
            f"Expected exactly one ck_audit_tenant_required_or_pretenant "
            f"CHECK constraint on audit_events; found {len(rows)} matching "
            f"row(s)"
        )
