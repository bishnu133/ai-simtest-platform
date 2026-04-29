"""Add workspace_id to idempotency_keys (Turn 2.6 Step 3).

Pre-Turn-2.6, idempotency was in-memory only. The table existed but was
never written to. Turn 2.6 introduces ``PostgresIdempotencyRepository`` and
the in-memory store has always been **workspace-scoped** (key:
``(workspace_id, idempotency_key)``), so the persistent table must mirror
that workspace dimension to preserve the same isolation guarantees:

  * Two workspaces in the same tenant may use the same idempotency_key
    independently.
  * RLS continues to scope by tenant (workspace_id is a defense-in-depth
    column inside the tenant-scoped row, not an RLS dimension).

Schema changes:
  1. ADD COLUMN ``workspace_id uuid NOT NULL`` with FK to workspaces.id
     ON DELETE CASCADE.
  2. DROP INDEX ``uq_idempotency_keys_tenant_key`` (the old
     tenant+key uniqueness).
  3. CREATE INDEX ``uq_idempotency_keys_tenant_workspace_key`` UNIQUE
     ON (tenant_id, workspace_id, idempotency_key).
  4. CREATE INDEX ``ix_idempotency_keys_expires_at`` for lazy cleanup
     of expired entries.

Fail-loud preflight (Turn 2.6 plan v0.2.1 §7.1 + R-4):
    Idempotency has been in-memory only until Turn 2.6, so the table is
    expected to be empty. ``upgrade()`` runs ``SELECT count(*) FROM
    idempotency_keys`` and refuses to apply if it returns non-zero. This
    is the right safety guarantee because we cannot infer ``workspace_id``
    for existing tenant-scoped rows.

Required preflight before applying to any non-local environment:

    SELECT count(*) FROM idempotency_keys;  -- MUST return 0

If it returns >0, pause and reconcile. Either truncate the table or
backfill workspace_id from another source before retrying.

Revision: 0003_idem_keys_workspace_id
Down revision: 0002_ws_default_unique_idx
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic. Mirrors the 0002 file's idiom
# (bare assignment, no type annotations). Verified at v0.2.1 §A.1 against
# 0002_workspaces_default_unique_partial_index.py:38 — exact string match.
# Note: Alembic's alembic_version.version_num column is varchar(32), so
# the revision string must be <= 32 chars.
revision = "0003_idem_keys_workspace_id"
down_revision = "0002_ws_default_unique_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail-loud preflight (Turn 2.6 plan v0.2.1 §7.1 + R-4): idempotency
    # has been in-memory only until Turn 2.6, so the table is expected to
    # be empty. If it isn't, manual reconciliation is required because
    # we cannot infer workspace_id from existing tenant-scoped rows.
    conn = op.get_bind()
    row_count = conn.execute(
        sa.text("SELECT count(*) FROM idempotency_keys")
    ).scalar_one()
    if row_count != 0:
        raise RuntimeError(
            f"Migration 0003 refuses to apply: idempotency_keys has "
            f"{row_count} row(s). Turn 2.6 expects an empty table because "
            f"idempotency was in-memory only until this turn. Manual "
            f"reconciliation is required: either truncate the table or "
            f"backfill workspace_id from another source before retrying."
        )

    # Add workspace_id (NOT NULL is safe because the table is empty per
    # the preflight above).
    op.add_column(
        "idempotency_keys",
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "workspaces.id",
                ondelete="CASCADE",
                name="fk_idempotency_keys_workspace_id_workspaces",
            ),
            nullable=False,
        ),
    )

    # Replace tenant-scoped uniqueness with tenant+workspace-scoped.
    # 0001 created this as a unique INDEX (not a constraint), so we must
    # drop the index, not the constraint.
    op.drop_index(
        "uq_idempotency_keys_tenant_key",
        table_name="idempotency_keys",
    )
    op.create_index(
        "uq_idempotency_keys_tenant_workspace_key",
        "idempotency_keys",
        ["tenant_id", "workspace_id", "idempotency_key"],
        unique=True,
    )

    # Lazy-cleanup support: index expires_at for DELETE WHERE expires_at <= now()
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
    )


def downgrade() -> None:
    """Reverses upgrade() exactly. The fail-loud preflight is upgrade-only;
    downgrade is always permitted and does not check row count, because
    the operator is explicitly choosing to revert.
    """
    op.drop_index(
        "ix_idempotency_keys_expires_at",
        table_name="idempotency_keys",
    )
    op.drop_index(
        "uq_idempotency_keys_tenant_workspace_key",
        table_name="idempotency_keys",
    )
    op.create_index(
        "uq_idempotency_keys_tenant_key",
        "idempotency_keys",
        ["tenant_id", "idempotency_key"],
        unique=True,
    )
    op.drop_constraint(
        "fk_idempotency_keys_workspace_id_workspaces",
        "idempotency_keys",
        type_="foreignkey",
    )
    op.drop_column("idempotency_keys", "workspace_id")
