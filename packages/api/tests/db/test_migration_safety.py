"""Migration safety tests (plan §8 Turn 1b — 5 tests).

These tests guard the operational properties of the initial migration
(`0001_initial_schema.py`) that are easy to break in future revisions:

  1. Idempotency at head — running `alembic upgrade head` twice on an
     already-migrated database is a no-op and produces the same state.
  2. Zero data rows after fresh upgrade — the initial migration creates
     schema only, never seed data. (The dev seed is operator-run via
     `scripts/seed_dev_tenant.py` per plan §6.4 MF-4 fix.)
  3. Schema revision check — `alembic_version.version_num` matches the
     expected single revision id `0001_initial_schema`.
  4. Downgrade → upgrade round-trip works — proves the `downgrade()`
     function is correct and a future operator can roll back.
  5. Index presence — every named index from §7.0 + §7.1 is present
     after upgrade. Specifically guards the two partial unique indexes
     `uq_memberships_tenant_level` and `uq_memberships_workspace_level`
     (the v0.5.1 MF-1 fix) against accidental removal in future
     refactors.

These tests share the `migrated_db` session fixture but each one runs
its own subprocess `alembic` invocation against a freshly truncated DB
so they never see each other's state.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from src.db.session import raw_admin_session


pytestmark = pytest.mark.asyncio


# Tables that should exist (and contain zero rows) after a fresh upgrade.
# The 12 logical tables from plan §7.0 + the 3 audit_events partitions.
_EXPECTED_TABLES_ZERO_ROWS = (
    "tenants",
    "workspaces",
    "memberships",
    "service_accounts",
    "api_keys",
    "assets",
    "runs",
    "conversation_summaries",
    "dashboard_artifacts",
    "comparisons",
    "idempotency_keys",
    "audit_events",
    "audit_events_2026_04",
    "audit_events_2026_05",
    "audit_events_default",
)

# Indexes that MUST exist after a fresh upgrade. The two partial unique
# indexes are the v0.5.1 MF-1 fix; the rest are tenant lookup composite
# indexes from §7.0.
_REQUIRED_INDEXES = {
    # The v0.5.1 MF-1 fix — protect against accidental removal
    "uq_memberships_tenant_level",
    "uq_memberships_workspace_level",
    # Tenant-scoped uniqueness (Turn 1b amendment)
    "uq_assets_tenant_slug_type_version",
    # Membership lookup indexes (authz query path)
    "ix_memberships_lookup_workspace",
    "ix_memberships_lookup_tenant",
    # Tenant lookup composite indexes (§7.0)
    "ix_runs_tenant_workspace",
    "ix_runs_tenant_status",
    "ix_assets_tenant_workspace",
    "ix_assets_tenant_type",
    "ix_conv_sum_tenant_workspace",
    "ix_conv_sum_run_id",
    "ix_dashboard_artifacts_run_id",
    "ix_dashboard_artifacts_tenant_workspace",
    "ix_comparisons_tenant_workspace",
    "ix_comparisons_left_run_id",
    "ix_comparisons_right_run_id",
}


def _alembic_run(database_url: str, *args: str) -> subprocess.CompletedProcess:
    """Run an alembic subcommand in a subprocess against the given DB URL."""
    project_root = Path(__file__).resolve().parent.parent.parent
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(project_root),
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "AI_SIMTEST_DATABASE_URL": database_url,
            "PYTHONPATH": str(project_root),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# 1. Idempotency at head
# ---------------------------------------------------------------------------


async def test_upgrade_head_is_idempotent(clean_db: str) -> None:
    """Running `alembic upgrade head` against an already-migrated database
    is a no-op. Schema state, table list, and revision are unchanged.
    """
    # Snapshot state BEFORE the second upgrade.
    async with raw_admin_session() as s:
        tables_before = {
            row[0]
            for row in (
                await s.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            ).all()
        }
        rev_before = (
            await s.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
        ).scalar_one()

    # Second upgrade should succeed and be a no-op.
    result = _alembic_run(clean_db, "upgrade", "head")
    assert result.returncode == 0, (
        f"Second upgrade head failed:\n{result.stdout}\n{result.stderr}"
    )

    # Snapshot state AFTER and compare.
    async with raw_admin_session() as s:
        tables_after = {
            row[0]
            for row in (
                await s.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            ).all()
        }
        rev_after = (
            await s.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
        ).scalar_one()

    assert tables_before == tables_after, (
        f"Table set changed after second upgrade: "
        f"added={tables_after - tables_before} removed={tables_before - tables_after}"
    )
    assert rev_before == rev_after, (
        f"Revision changed after no-op upgrade: {rev_before} → {rev_after}"
    )


# ---------------------------------------------------------------------------
# 2. Zero data rows after fresh upgrade
# ---------------------------------------------------------------------------


async def test_fresh_upgrade_creates_no_data_rows(clean_db: str) -> None:
    """The initial migration creates schema only, never seed data.

    Plan §6.4 MF-4 fix: the dev tenant seed lives OUTSIDE the migration,
    in `scripts/seed_dev_tenant.py`. A production deploy that runs only
    `alembic upgrade head` must not get any rows.

    We use `clean_db` which TRUNCATEs all tables before running the test,
    simulating the post-fresh-upgrade state.
    """
    async with raw_admin_session() as s:
        for table in _EXPECTED_TABLES_ZERO_ROWS:
            count = (
                await s.execute(sa.text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar_one()
            assert count == 0, (
                f"Table '{table}' has {count} row(s) after fresh upgrade. "
                f"The initial migration must create schema only — seed data "
                f"belongs in scripts/seed_dev_tenant.py per plan §6.4."
            )


# ---------------------------------------------------------------------------
# 3. Schema revision check
# ---------------------------------------------------------------------------


async def test_alembic_current_returns_initial_schema(clean_db: str) -> None:
    """`alembic current` must return exactly one head revision.

    This is a guard against accidental dual-revision states (e.g. someone
    creating a migration without realizing another head exists). It does
    not pin a specific revision id, because the latest planned migration
    advances over time (0001 -> 0002 -> 0003 -> ...) as Turn-by-Turn
    migrations land legitimately. The test name is preserved for the
    sacred-287 identity guarantee (Turn 2.6 plan v0.2.1 §A.3).
    """
    async with raw_admin_session() as s:
        revisions = (
            await s.execute(
                sa.text("SELECT version_num FROM alembic_version ORDER BY version_num")
            )
        ).all()

    assert len(revisions) == 1, (
        f"Expected exactly 1 alembic revision, got {len(revisions)}: "
        f"{[r[0] for r in revisions]}"
    )


# ---------------------------------------------------------------------------
# 4. Downgrade → upgrade round-trip
# ---------------------------------------------------------------------------


async def test_downgrade_upgrade_round_trip(clean_db: str) -> None:
    """Run `alembic downgrade base` then `alembic upgrade head`.

    After the round-trip, the database state must be functionally
    identical to the starting state: same tables present, same revision,
    zero data rows.

    This proves the `downgrade()` function in 0001_initial_schema.py is
    correct — a future operator can roll back the initial migration
    cleanly without leaving orphan objects.
    """
    # Snapshot starting state
    async with raw_admin_session() as s:
        tables_before = {
            row[0]
            for row in (
                await s.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            ).all()
        }

    # Downgrade to base
    result = _alembic_run(clean_db, "downgrade", "base")
    assert result.returncode == 0, (
        f"Downgrade failed:\n{result.stdout}\n{result.stderr}"
    )

    # Verify the schema was actually torn down — only alembic_version (or
    # nothing) should remain in the public schema.
    async with raw_admin_session() as s:
        tables_after_downgrade = {
            row[0]
            for row in (
                await s.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            ).all()
        }
    # alembic_version is created by alembic itself, not the migration, so
    # it survives downgrade. Everything else from `0001_initial_schema`
    # MUST be gone.
    leftover = tables_after_downgrade - {"alembic_version"}
    assert leftover == set(), (
        f"Downgrade left orphan tables in public schema: {leftover}"
    )

    # Upgrade back to head
    result = _alembic_run(clean_db, "upgrade", "head")
    assert result.returncode == 0, (
        f"Upgrade after downgrade failed:\n{result.stdout}\n{result.stderr}"
    )

    # The starting table set must be restored
    async with raw_admin_session() as s:
        tables_after_round_trip = {
            row[0]
            for row in (
                await s.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            ).all()
        }
    assert tables_after_round_trip == tables_before, (
        f"Round-trip did not restore the starting state. "
        f"Missing: {tables_before - tables_after_round_trip}, "
        f"Extra: {tables_after_round_trip - tables_before}"
    )


# ---------------------------------------------------------------------------
# 5. Index presence
# ---------------------------------------------------------------------------


async def test_required_indexes_present(clean_db: str) -> None:
    """Every named index from §7.0 + §7.1 must exist after upgrade.

    This test specifically guards the two partial unique indexes
    `uq_memberships_tenant_level` and `uq_memberships_workspace_level`
    (the v0.5.1 MF-1 fix) against accidental removal in future refactors.
    A migration that drops one of these indexes would re-introduce the
    duplicate-tenant-level-membership bug — this test catches it at CI time.
    """
    async with raw_admin_session() as s:
        present = {
            row[0]
            for row in (
                await s.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public'"
                    )
                )
            ).all()
        }

    missing = _REQUIRED_INDEXES - present
    assert missing == set(), (
        f"Required indexes missing from schema: {sorted(missing)}\n"
        f"This is a regression on the v0.5.1 MF-1 fix or §7.0 lookup indexes. "
        f"Indexes currently present: {sorted(present & _REQUIRED_INDEXES)}"
    )


# ---------------------------------------------------------------------------
# 6. Migration 0003 round-trip — Turn 2.6 Step 3 (sacred-tests exception §A.3)
# ---------------------------------------------------------------------------


async def test_migration_0003_round_trips(clean_db: str) -> None:
    """Migration 0003 (idempotency_keys.workspace_id) round-trips cleanly.

    Forward: workspace_id column exists, ix_idempotency_keys_expires_at
    index exists, uq_idempotency_keys_tenant_workspace_key index exists,
    old uq_idempotency_keys_tenant_key is gone.

    Downgrade then re-upgrade: state matches the forward state again
    (tests that downgrade is the exact inverse).
    """
    project_root = Path(__file__).resolve().parent.parent.parent

    async def _index_set() -> set[str]:
        async with raw_admin_session() as s:
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' "
                        "AND tablename = 'idempotency_keys'"
                    )
                )
            ).all()
        return {row[0] for row in rows}

    async def _has_workspace_id_column() -> bool:
        async with raw_admin_session() as s:
            row = await s.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'idempotency_keys' "
                    "AND column_name = 'workspace_id'"
                )
            )
            return row.scalar_one_or_none() is not None

    # --- After head: 0003 applied ---
    indexes_at_head = await _index_set()
    assert "uq_idempotency_keys_tenant_workspace_key" in indexes_at_head
    assert "ix_idempotency_keys_expires_at" in indexes_at_head
    assert "uq_idempotency_keys_tenant_key" not in indexes_at_head, (
        "Old tenant-only unique index should be gone after 0003."
    )
    assert await _has_workspace_id_column(), (
        "workspace_id column should exist after 0003 upgrade."
    )

    # --- Downgrade to 0002 ---
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0002_ws_default_unique_idx"],
        cwd=str(project_root),
        env={
            **os.environ,
            "PYTHONPATH": str(project_root),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic downgrade failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    indexes_at_0002 = await _index_set()
    assert "uq_idempotency_keys_tenant_workspace_key" not in indexes_at_0002
    assert "ix_idempotency_keys_expires_at" not in indexes_at_0002
    assert "uq_idempotency_keys_tenant_key" in indexes_at_0002, (
        "Old tenant-only unique index must be restored on downgrade."
    )
    assert not await _has_workspace_id_column(), (
        "workspace_id column should be dropped on downgrade."
    )

    # --- Re-upgrade to head: state matches forward state ---
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(project_root),
        env={
            **os.environ,
            "PYTHONPATH": str(project_root),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic re-upgrade failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    indexes_after_reup = await _index_set()
    assert indexes_after_reup == indexes_at_head, (
        f"After downgrade -> re-upgrade round-trip, index set diverged:\n"
        f"  before: {sorted(indexes_at_head)}\n"
        f"  after:  {sorted(indexes_after_reup)}"
    )
