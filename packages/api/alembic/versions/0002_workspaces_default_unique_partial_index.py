"""Workspaces: at most one default workspace per tenant.

Adds the partial unique index that was deferred from Turn 1a (sprint plan
v0.5.1 §7.2). Before this migration, "at most one default workspace per
tenant" was enforced at the application layer in
``PostgresWorkspaceRepository.create`` via a check-then-insert. That
pattern races under concurrent bootstrap. This migration moves the
invariant to the DB so it is race-safe.

After this migration applies, the source-of-truth for the invariant is
the ``uq_workspaces_one_default_per_tenant`` partial unique index.
``PostgresWorkspaceRepository.create`` is refactored in this same Turn
(Turn 2.5 plan v0.2.1 §3.1 D-Mig + §6.1 + MF-5) to drop the app-level
check-then-insert and rely on ``IntegrityError`` mapping.

Required preflight before applying to any non-local environment
(Turn 2.5 plan v0.2.1 §3.1 RC-8):

    SELECT tenant_id, COUNT(*)
    FROM workspaces
    WHERE is_default = true
    GROUP BY tenant_id
    HAVING COUNT(*) > 1;

The query MUST return zero rows. If it returns any row, the migration
will fail at apply time with a unique-violation; the operator should
investigate and clean up before applying.

Revision: 0002_ws_default_unique_idx
Down revision: 0001_initial_schema
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_ws_default_unique_idx"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the partial unique index on workspaces(tenant_id) WHERE is_default IS TRUE."""
    op.create_index(
        "uq_workspaces_one_default_per_tenant",
        "workspaces",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    """Drop the partial unique index. Reverses upgrade()."""
    op.drop_index(
        "uq_workspaces_one_default_per_tenant",
        table_name="workspaces",
    )
