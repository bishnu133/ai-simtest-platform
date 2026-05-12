"""Stage B bootstrap SQL generator (Turn 2.7 Drift 3, plan v0.2.1 §2.7).

Operator helper that introspects ``information_schema.columns`` for
``tenants`` and ``workspaces`` and emits RLS-aware bootstrap SQL for
the Stage B / Stage C smoke flow. Adapts to the *actual* live schema
rather than relying on a frozen-at-write-time column list (which is
exactly the failure mode that surfaced T2.7-D-Stage-B-2 during Stage B
on 2026-04-29).

Architecture (per plan v0.2.1 §2.7 must-fix #7):

    build_bootstrap_sql(...)   PURE function. No DB, no env vars, no
                               credentials. Takes column metadata +
                               values, returns a SQL string.
    introspect_columns(...)    DB-coupled. Connects, queries
                               information_schema, returns ColumnInfo[].
    main()                     CLI entrypoint. Reads DATABASE_URL,
                               composes the two, prints SQL to stdout.

The split is deliberate: it lets the unit tests verify SQL shape with
mock columns (no DB), while one integration test exercises the
introspection path against the local ``migrated_db`` fixture.

Output shape (one block):

    -- tenants INSERT (NO RLS wrapping — tenants is not RLS-forced)
    INSERT INTO tenants (...) VALUES (...) ON CONFLICT (id) DO NOTHING;

    -- workspaces INSERT (RLS-wrapped — workspaces has FORCE RLS, see
    --                    plan v0.2.1 §2.3 / Stage B drift T2.7-D-Stage-B-2)
    BEGIN;
    SET LOCAL app.current_tenant_id = '...';
    INSERT INTO workspaces (...) VALUES (...) ON CONFLICT (id) DO NOTHING;
    COMMIT;

Caller usage (operator):
    DATABASE_URL=postgresql://...@.../neondb \\
        python scripts/stage_b_bootstrap.py | psql "$PSQL_URL"

Caller usage (Python, for runbook scripts):
    from scripts.stage_b_bootstrap import (
        build_bootstrap_sql,
        introspect_columns,
    )
    tenant_cols   = introspect_columns(db_url, "tenants")
    ws_cols       = introspect_columns(db_url, "workspaces")
    sql = build_bootstrap_sql(
        tenant_columns=tenant_cols,
        workspace_columns=ws_cols,
        tenant_id="11111111-...",
        workspace_id="22222222-...",
        tenant_name="Stage B Smoke Tenant",
        tenant_slug="stage-b-smoke-tenant",
        clerk_org_id="stage-b-smoke-org",  # optional
    )
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Pure types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnInfo:
    """Minimal metadata about a schema column.

    Frozen because callers should not be able to mutate one column's
    metadata after it has been introspected — that would break the
    "what we emitted SQL for" invariant.
    """

    name: str
    is_nullable: bool
    has_default: bool


# ---------------------------------------------------------------------------
# Pure SQL builder
# ---------------------------------------------------------------------------


# Tables that have FORCE ROW LEVEL SECURITY (per migration 0001 + 0003).
# An INSERT/SELECT against any of these requires:
#     BEGIN; SET LOCAL app.current_tenant_id = '...'; <op>; COMMIT;
# even when the connecting role is the table owner.
RLS_FORCED_TABLES = frozenset(
    {
        "workspaces",
        "memberships",
        "runs",
        "assets",
        "conversation_summaries",
        "comparisons",
        "idempotency_keys",
        "dashboard_artifacts",
    }
)


def _emit_insert(
    table: str,
    columns: Iterable[ColumnInfo],
    values: dict[str, str],
    *,
    on_conflict_target: str = "id",
) -> str:
    """Emit a single ``INSERT INTO table (cols) VALUES (vals)
    ON CONFLICT DO NOTHING;`` statement.

    Discipline rules:

      * Only columns that exist in ``columns`` AND are present in
        ``values`` are emitted. This is the source of the helper's
        adapt-to-actual-schema behavior.
      * Required columns (NOT NULL with no default) MUST appear in
        ``values`` or the function raises ValueError. Fail-loud is
        better than emitting SQL that will fail at execution time
        with a less informative error.
      * Values are inlined as SQL literals, single-quoted and escaped
        with the standard SQL '' doubling. The helper is not used in
        contexts where SQL injection would be a concern (it's an
        operator tool that takes operator-supplied values), but the
        escaping protects against accidental quote characters in
        names like ``"O'Brien Co"``.
    """
    column_index = {c.name: c for c in columns}

    # Fail loud if a required column is missing from values.
    missing_required = [
        c.name
        for c in columns
        if not c.is_nullable and not c.has_default and c.name not in values
    ]
    if missing_required:
        raise ValueError(
            f"build_bootstrap_sql cannot emit INSERT for {table!r}: required "
            f"columns missing from values: {sorted(missing_required)}. "
            f"Add them to the values dict or supply server defaults in the "
            f"schema."
        )

    # Emit only the columns the caller explicitly wants to set AND that
    # actually exist in the live schema. This is the adapt-to-schema path.
    emit_cols = [c.name for c in columns if c.name in values]
    if not emit_cols:
        raise ValueError(
            f"build_bootstrap_sql: no values supplied for any column of "
            f"{table!r}. The values dict was empty or referenced columns "
            f"not present in the live schema. introspected columns: "
            f"{sorted(column_index)}"
        )

    # Inline literal values (single-quoted, '' escaping).
    quoted = [f"'{values[c].replace(chr(39), chr(39) * 2)}'" for c in emit_cols]
    cols_sql = ", ".join(emit_cols)
    vals_sql = ",\n    ".join(quoted)
    return (
        f"INSERT INTO {table} ({cols_sql}) VALUES (\n    "
        f"{vals_sql}\n) ON CONFLICT ({on_conflict_target}) DO NOTHING;"
    )


def build_bootstrap_sql(
    tenant_columns: list[ColumnInfo],
    workspace_columns: list[ColumnInfo],
    *,
    tenant_id: str,
    workspace_id: str,
    tenant_name: str,
    tenant_slug: str,
    clerk_org_id: str | None = None,
    workspace_name: str = "default",
    workspace_is_default: bool = True,
) -> str:
    """Pure function — emit RLS-aware bootstrap SQL for tenants + workspaces.

    Adapts to the actual live schema (as introspected and passed in via
    ``tenant_columns`` / ``workspace_columns``) rather than hard-coding
    a frozen column list. This is the helper's purpose: a future schema
    change that adds or drops a column on tenants or workspaces will
    not silently break the runbook.

    Args:
        tenant_columns: columns of the live ``tenants`` table.
        workspace_columns: columns of the live ``workspaces`` table.
        tenant_id: UUID string for the seed tenant.
        workspace_id: UUID string for the seed workspace.
        tenant_name: human-readable tenant name.
        tenant_slug: tenant slug (URL-safe identifier).
        clerk_org_id: optional Clerk org id; emitted only if non-None
            AND the column exists in the live schema. (Live schema as
            of 2026-04 has it; future schemas may not.)
        workspace_name: workspace name (defaults to "default").
        workspace_is_default: whether this is the tenant's default
            workspace (defaults to True — for smoke flows there's only
            ever one workspace per tenant, and it's the default one).

    Returns:
        A SQL string with two statements (or two blocks): the tenants
        INSERT, then the RLS-wrapped workspaces INSERT block.

    Raises:
        ValueError: if a required column on tenants or workspaces is
            not present in the supplied values.
    """
    # tenants — non-RLS, plain INSERT.
    tenant_values: dict[str, str] = {
        "id": tenant_id,
        "name": tenant_name,
        "slug": tenant_slug,
    }
    if clerk_org_id is not None:
        tenant_values["clerk_org_id"] = clerk_org_id

    tenants_sql = _emit_insert(
        "tenants",
        tenant_columns,
        tenant_values,
    )

    # workspaces — FORCE RLS, must be wrapped in BEGIN/SET LOCAL/COMMIT.
    workspace_values: dict[str, str] = {
        "id": workspace_id,
        "tenant_id": tenant_id,
        "name": workspace_name,
        "is_default": "true" if workspace_is_default else "false",
    }
    workspaces_insert = _emit_insert(
        "workspaces",
        workspace_columns,
        workspace_values,
    )

    # The RLS wrapper. Pre-Stage-B v0.3 runbook missed this and had to
    # be manually corrected mid-execution on 2026-04-29 (T2.7-D-Stage-B-2).
    workspaces_block = (
        "BEGIN;\n"
        f"SET LOCAL app.current_tenant_id = '{tenant_id}';\n"
        f"{workspaces_insert}\n"
        "COMMIT;"
    )

    return (
        "-- tenants INSERT — tenants is NOT RLS-forced, plain INSERT is fine.\n"
        f"{tenants_sql}\n"
        "\n"
        "-- workspaces INSERT — RLS-wrapped because workspaces has\n"
        "--   FORCE ROW LEVEL SECURITY (applies to the owning role too).\n"
        "--   See plan v0.2.1 §2.3 / Stage B drift T2.7-D-Stage-B-2.\n"
        f"{workspaces_block}"
    )


# ---------------------------------------------------------------------------
# DB-coupled introspection
# ---------------------------------------------------------------------------


def _normalize_asyncpg_url(database_url: str) -> str:
    """Normalize a DATABASE_URL to a plain ``postgresql://...`` form
    that asyncpg accepts.

    asyncpg does not accept the SQLAlchemy ``+asyncpg`` suffix, and
    ``postgres://`` is a legacy spelling that works but is normalized
    to ``postgresql://`` for consistency in the rest of the helper.
    """
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://"):]
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://"):]
    return database_url


async def _introspect_columns_async(
    database_url: str, table_name: str
) -> list[ColumnInfo]:
    """Async core of the introspection — open an asyncpg connection,
    query ``information_schema.columns``, return ``ColumnInfo[]``.

    Callers that already have an event loop (pytest-asyncio tests,
    other async code) should ``await`` this directly. Sync callers
    (the CLI, ad-hoc scripts) should call ``introspect_columns()``
    instead — that wrapper handles the event-loop bootstrap.

    Why the split exists (Turn 2.7 Drift 3 hotfix):
        The earlier single-function design called ``asyncio.run()``
        unconditionally, which raises ``RuntimeError: asyncio.run()
        cannot be called from a running event loop`` when invoked
        from inside an async test. Splitting into async-core +
        sync-wrapper is the standard fix and mirrors how
        ``alembic/env.py`` exposes async migrations to a sync caller.
    """
    # Lazy import — keeps pure-function tests fast and gives a clean
    # error message if asyncpg isn't installed.
    import asyncpg  # type: ignore[import-not-found]

    asyncpg_url = _normalize_asyncpg_url(database_url)

    conn = await asyncpg.connect(asyncpg_url)
    try:
        rows = await conn.fetch(
            """
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = $1
            ORDER BY ordinal_position
            """,
            table_name,
        )
        return [
            ColumnInfo(
                name=row["column_name"],
                is_nullable=(row["is_nullable"] == "YES"),
                has_default=(row["column_default"] is not None),
            )
            for row in rows
        ]
    finally:
        await conn.close()


def introspect_columns(database_url: str, table_name: str) -> list[ColumnInfo]:
    """Sync wrapper around :func:`_introspect_columns_async`. Connect
    to ``database_url``, return the column metadata for ``table_name``
    from ``information_schema.columns``.

    Uses ``asyncio.run`` to bootstrap an event loop — same pattern
    alembic/env.py uses to invoke async code from sync entrypoints.

    **Important:** if you are already inside a running event loop
    (e.g. inside an ``async def`` test or async application code),
    use :func:`_introspect_columns_async` directly with ``await``.
    Calling this sync wrapper from within a running loop raises
    ``RuntimeError: asyncio.run() cannot be called from a running
    event loop``.

    The DATABASE_URL may be in any of:
      * ``postgres://...`` (psql form)
      * ``postgresql://...`` (psql form)
      * ``postgresql+asyncpg://...`` (SQLAlchemy async form — stripped)

    Returns the columns in their ``ordinal_position`` order so the
    output SQL has a stable column order across runs.
    """
    import asyncio

    return asyncio.run(_introspect_columns_async(database_url, table_name))


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entrypoint.

    Reads DATABASE_URL from the environment, introspects tenants +
    workspaces columns, emits the bootstrap SQL to stdout. Non-zero
    exit on any error.

    The seed identifiers are the canonical Stage B values:
      tenant_id     = 11111111-1111-1111-1111-111111111111
      workspace_id  = 22222222-2222-2222-2222-222222222222
      tenant_slug   = stage-b-smoke-tenant
      clerk_org_id  = stage-b-smoke-org

    These match the v0.4 runbook §5.1 (and the corresponding cleanup
    in §5.5) so a future operator can pipe the output directly into
    psql without any further substitution.
    """
    database_url = os.environ.get("DATABASE_URL") or os.environ.get(
        "AI_SIMTEST_DATABASE_URL"
    )
    if not database_url:
        print(
            "ERROR: DATABASE_URL is not set. "
            "Export it before running this script (psql or asyncpg form).",
            file=sys.stderr,
        )
        return 2

    try:
        tenant_cols = introspect_columns(database_url, "tenants")
        workspace_cols = introspect_columns(database_url, "workspaces")
    except Exception as exc:  # noqa: BLE001 — operator-friendly error
        print(
            f"ERROR: introspection failed against the live schema: {exc}",
            file=sys.stderr,
        )
        return 3

    if not tenant_cols:
        print(
            "ERROR: introspection returned 0 columns for tenants. "
            "Has migration 0001 been applied to this database?",
            file=sys.stderr,
        )
        return 4
    if not workspace_cols:
        print(
            "ERROR: introspection returned 0 columns for workspaces. "
            "Has migration 0001 been applied to this database?",
            file=sys.stderr,
        )
        return 4

    try:
        sql = build_bootstrap_sql(
            tenant_columns=tenant_cols,
            workspace_columns=workspace_cols,
            tenant_id="11111111-1111-1111-1111-111111111111",
            workspace_id="22222222-2222-2222-2222-222222222222",
            tenant_name="Stage B Smoke Tenant",
            tenant_slug="stage-b-smoke-tenant",
            clerk_org_id="stage-b-smoke-org",
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5

    print(sql)
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    raise SystemExit(main())
