"""Migration 0004 dynamic test — non-superuser GRANT verification (Drift 1).

This test is GATED behind the ``TEST_PG_OWNERLIKE_URL`` environment
variable. If unset, the test SKIPS (with a clear message). If set, it
must point to an "owner-like" role connection: a role that is

  * NOT a Postgres superuser (so SET ROLE goes through the actual
    privilege check that Stage B revealed), AND
  * IS authorized to run Alembic migrations (i.e., can CREATE TABLE,
    CREATE ROLE, GRANT, ALTER, etc. — comparable to Neon's
    `neondb_owner`).

A read/write application user without DDL privileges WILL fail here in
ways that are NOT meaningful — that is not the bug we're testing. If
this test fails with "permission denied to create role" or similar,
the env var points at the wrong role.

Examples of valid URLs:
  * Neon: the project's owner connection string
        postgres+asyncpg://neondb_owner:...@.../neondb
  * Local: a non-superuser role created via:
        CREATE ROLE owner_test LOGIN PASSWORD '...'
            CREATEDB CREATEROLE NOSUPERUSER;
        ALTER DATABASE <test_db> OWNER TO owner_test;

Why this test matters:
    The local PG fixture used by the rest of the suite connects as a
    superuser, which silently bypasses the very check (SET ROLE
    permission) that Stage B revealed. This test is the *only* place
    that exercises migration 0004 on the same kind of role profile that
    Neon production uses. Without it, a future regression (e.g., a
    "fix" that drops the GRANT) would not be caught until the next
    Neon apply.

Plan reference: turn_2_7_plan_v0_2_1.md §2.3 + §4.1
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

OWNERLIKE_URL_ENV = "TEST_PG_OWNERLIKE_URL"


pytestmark = pytest.mark.skipif(
    OWNERLIKE_URL_ENV not in os.environ,
    reason=(
        f"set {OWNERLIKE_URL_ENV} to exercise migration 0004 against a "
        "non-superuser, migration-capable (owner-like) role. See module "
        "docstring for role requirements."
    ),
)


async def test_migration_0004_dynamic_owner_like_role_applies_chain_and_grants_membership() -> None:
    """End-to-end migration 0004 test on an owner-like (non-superuser)
    Postgres role.

    Procedure:
      1. Take the URL from $TEST_PG_OWNERLIKE_URL.
      2. Run `alembic upgrade head` against it as a subprocess.
      3. Connect as the same role and assert
         `pg_has_role(current_user, 'app_user', 'MEMBER')` is true.
      4. Confirm `SET LOCAL ROLE app_user` succeeds inside a transaction
         — this is the actual operational behavior the migration unblocks.

    All four steps are required; any failure means a future Neon/Stage C
    apply would still need manual `GRANT app_user TO ...` remediation.
    """
    owner_url = os.environ[OWNERLIKE_URL_ENV]

    # The URL may be in psql form; normalize to asyncpg for SQLAlchemy.
    asyncpg_url = owner_url
    if asyncpg_url.startswith("postgres://"):
        asyncpg_url = "postgresql+asyncpg://" + asyncpg_url[len("postgres://"):]
    elif asyncpg_url.startswith("postgresql://"):
        asyncpg_url = "postgresql+asyncpg://" + asyncpg_url[len("postgresql://"):]

    # Step 2 — apply the full chain. Run alembic in a subprocess so it
    # owns its own event loop (mirrors tests/db/conftest.py:migrated_db).
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(PROJECT_ROOT),
        env={
            **os.environ,
            "DATABASE_URL": asyncpg_url,
            "AI_SIMTEST_DATABASE_URL": asyncpg_url,
            "PYTHONPATH": str(PROJECT_ROOT),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"`alembic upgrade head` failed against owner-like role:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\n\n"
        f"If the failure is 'permission denied to create role' or similar, "
        f"the URL in {OWNERLIKE_URL_ENV} points at a role that is not "
        f"migration-capable. See module docstring."
    )

    # Step 3 — connect as the same role; assert membership.
    engine = create_async_engine(asyncpg_url, future=True)
    try:
        async with engine.connect() as conn:
            current_user = (
                await conn.execute(sa.text("SELECT current_user"))
            ).scalar_one()
            has_role = (
                await conn.execute(
                    sa.text(
                        "SELECT pg_has_role(current_user, 'app_user', 'MEMBER')"
                    )
                )
            ).scalar_one()
            assert has_role is True, (
                f"After applying chain through 0004 as owner-like role "
                f"({current_user!r}), pg_has_role still reports no app_user "
                f"membership. Migration 0004 did NOT fix T2.7-D-Neon-1 at "
                f"the source — Neon/Stage C apply would still require "
                f"manual `GRANT app_user TO {current_user};` remediation."
            )

            # Step 4 — operational behavior: SET LOCAL ROLE app_user
            # must succeed inside a transaction. This is the precise
            # operation `tenant_scoped_session` performs at runtime.
            async with conn.begin():
                await conn.execute(sa.text("SET LOCAL ROLE app_user"))
                effective = (
                    await conn.execute(sa.text("SELECT current_user"))
                ).scalar_one()
                assert effective == "app_user", (
                    f"SET LOCAL ROLE app_user did not switch effective role; "
                    f"current_user reports {effective!r} after SET LOCAL. "
                    f"This is the runtime failure mode Stage B revealed."
                )
    finally:
        await engine.dispose()
