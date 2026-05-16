"""audit_events.tenant_id nullable for approved pre-tenant audit actions.

Tier-1 FH (Foundation Hardening) sprint, May 14, 2026.

Surfaced by T45-8 / FH-Tier-1 plan v0.3 + v0.3.1 + v0.3.2 + v0.3.3:
pre-tenant audit events (auth.rejected when no/invalid bearer token)
must persist to audit_events, but the table's `tenant_id UUID NOT NULL`
column prevents NULL tenant_id rows.

Three-part fix:

  1. Drop NOT NULL on audit_events.tenant_id. This is a metadata-only
     operation in Postgres 16; no table rewrite.

  2. Add CHECK constraint ck_audit_tenant_required_or_pretenant:

         tenant_id IS NOT NULL OR action = 'auth.rejected'

     Allows NULL only for the single approved pretenant action.
     Future pretenant actions are added one at a time via subsequent
     migrations (never `LIKE 'auth.%'` — explicit per-action allow only,
     per master sprint plan v0.5.1 §8.5 discipline).

  3. Add PERMISSIVE RLS policy audit_events_pretenant_insert: accepts
     INSERTs where tenant_id IS NULL AND action = 'auth.rejected'.
     Necessary because the existing audit_events_tenant_isolation
     policy blocks NULL tenant_id INSERTs (NULL = current_setting(...)
     evaluates to NULL, not TRUE). PERMISSIVE policies are OR'd, so the
     pretenant policy provides an alternative path. RESTRICTIVE
     no-update/no-delete policies remain in force (still AS RESTRICTIVE
     per master architectural lock).

Migration safety:

  - Existing tenant-scoped reads continue to filter via the
    audit_events_tenant_isolation PERMISSIVE policy. NULL tenant_id
    rows are invisible to tenant-scoped queries (semantic match —
    auth.rejected rows have no tenant to own them).

  - Append-only invariant unchanged (audit_events_no_update /
    audit_events_no_delete RESTRICTIVE policies remain in force).

  - Hot-path safety: DROP NOT NULL is O(1) metadata; CHECK ADD is a
    full table scan, but partitioned audit_events has small early-life
    partitions at Tier-1 timing.

Downgrade is intentionally fail-loud:

  If any NULL tenant_id rows exist when downgrade runs, the
  ALTER COLUMN ... SET NOT NULL step fails. Operator must explicitly
  purge those rows first — which is a destructive operation on append-only
  audit evidence and should never happen accidentally. This matches
  the project's destructive-ops-require-explicit-acknowledgment discipline.

Idempotency:

  DROP POLICY IF EXISTS / DROP CONSTRAINT IF EXISTS guard the downgrade.
  Upgrade is not re-runnable as-is (CREATE POLICY without IF NOT EXISTS
  would fail on retry); a re-attempt requires running downgrade first.
  This matches the convention used by 0001-0004 in this project.

Revision: 0005_audit_nullable_tenant
Down revision: 0004_grant_app_user_membership
Create Date: 2026-05-14

Naming note (mirrors 0002/0003/0004 convention):

  File name is descriptive; revision id is the short identifier.
  Alembic's alembic_version.version_num column is varchar(32), so
  the revision string must be <= 32 chars.

      len("0005_audit_nullable_tenant") == 26  ✓
"""
from alembic import op


# Descriptive revision IDs per FH-Tier-1 v0.3.2 §A.1 (within 32-char limit)
revision = "0005_audit_nullable_tenant"
down_revision = "0004_grant_app_user_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop NOT NULL on tenant_id (metadata-only in PG16).
    op.execute("ALTER TABLE audit_events ALTER COLUMN tenant_id DROP NOT NULL;")

    # 2. CHECK constraint: NULL only for approved pre-tenant actions.
    #    Initially allows ONLY 'auth.rejected'. Future actions added one at
    #    a time via subsequent migrations (no wildcards).
    op.execute(
        """
        ALTER TABLE audit_events
        ADD CONSTRAINT ck_audit_tenant_required_or_pretenant
        CHECK (
            tenant_id IS NOT NULL
            OR action = 'auth.rejected'
        );
        """
    )

    # 3. RLS policy for pre-tenant INSERTs.
    #    The existing audit_events_tenant_isolation PERMISSIVE policy blocks
    #    NULL tenant_id inserts (NULL = current_setting(...) yields NULL, not
    #    TRUE). PERMISSIVE policies are OR'd, so this parallel PERMISSIVE
    #    policy accepts the exact pre-tenant shape. The RESTRICTIVE
    #    no-update/no-delete policies remain in force.
    op.execute(
        """
        CREATE POLICY audit_events_pretenant_insert ON audit_events
            AS PERMISSIVE FOR INSERT TO app_user
            WITH CHECK (
                tenant_id IS NULL
                AND action = 'auth.rejected'
            );
        """
    )


def downgrade() -> None:
    # Intentionally fail-loud: if any NULL tenant_id rows exist, the
    # ALTER COLUMN ... SET NOT NULL step fails. Operator must explicitly
    # purge those rows first — see module docstring "Downgrade is intentionally
    # fail-loud" for rationale.
    op.execute("DROP POLICY IF EXISTS audit_events_pretenant_insert ON audit_events;")
    op.execute(
        "ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS ck_audit_tenant_required_or_pretenant;"
    )
    op.execute("ALTER TABLE audit_events ALTER COLUMN tenant_id SET NOT NULL;")
