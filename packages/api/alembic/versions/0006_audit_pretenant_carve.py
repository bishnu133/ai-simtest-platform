"""audit_events tenant_isolation widened with pretenant write carve-out.

Tier-1 FH (Foundation Hardening) sprint, May 15, 2026.

Surfaced during P1 Slice 0-Neon empirical verification (P1.0d/P1.0e):
the PERMISSIVE audit_events_pretenant_insert policy added in 0005 did
not, in practice, allow INSERTs of auth.rejected + NULL tenant_id
under SET ROLE app_user, despite PostgreSQL's documented PERMISSIVE-OR
semantics predicting acceptance (NULL OR TRUE = TRUE).

Empirical probes confirmed:

  - Both 0005 policies exist on the parent audit_events (none on
    partitions).
  - Parent has relrowsecurity=t, relforcerowsecurity=t.
  - Partitions have RLS disabled (relrowsecurity=f) — RLS evaluation
    happens at parent only.
  - INSERT succeeds as the connecting superuser role (bypasses RLS).
  - INSERT fails under SET ROLE app_user even with
    app.current_tenant_id set to a sentinel UUID — so the failure
    is not about the GUC being unset.

The root cause of PERMISSIVE-OR not yielding TRUE when one branch
returns TRUE is not fully understood — possibly PG 16-specific
behavior or a subtle interaction between FOR ALL and FOR INSERT
PERMISSIVE policies. Recorded as Future-X in plan v0.3.4 for later
investigation.

Pragmatic fix: fold the pretenant carve-out into the existing
tenant_isolation_audit_events policy itself. Single policy, no
PERMISSIVE-OR dependency, no question of inter-policy combination.
Asymmetric USING/WITH CHECK preserves the security semantic that
pretenant audit_events rows are admin-only-readable:

    USING:      tenant_id = current_setting('app.current_tenant_id', true)::uuid
                (READ: strict tenant scoping; pretenant rows hidden from app_user)

    WITH CHECK: tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR (tenant_id IS NULL AND action = 'auth.rejected')
                (WRITE: tenant rows OR approved pretenant actions)

Three-part change:

  1. DROP audit_events_pretenant_insert — the 0005-added parallel
     PERMISSIVE policy that did not effectively contribute TRUE to
     OR evaluation. Removing it is a no-op for runtime behavior.

  2. DROP tenant_isolation_audit_events — required to redefine
     WITH CHECK (Postgres has no ALTER POLICY ... ALTER WITH CHECK
     in PG 16).

  3. CREATE tenant_isolation_audit_events with the widened
     WITH CHECK clause shown above.

Future pretenant actions are added one at a time via subsequent
migrations widening WITH CHECK (never `LIKE 'auth.%'` wildcards —
explicit per-action allow only). The CHECK constraint
ck_audit_tenant_required_or_pretenant (from 0005) is the table-level
mirror of the RLS WITH CHECK and must be updated in lockstep for any
new pretenant action.

Downgrade restores 0005's state symmetrically (re-adds pretenant_insert,
restores narrower tenant_isolation). Safe — no destructive operation
on audit data.

Revision: 0006_audit_pretenant_carve
Down revision: 0005_audit_nullable_tenant
Create Date: 2026-05-15

Naming note (mirrors 0002/0003/0004/0005 convention):
    len("0006_audit_pretenant_carve") == 26  ✓
"""
from alembic import op


revision = "0006_audit_pretenant_carve"
down_revision = "0005_audit_nullable_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the parallel pretenant_insert from 0005 — superseded.
    op.execute(
        "DROP POLICY audit_events_pretenant_insert ON audit_events;"
    )

    # 2. Drop the existing tenant_isolation to redefine WITH CHECK.
    op.execute(
        "DROP POLICY tenant_isolation_audit_events ON audit_events;"
    )

    # 3. Recreate tenant_isolation with asymmetric USING/WITH CHECK:
    #    - USING:      strict tenant scoping (READ; pretenant rows admin-only)
    #    - WITH CHECK: tenant rows OR approved pretenant actions (WRITE)
    op.execute(
        """
        CREATE POLICY tenant_isolation_audit_events ON audit_events
            AS PERMISSIVE FOR ALL TO app_user
            USING (
                tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
            )
            WITH CHECK (
                tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
                OR (tenant_id IS NULL AND action = 'auth.rejected')
            );
        """
    )


def downgrade() -> None:
    # Symmetric reversal: restore 0005's state (narrow tenant_isolation +
    # parallel pretenant_insert). Safe — no destructive operation on data.
    op.execute(
        "DROP POLICY tenant_isolation_audit_events ON audit_events;"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_audit_events ON audit_events
            AS PERMISSIVE FOR ALL TO app_user
            USING (
                tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
            )
            WITH CHECK (
                tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
            );
        """
    )
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
