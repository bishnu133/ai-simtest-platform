"""Grant app_user role membership to the connecting role.

Surfaced by Stage B (2026-04-30) on Neon production. On non-superuser
connecting roles (Neon's neondb_owner, CI roles, future Stage C
production roles), `SET LOCAL ROLE app_user` issued by
`tenant_scoped_session` fails with:

    ERROR: permission denied to set role "app_user"

unless the connecting role has been granted membership in app_user.
0001 creates the role but does not grant membership. Local Homebrew
PG hides the bug because the connecting role is superuser and SET ROLE
is unrestricted.

This migration fixes it forward.

Form: `GRANT app_user TO CURRENT_USER`.

CURRENT_USER is an explicit `role_specification` keyword in the Postgres
GRANT grammar (see Postgres docs sql-grant.html) — evaluated at execution
time to the connecting role. Using the keyword form avoids both string
interpolation and dynamic SQL entirely.

Idempotency: re-granting an existing role membership is a no-op in
Postgres. The DO block guards against `app_user` not existing yet
(which shouldn't happen because 0001 must run first, but defense in
depth — same idiom 0001 itself uses for CREATE ROLE).

Downgrade is intentionally a no-op — see downgrade() docstring.

Stage B precedent (2026-04-30, Neon `ai-simtest-dev/neondb` Singapore):
    Operator manually ran `GRANT app_user TO neondb_owner;` to remediate
    the upgrade-time failure that this migration now fixes at the source.
    This pre-existing membership is exactly why downgrade is no-op:
    revoking would remove a membership granted outside this migration.

Revision: 0004_grant_app_user_membership
Down revision: 0003_idem_keys_workspace_id
Create Date: 2026-05-01

Naming note (mirrors 0002/0003 convention):
    File name is descriptive; revision id is the short identifier.
    Alembic's alembic_version.version_num column is varchar(32), so
    the revision string must be <= 32 chars. Verified at v0.2.1
    implementation time:
        len("0004_grant_app_user_membership") == 30  ✓
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic. Bare assignment, no type
# annotations — mirrors 0002/0003 idiom.
revision = "0004_grant_app_user_membership"
down_revision = "0003_idem_keys_workspace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant app_user role membership to the migration connection role.

    Wrapped in a DO block guarded by `pg_roles` lookup so the migration
    is robust to a partially-applied chain (defense in depth — 0001
    creates `app_user`, so under normal application order this guard
    never trips).

    Inside the DO block, `CURRENT_USER` resolves to the role currently
    executing the block — which, for `alembic upgrade head`, is the
    role that opened the migration connection. That is the role that
    needs to be able to `SET LOCAL ROLE app_user` at runtime via
    `tenant_scoped_session`.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                GRANT app_user TO CURRENT_USER;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    """No-op.

    GRANT in upgrade() is intentionally NOT symmetrized with a REVOKE
    here, because the connecting role's `app_user` membership may have
    been granted manually before this migration was applied. This is
    exactly the case on Neon production: Stage B operator ran
    `GRANT app_user TO neondb_owner` on 2026-04-29 to remediate the
    upgrade-time failure that this migration now fixes at the source.
    A symmetric REVOKE on downgrade would remove that pre-existing
    membership and leave `tenant_scoped_session` broken on rollback —
    the inverse of the problem this migration solves.

    If a future operator needs to remove app_user membership from a role
    that was granted only by this migration (a rare case in practice),
    they can do so manually:

        REVOKE app_user FROM <role_name>;

    But that decision belongs to the operator, not the migration.

    Approved as no-op by Bishnu, plan v0.2.1 §2.1 (after v0.1 review
    flagged the symmetric-REVOKE as enterprise-unsafe).
    """
    pass
