#!/usr/bin/env python
"""Idempotent dev tenant seed script (plan §6.4 — MF-4 fix).

Purpose: create a "__dev__" tenant + default workspace + owner membership
for the reserved `dev_actor_1` actor, so developers running the app
against a fresh local Postgres can immediately authenticate as the dev
user without going through Clerk.

This script lives OUTSIDE the Alembic migration on purpose (plan §6.4):
production migrations must create schema only, never fake data rows.
Operators run this script once after `alembic upgrade head` for local
development. Production never runs it.

Usage::

    # Local development, pointing at docker-compose Postgres:
    export DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/postgres
    python scripts/seed_dev_tenant.py

    # CI integration test:
    DATABASE_URL=$TEST_PG_URL python scripts/seed_dev_tenant.py

The script is idempotent: running it twice is safe. Every INSERT uses
`ON CONFLICT ... DO NOTHING` scoped to the appropriate unique index, so
the second run is a no-op.

What it creates:

  1. A `tenants` row with slug=`__dev__` and a deterministic UUID so
     tests and dev code can reference it directly.
  2. A `workspaces` row named "Default Workspace" flagged as is_default.
  3. TWO `memberships` rows for `dev_actor_1` (plan §6.5 dual-membership):
     - a tenant-level row (workspace_id = NULL, role = 'owner')
     - a workspace-level row (workspace_id = <default_ws>, role = 'owner')

  The dual-membership shape matches exactly what the real auth bootstrap
  (Turn 3) will create for real Clerk users, so dev and production code
  paths exercise the same authz resolution logic.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import sqlalchemy as sa


# Deterministic UUIDs for dev entities — stable across runs so tests can
# reference them directly without lookups.
DEV_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEV_WORKSPACE_ID = "00000000-0000-0000-0000-000000000002"
DEV_ACTOR_ID = "dev_actor_1"
DEV_TENANT_SLUG = "__dev__"


async def seed() -> None:
    """Run the idempotent seed against the DATABASE_URL env var."""
    # Make the src package importable whether the script is run from the
    # project root or the scripts/ directory.
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from src.db.session import raw_admin_session

    async with raw_admin_session() as session:
        # 1. Tenant row — idempotent via slug unique constraint
        await session.execute(
            sa.text(
                """
                INSERT INTO tenants (id, name, slug, plan_id)
                VALUES (:id, :name, :slug, 'free')
                ON CONFLICT (slug) DO NOTHING
                """
            ),
            {
                "id": DEV_TENANT_ID,
                "name": "Development Tenant",
                "slug": DEV_TENANT_SLUG,
            },
        )

        # 2. Default workspace — idempotent via an application-layer check.
        # workspaces has no natural unique key besides id, so we query first.
        existing_ws = (
            await session.execute(
                sa.text(
                    "SELECT id FROM workspaces WHERE id = :id"
                ),
                {"id": DEV_WORKSPACE_ID},
            )
        ).scalar_one_or_none()
        if existing_ws is None:
            await session.execute(
                sa.text(
                    """
                    INSERT INTO workspaces (id, tenant_id, name, is_default)
                    VALUES (:id, :tid, 'Default Workspace', true)
                    """
                ),
                {"id": DEV_WORKSPACE_ID, "tid": DEV_TENANT_ID},
            )

        # 3a. Tenant-level owner membership (workspace_id = NULL).
        # Idempotent via the §7.1 partial unique index uq_memberships_tenant_level.
        await session.execute(
            sa.text(
                """
                INSERT INTO memberships (tenant_id, user_id, workspace_id, role)
                VALUES (:tid, :uid, NULL, 'owner')
                ON CONFLICT (tenant_id, user_id) WHERE workspace_id IS NULL
                    DO NOTHING
                """
            ),
            {"tid": DEV_TENANT_ID, "uid": DEV_ACTOR_ID},
        )

        # 3b. Workspace-level owner membership.
        # Idempotent via the §7.1 partial unique index uq_memberships_workspace_level.
        await session.execute(
            sa.text(
                """
                INSERT INTO memberships (tenant_id, user_id, workspace_id, role)
                VALUES (:tid, :uid, :wid, 'owner')
                ON CONFLICT (tenant_id, user_id, workspace_id) WHERE workspace_id IS NOT NULL
                    DO NOTHING
                """
            ),
            {
                "tid": DEV_TENANT_ID,
                "uid": DEV_ACTOR_ID,
                "wid": DEV_WORKSPACE_ID,
            },
        )

        await session.commit()

    print("✓ Dev tenant seed complete")
    print(f"  tenant_id    = {DEV_TENANT_ID}")
    print(f"  workspace_id = {DEV_WORKSPACE_ID}")
    print(f"  actor_id     = {DEV_ACTOR_ID}")
    print(f"  role         = owner (tenant-level + workspace-level)")


def main() -> None:
    if not os.environ.get("DATABASE_URL") and not os.environ.get(
        "AI_SIMTEST_DATABASE_URL"
    ):
        print(
            "ERROR: DATABASE_URL is not set.\n"
            "Set it to a postgres+asyncpg:// URL before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
