"""Stage B bootstrap helper tests (Turn 2.7 Drift 3, plan v0.2.1 §4.3).

Three tests:
  1. ``test_build_bootstrap_sql_uses_actual_tenants_columns`` — pure;
     given mock columns ``[id, name, slug, clerk_org_id, plan_id, ...]``,
     the emitted SQL references those names AND does NOT reference the
     legacy ``external_org_id`` column from v0.3 runbook.
  2. ``test_build_bootstrap_sql_workspace_insert_is_rls_aware`` — pure;
     emitted SQL contains ``BEGIN``, ``SET LOCAL app.current_tenant_id``,
     and ``COMMIT`` for the workspaces INSERT block.
  3. ``test_introspect_and_emit_against_local_migrated_db`` — integration;
     uses the local ``migrated_db`` fixture (NOT Neon credentials),
     introspects the live schema, builds + executes the SQL, asserts
     one tenant row + one workspace row exist after the bootstrap.

Tests 1 and 2 are pure — no DB connection, no env var, no credentials.
Test 3 uses the local Postgres fixture from tests/db/conftest.py.
This satisfies plan v0.2.1 must-fix #7 ("tests must not require real
Neon credentials").

Plan reference: turn_2_7_plan_v0_2_1.md §2.7 + §4.3
"""
from __future__ import annotations

import sqlalchemy as sa

from scripts.stage_b_bootstrap import (
    ColumnInfo,
    _introspect_columns_async,
    build_bootstrap_sql,
)


# ---------------------------------------------------------------------------
# Mock column fixtures (used by pure tests 1 and 2)
# ---------------------------------------------------------------------------


# These mirror the actual `tenants` schema as of migration 0001 + 0002 + 0003.
# If a future migration changes them, this fixture should be updated to match
# — but ideally test 3 (the integration test) catches the drift first.
_MOCK_TENANT_COLS = [
    ColumnInfo("id", is_nullable=False, has_default=True),  # gen_random_uuid()
    ColumnInfo("name", is_nullable=False, has_default=False),
    ColumnInfo("slug", is_nullable=False, has_default=False),
    ColumnInfo("clerk_org_id", is_nullable=True, has_default=False),
    ColumnInfo("plan_id", is_nullable=False, has_default=True),
    ColumnInfo("settings", is_nullable=False, has_default=True),
    ColumnInfo("created_at", is_nullable=False, has_default=True),
    ColumnInfo("updated_at", is_nullable=False, has_default=True),
]

_MOCK_WORKSPACE_COLS = [
    ColumnInfo("id", is_nullable=False, has_default=True),
    ColumnInfo("tenant_id", is_nullable=False, has_default=False),
    ColumnInfo("name", is_nullable=False, has_default=False),
    ColumnInfo("is_default", is_nullable=False, has_default=True),
    ColumnInfo("created_at", is_nullable=False, has_default=True),
    ColumnInfo("updated_at", is_nullable=False, has_default=True),
]


def _build_smoke_sql() -> str:
    """Build the SQL used by tests 1 + 2. Same parameters as the CLI's
    main() so the assertions match what an operator would actually run.
    """
    return build_bootstrap_sql(
        tenant_columns=_MOCK_TENANT_COLS,
        workspace_columns=_MOCK_WORKSPACE_COLS,
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        tenant_name="Stage B Smoke Tenant",
        tenant_slug="stage-b-smoke-tenant",
        clerk_org_id="stage-b-smoke-org",
    )


# ---------------------------------------------------------------------------
# Test 1 — pure; emitted tenants INSERT uses actual columns
# ---------------------------------------------------------------------------


def test_build_bootstrap_sql_uses_actual_tenants_columns() -> None:
    """Emitted SQL must reference the columns that exist in the live
    schema (``id``, ``name``, ``slug``, ``clerk_org_id``) and MUST NOT
    reference the legacy ``external_org_id`` column from v0.3 runbook.

    This is the regression guard for T2.7-D-Stage-B-2: the v0.3 runbook
    had ``INSERT INTO tenants (id, external_org_id, name, created_at)``
    which fails on the live schema with
    ``ERROR: column "external_org_id" of relation "tenants" does not exist``.
    """
    sql = _build_smoke_sql()

    # Must contain the actual schema columns we want to set.
    assert "INSERT INTO tenants" in sql
    for actual_col in ("id", "name", "slug", "clerk_org_id"):
        assert actual_col in sql, (
            f"Expected column {actual_col!r} to appear in emitted SQL "
            f"(it's part of the live tenants schema). SQL:\n{sql}"
        )

    # Must NOT reference the legacy column. This is the precise drift
    # the v0.3 runbook had.
    assert "external_org_id" not in sql, (
        "Emitted SQL references the legacy `external_org_id` column "
        "which does not exist in the live schema (Stage B drift "
        "T2.7-D-Stage-B-2). Use clerk_org_id instead."
    )

    # The seed values must round-trip into the SQL as-is.
    assert "Stage B Smoke Tenant" in sql
    assert "stage-b-smoke-tenant" in sql
    assert "stage-b-smoke-org" in sql


# ---------------------------------------------------------------------------
# Test 2 — pure; workspace INSERT is RLS-aware
# ---------------------------------------------------------------------------


def test_build_bootstrap_sql_workspace_insert_is_rls_aware() -> None:
    """The workspaces INSERT must be wrapped in
    ``BEGIN; SET LOCAL app.current_tenant_id = '...'; INSERT ...; COMMIT;``
    because workspaces has FORCE ROW LEVEL SECURITY in 0001 (the table
    owner cannot bypass RLS without setting the tenant context).

    This is the regression guard for the second symptom of
    T2.7-D-Stage-B-2: the v0.3 runbook's plain workspaces INSERT
    silently failed (returned 0 rows visible) under FORCE RLS.
    """
    sql = _build_smoke_sql()

    # The wrapper must be present.
    assert "BEGIN;" in sql, "Emitted SQL is missing the BEGIN wrapper"
    assert "SET LOCAL app.current_tenant_id" in sql, (
        "Emitted SQL is missing the `SET LOCAL app.current_tenant_id` "
        "directive — workspaces INSERT will return 0 rows under FORCE RLS."
    )
    assert "COMMIT;" in sql, "Emitted SQL is missing the COMMIT wrapper"

    # The tenant id in the SET LOCAL must match the tenant being inserted —
    # otherwise the INSERT would scope to a different tenant context.
    expected_tid = "11111111-1111-1111-1111-111111111111"
    assert (
        f"SET LOCAL app.current_tenant_id = '{expected_tid}'" in sql
    ), (
        f"SET LOCAL must use tenant_id={expected_tid!r}. The emitted SQL "
        f"either uses a different value or has different quoting."
    )

    # Ordering check — the BEGIN must come BEFORE the workspaces INSERT,
    # and the INSERT must come before COMMIT. If those go out of order,
    # RLS won't apply to the INSERT.
    begin_idx = sql.index("BEGIN;")
    set_local_idx = sql.index("SET LOCAL app.current_tenant_id")
    workspaces_insert_idx = sql.index("INSERT INTO workspaces")
    commit_idx = sql.index("COMMIT;")
    assert begin_idx < set_local_idx < workspaces_insert_idx < commit_idx, (
        f"RLS wrapping ordering wrong: BEGIN@{begin_idx}, "
        f"SET LOCAL@{set_local_idx}, INSERT@{workspaces_insert_idx}, "
        f"COMMIT@{commit_idx}. Required: BEGIN < SET LOCAL < INSERT < COMMIT."
    )


# ---------------------------------------------------------------------------
# Test 3 — integration; introspect + emit + execute against local PG
# ---------------------------------------------------------------------------


async def test_introspect_and_emit_against_local_migrated_db(
    clean_db: str,
) -> None:
    """End-to-end: introspect the live ``tenants`` + ``workspaces``
    schema from the local migrated_db fixture, build SQL, execute it,
    confirm one tenant row + one workspace row exist after.

    This test uses the local PG fixture that all other DB tests use —
    NOT Neon credentials. Plan v0.2.1 must-fix #7.

    What this catches that tests 1 + 2 don't:
      * Real schema drift — if a column was added/dropped/renamed in a
        new migration, the mock columns in tests 1+2 are stale; this
        test catches the drift by introspecting the actual live schema.
      * asyncpg quoting/parameterization regressions in the emitted SQL.
      * Type coercion issues (e.g., the boolean ``'true'`` literal must
        be accepted by the is_default column).
    """
    # Step 1 — introspect the actual schema.
    # Use the async core directly because we're already inside a
    # pytest-asyncio event loop. Calling the sync wrapper here would
    # raise "asyncio.run() cannot be called from a running event loop".
    # See helper module's introspect_columns docstring for the rule.
    tenant_cols = await _introspect_columns_async(clean_db, "tenants")
    workspace_cols = await _introspect_columns_async(clean_db, "workspaces")

    assert len(tenant_cols) > 0, "introspect_columns returned no tenants columns"
    assert len(workspace_cols) > 0, "introspect_columns returned no workspaces columns"

    # Discovery sanity — the live schema must NOT have external_org_id
    # (regression guard against schema reverting to v0.3 wording).
    tenant_col_names = {c.name for c in tenant_cols}
    assert "external_org_id" not in tenant_col_names, (
        "Live schema has `external_org_id` column — that's the legacy "
        "name from v0.3 runbook. Live schema should have `clerk_org_id`."
    )
    assert "clerk_org_id" in tenant_col_names, (
        "Live schema is missing `clerk_org_id` — was migration 0001 "
        "applied?"
    )

    # Step 2 — build SQL using the real introspected columns.
    sql = build_bootstrap_sql(
        tenant_columns=tenant_cols,
        workspace_columns=workspace_cols,
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        tenant_name="Stage B Smoke Tenant",
        tenant_slug="stage-b-smoke-tenant",
        clerk_org_id="stage-b-smoke-org",
    )

    # Step 3 — execute the emitted SQL and verify the rows landed.
    # Use the same admin session pattern as other DB tests: raw_admin_session
    # bypasses RLS for the verification queries (the workspaces INSERT in
    # the SQL has its own SET LOCAL wrapper).
    from src.db.session import raw_admin_session

    async with raw_admin_session() as session:
        # Execute the multi-statement SQL by splitting on the BEGIN/COMMIT
        # boundary. asyncpg / SQLAlchemy can't run multi-statement SQL with
        # transaction control statements in a single execute() — they must
        # be split into separate statements. This is true of any tool;
        # the CLI variant uses psql which does its own splitting.
        for stmt in _split_sql_statements(sql):
            stripped = stmt.strip()
            if stripped:
                await session.execute(sa.text(stripped))
        await session.commit()

    # Step 4 — verify rows.
    async with raw_admin_session() as session:
        tenant_count = (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM tenants "
                    "WHERE id = '11111111-1111-1111-1111-111111111111'"
                )
            )
        ).scalar_one()
        # workspaces has FORCE RLS — admin still bypasses with no GUC,
        # but to be safe wrap in SET LOCAL like the runbook recommends.
        await session.execute(
            sa.text(
                "SET LOCAL app.current_tenant_id = "
                "'11111111-1111-1111-1111-111111111111'"
            )
        )
        workspace_count = (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM workspaces "
                    "WHERE id = '22222222-2222-2222-2222-222222222222'"
                )
            )
        ).scalar_one()

    assert tenant_count == 1, (
        f"Expected 1 tenant row after bootstrap, got {tenant_count}. "
        f"Emitted SQL did not result in a tenant insert."
    )
    assert workspace_count == 1, (
        f"Expected 1 workspace row after bootstrap, got {workspace_count}. "
        f"Either the RLS wrapper didn't apply, or the workspace INSERT "
        f"silently failed."
    )


# ---------------------------------------------------------------------------
# Helper: split multi-statement SQL on top-level statement boundaries
# ---------------------------------------------------------------------------


def _split_sql_statements(sql: str) -> list[str]:
    """Naive splitter — splits on ``;`` followed by a newline.

    Adequate for our generated SQL which has no embedded semicolons in
    string literals. NOT a general-purpose SQL splitter — for that, use
    a real parser.
    """
    out: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            # Skip comment lines so we don't split inside a comment.
            continue
        buf.append(line)
        if stripped.endswith(";"):
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out
