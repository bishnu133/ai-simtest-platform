"""Tenant-scoped async session factory (plan §5 — RLS wiring).

Every DB interaction in the control plane goes through this module. The
core primitive is `tenant_scoped_session(tenant_id)`, an async context
manager that:

  1. Acquires a fresh `AsyncSession` from the engine's session factory
  2. Opens a transaction
  3. Runs `SET LOCAL app.current_tenant_id = '<uuid>'` inside that
     transaction, making Postgres RLS policies see the tenant
  4. Yields the session for application code
  5. Commits on success, rolls back on exception
  6. Closes the session (returns connection to pool)

Because `SET LOCAL` is scoped to the transaction, the tenant setting is
automatically cleared when the transaction ends — there is no way for a
tenant setting to leak across requests on a pooled connection.

Fail-closed design: passing an empty, None, or whitespace-only tenant_id
raises `ValueError` **before** any DB work happens. This matches plan §5
"missing tenant → no DB access" rule. An empty `current_setting` in
Postgres would also make every RLS policy fail-closed at the DB layer,
but we enforce it at the app layer first so the error is clearly actionable.

Neon-specific note (plan §5): asyncpg is configured with
`statement_cache_size=0` because Neon's pooled mode does not support
prepared statements across pooled connections. This setting is harmless
in direct-connection mode and essential in pooled mode, so we always set it.

---

Turn 1 scope: this module provides the session primitive only.
Repositories (Turn 2), middleware (Turn 3), and app factory (Turn 4) all
consume it. It does not itself import or depend on any repository, router,
or middleware module — circular imports are avoided by construction.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


# ---------------------------------------------------------------------------
# Engine management
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    """Resolve the database URL from the environment.

    Accepts any of these env var names (checked in order):
      - DATABASE_URL              (standard)
      - AI_SIMTEST_DATABASE_URL   (app-specific)

    Raises `RuntimeError` if none are set, so a misconfigured deployment
    fails loudly at engine init, not on the first query.
    """
    url = os.environ.get("DATABASE_URL") or os.environ.get("AI_SIMTEST_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to a postgres+asyncpg:// URL "
            "(e.g. postgresql+asyncpg://user:pass@host:5432/db)."
        )
    # Helpful nudge: if the URL is a sync driver form, fail loudly.
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        raise RuntimeError(
            "DATABASE_URL must use the asyncpg driver form: "
            "postgresql+asyncpg://... — got: " + url.split("@")[0] + "@..."
        )
    return url


def get_engine() -> AsyncEngine:
    """Return the shared `AsyncEngine`, creating it on first access.

    Lazy construction means tests that set `DATABASE_URL` after import-time
    still get the correct engine. `reset_engine()` (below) is provided for
    tests that need to swap engines between test sessions.

    Test-mode pooling: if env var `AI_SIMTEST_DB_NULLPOOL=1` is set, uses
    `NullPool` (no connection pooling). This is required under pytest-asyncio
    because each test function gets a fresh event loop, and a pooled
    connection attached to a prior loop cannot be reused on the new one.
    Production always uses the real pool.
    """
    global _engine, _sessionmaker
    if _engine is None:
        use_nullpool = os.environ.get("AI_SIMTEST_DB_NULLPOOL") == "1"

        if use_nullpool:
            from sqlalchemy.pool import NullPool

            _engine = create_async_engine(
                _database_url(),
                connect_args={"statement_cache_size": 0},
                poolclass=NullPool,
                future=True,
            )
        else:
            _engine = create_async_engine(
                _database_url(),
                # Neon pooled mode: prepared statements don't survive across
                # pooled connections, so disable the statement cache entirely.
                # This is a no-op on direct connections.
                connect_args={"statement_cache_size": 0},
                # Conservative pool size; tune per environment at Turn 4.
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                future=True,
            )
        _sessionmaker = async_sessionmaker(
            bind=_engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the shared async session factory, initializing the engine if needed."""
    get_engine()
    assert _sessionmaker is not None  # get_engine() guarantees this
    return _sessionmaker


async def reset_engine() -> None:
    """Dispose the current engine. Used by tests between sessions.

    Safe to call when no engine exists.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def configure_engine_from_url(url: str) -> None:
    """Override the engine URL programmatically.

    Used by the test conftest to point at a container Postgres before any
    test imports `tenant_scoped_session`. After calling this, the next
    `get_engine()` call will build a fresh engine against `url`.

    This is intentionally synchronous so it can run during test collection.
    """
    global _engine, _sessionmaker
    # If an engine was already built against an old URL, drop it. The caller
    # is responsible for awaiting `reset_engine()` first if an async dispose
    # is required — in practice, tests call this BEFORE any engine exists.
    _engine = None
    _sessionmaker = None
    os.environ["DATABASE_URL"] = url


# ---------------------------------------------------------------------------
# Tenant-scoped session context manager (the §5 primitive)
# ---------------------------------------------------------------------------


def _validate_tenant_id(tenant_id: str) -> str:
    """Fail-closed validation of tenant_id before any DB work.

    Accepts: a non-empty, non-whitespace string. Returns the trimmed value.

    Rejects: None, empty string, whitespace-only string. Raises `ValueError`
    with a clear message — callers can map this to HTTP 401/500 as
    appropriate (middleware in Turn 3 handles this).

    We intentionally do NOT validate UUID format here. RLS policies compare
    to the session-local setting as a string cast, and the Postgres layer
    will reject malformed UUIDs cleanly. App-layer UUID validation would
    duplicate that check and add nothing.
    """
    if tenant_id is None:
        raise ValueError("tenant_id must not be None")
    if not isinstance(tenant_id, str):
        raise ValueError(f"tenant_id must be str, got {type(tenant_id).__name__}")
    trimmed = tenant_id.strip()
    if not trimmed:
        raise ValueError("tenant_id must not be empty or whitespace")
    return trimmed


@asynccontextmanager
async def tenant_scoped_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """Yield an `AsyncSession` with `app.current_tenant_id` set for the transaction.

    Usage::

        async with tenant_scoped_session(ctx.tenant_id) as session:
            result = await session.execute(text("SELECT * FROM runs"))
            # RLS policies automatically filter to ctx.tenant_id

    On clean exit, commits. On exception, rolls back and re-raises. On
    either path, closes the session.

    Two session-local settings are applied inside the transaction:

      1. ``SET LOCAL app.current_tenant_id = '<tenant_id>'`` — parameterized
         via SQLAlchemy bind params, never string-interpolated. This is
         what the RLS ``tenant_isolation_*`` policies compare against.

      2. ``SET LOCAL ROLE app_user`` — switches the session's effective
         role to the non-superuser ``app_user`` role for the duration of
         the transaction. This is CRITICAL for RLS to work, because
         Postgres superusers bypass row security entirely — even tables
         marked ``FORCE ROW LEVEL SECURITY``. By stepping down to
         ``app_user``, RLS policies apply normally. The role reverts
         automatically when the transaction ends, so pooled connections
         return to their original role for the next request.

    Both settings are applied with ``SET LOCAL`` so their scope is the
    current transaction only — there is no way for a tenant or role
    setting to leak across requests on a pooled connection.

    The ``app_user`` role is created by the initial Alembic migration
    (see ``alembic/versions/0001_initial_schema.py``). If that migration
    has not run against this database, ``SET LOCAL ROLE app_user`` will
    fail at the first transaction — which is the right behavior: the
    app should not run against an unmigrated database.
    """
    valid_tid = _validate_tenant_id(tenant_id)
    sessionmaker = get_sessionmaker()
    session = sessionmaker()
    try:
        # Begin a transaction. Order of SETs inside the transaction matters:
        #   1. SET LOCAL ROLE first, so the session drops out of superuser
        #      mode before it touches any tenant-scoped table.
        #   2. SET LOCAL app.current_tenant_id second, so RLS policies
        #      (which now apply under app_user) see the correct value.
        await session.begin()
        await session.execute(text("SET LOCAL ROLE app_user"))
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": valid_tid},
        )
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def raw_admin_session() -> AsyncIterator[AsyncSession]:
    """Yield an `AsyncSession` WITHOUT setting `app.current_tenant_id`.

    DO NOT use for normal request handling. This is reserved for:
      - Alembic migrations (already runs outside the app)
      - Bootstrap (plan §6.4) — tenant doesn't exist yet, so there's
        nothing to scope to. Bootstrap explicitly bypasses RLS by running
        as the `postgres` superuser or by setting the tenant *after* the
        initial INSERT in the same transaction.
      - Test fixtures that insert seed data before a test runs

    Any code that imports this outside of those three contexts is a bug.
    """
    sessionmaker = get_sessionmaker()
    session = sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


__all__ = [
    "get_engine",
    "get_sessionmaker",
    "reset_engine",
    "configure_engine_from_url",
    "tenant_scoped_session",
    "raw_admin_session",
]
