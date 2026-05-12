"""Migration 0004 — DO-block CURRENT_USER resolution test (Drift 1, plan v0.2.1 §2.1).

ONE test, with a deliberately long and specific name, that verifies the
PRIMARY invariant of migration 0004:

    Inside the PL/pgSQL DO $$ ... $$ block, the GRANT statement resolves
    CURRENT_USER to the migration connection role (not to 'postgres',
    not to a session-saved role, not to a no-op).

The whole reason migration 0004 exists is that on Neon production,
`SET LOCAL ROLE app_user` was failing because the connecting role had
no app_user membership. If CURRENT_USER inside the DO block does NOT
resolve to the migration connection role, the GRANT is a silent no-op
against the wrong role — this migration would land green but the bug
would persist.

Pass/fail criterion (the assertion that closes the gate):
    After applying the full chain on a fresh Postgres DB, run
        SELECT pg_has_role(current_user, 'app_user', 'MEMBER')
    against the same connection. It MUST return true.

Plan reference: turn_2_7_plan_v0_2_1.md §2.1 + §4.1
"""
from __future__ import annotations

import sqlalchemy as sa

from src.db.session import raw_admin_session


# This test is async and uses the migrated_db / clean_db fixture chain
# from tests/db/conftest.py (re-exported in tests/conftest.py).
# `clean_db` already implies `migrated_db`, which runs the FULL alembic
# chain to head — including 0004 once this migration is in place.


async def test_migration_0004_do_block_grants_app_user_to_migration_connection_role(
    clean_db: str,
) -> None:
    """Verifies the *primary* invariant of migration 0004:

    Inside the PL/pgSQL DO $$ ... $$ block, the GRANT statement
    resolves CURRENT_USER to the migration connection role (not to
    'postgres', not to a session-saved role, not to a silent no-op).

    Pass/fail criterion:
        After the full chain `<base> -> 0001 -> 0002 -> 0003 -> 0004`
        applies on a fresh database, `pg_has_role(current_user,
        'app_user', 'MEMBER')` returns true on a fresh connection
        from the same role that ran the migration.

    If this assertion ever fails, the bug is *almost certainly* that
    CURRENT_USER inside the DO block did not resolve to the migration
    connection role — that is the precise question the test answers.

    This is the regression guard for T2.7-D-Neon-1 (Stage B 2026-04-30
    failure mode where `SET LOCAL ROLE app_user` got 'permission denied'
    on Neon's neondb_owner because membership was never granted).

    Note on the local fixture being a superuser:
        On the local Homebrew/CI Postgres fixture, the connecting role
        IS a superuser, and `pg_has_role` returns true for ANY role
        because superusers implicitly inherit everything. So this test
        validates that the GRANT *executed* and is recorded in
        `pg_auth_members` — by checking BOTH `pg_has_role` AND a direct
        `pg_auth_members` lookup. The membership row is the
        non-superuser-bypassed evidence; pg_has_role is the
        runtime-effective-membership check. Both must be true.
    """
    async with raw_admin_session() as session:
        # Capture the connecting role (= migration connection role; the
        # subprocess `alembic upgrade head` run by the migrated_db
        # fixture connects with the same DATABASE_URL as this session).
        current_user_result = await session.execute(sa.text("SELECT current_user"))
        connecting_role = current_user_result.scalar_one()
        assert connecting_role, "current_user resolved to empty string"

        # Primary assertion — runtime effective membership.
        has_role_result = await session.execute(
            sa.text(
                "SELECT pg_has_role(current_user, 'app_user', 'MEMBER')"
            )
        )
        has_role = has_role_result.scalar_one()
        assert has_role is True, (
            f"Migration 0004 DO-block GRANT did NOT make the migration "
            f"connection role ({connecting_role!r}) a member of app_user. "
            f"CURRENT_USER inside the DO block likely did not resolve to "
            f"the connecting role. This is exactly T2.7-D-Neon-1 — the bug "
            f"Stage B remediated manually with `GRANT app_user TO neondb_owner`."
        )

        # Secondary assertion — direct pg_auth_members evidence.
        # On a non-superuser role this would be the only meaningful check
        # (since pg_has_role on a superuser always returns true). Both
        # checks are kept here so the test is meaningful regardless of
        # the connecting role's superuser status.
        membership_row = await session.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_auth_members am
                JOIN pg_roles role_member ON am.member = role_member.oid
                JOIN pg_roles role_group  ON am.roleid = role_group.oid
                WHERE role_member.rolname = current_user
                  AND role_group.rolname = 'app_user'
                """
            )
        )
        membership_present = membership_row.scalar_one_or_none()
        assert membership_present == 1, (
            f"Migration 0004 did NOT create a pg_auth_members row granting "
            f"app_user to the migration connection role ({connecting_role!r}). "
            f"This means the GRANT in 0004's DO block did not execute, OR "
            f"CURRENT_USER inside the DO block resolved to a DIFFERENT role. "
            f"Either way, T2.7-D-Neon-1 is not actually fixed by the source "
            f"migration and Stage C apply would still need manual remediation."
        )
