"""audit_events RLS: per-command policy split with CASE-based WITH CHECK.

Tier-1 FH (Foundation Hardening) sprint, May 15, 2026.

After 0006's unified-WITH-CHECK approach also failed to allow pretenant
INSERTs (empirical probes PB SL06.5, SL06.6), the definitive diagnostic
PB SL06.7 isolated the root cause:

  WITH CHECK (true)                                  → INSERT succeeds
  WITH CHECK ((NULL = x) OR (TRUE))                  → INSERT rejected
  Same OR expression as a standalone SELECT           → returns TRUE

PG's policy framework rejects WITH CHECK expressions containing
OR-with-NULL branches under app_user INSERTs, even when the same
expression returns TRUE as a SELECT. Standard SQL three-valued logic
is honored at SELECT time but NOT at policy WITH CHECK time. Root
cause not fully understood; recorded as Future-X in plan v0.3.4 for
later investigation.

Pragmatic fix combining two defenses:

  1. Per-command policy split. Drop the FOR ALL
     tenant_isolation_audit_events policy. Replace with separate
     FOR SELECT (strict USING) and FOR INSERT (CASE-based WITH CHECK)
     policies. This eliminates any FOR ALL ambiguity about whether
     USING is also evaluated for INSERT (per docs it's not, but
     defensive isolation removes the question entirely).

  2. CASE-based WITH CHECK on the FOR INSERT policy. CASE returns
     concrete TRUE/FALSE per branch with no three-valued-logic NULL
     ambiguity, so the policy framework evaluates predictably:

         CASE
             WHEN tenant_id IS NULL THEN
                 action = 'auth.rejected'   -- TRUE or FALSE, never NULL
             ELSE
                 tenant_id = sentinel       -- TRUE or FALSE for non-null
         END

Result:
  - app_user INSERTs of auth.rejected + NULL tenant_id → CASE returns
    TRUE → row passes via audit_events_insert.
  - Tenant-scoped INSERTs → CASE goes to ELSE branch → match against
    GUC → TRUE/FALSE.
  - SELECTs remain strict tenant scoping via audit_events_select.
  - UPDATEs and DELETEs continue to be blocked by the existing
    RESTRICTIVE no_update / no_delete policies.

Pretenant rows (tenant_id IS NULL) remain admin-only readable —
audit_events_select's USING evaluates `NULL = sentinel` → NULL → policy
fails for app_user. Consistent with the v0.3.4 security model.

Three-part change:

  1. DROP tenant_isolation_audit_events (the 0006-widened FOR ALL).

  2. CREATE audit_events_select (FOR SELECT, USING strict).

  3. CREATE audit_events_insert (FOR INSERT, CASE-based WITH CHECK).

Future pretenant actions are added by widening the action list inside
the CASE WHEN clause AND the CHECK constraint
ck_audit_tenant_required_or_pretenant in lockstep.

Downgrade restores 0006's state symmetrically.

Revision: 0007_audit_iso_split_case
Down revision: 0006_audit_pretenant_carve
Create Date: 2026-05-15

Naming note: len("0007_audit_iso_split_case") == 25  ✓
"""
from alembic import op


revision = "0007_audit_iso_split_case"
down_revision = "0006_audit_pretenant_carve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the 0006-widened FOR ALL policy.
    op.execute(
        "DROP POLICY tenant_isolation_audit_events ON audit_events;"
    )

    # 2. FOR SELECT — strict tenant scoping for reads.
    #    Pretenant rows (tenant_id IS NULL) remain admin-only readable since
    #    no app_user session will have current_setting() = NULL.
    op.execute(
        """
        CREATE POLICY audit_events_select ON audit_events
            AS PERMISSIVE FOR SELECT TO app_user
            USING (
                tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
            );
        """
    )

    # 3. FOR INSERT — CASE-based WITH CHECK avoids OR-with-NULL ambiguity.
    #    The CASE returns concrete TRUE/FALSE for each branch, so PG's policy
    #    framework evaluates the WITH CHECK predictably (matches empirically
    #    confirmed PB SL06.7 behavior for non-NULL WITH CHECK expressions).
    op.execute(
        """
        CREATE POLICY audit_events_insert ON audit_events
            AS PERMISSIVE FOR INSERT TO app_user
            WITH CHECK (
                CASE
                    WHEN tenant_id IS NULL THEN
                        action = 'auth.rejected'
                    ELSE
                        tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
                END
            );
        """
    )


def downgrade() -> None:
    # Symmetric reversal: restore 0006's FOR ALL widened policy.
    op.execute(
        "DROP POLICY audit_events_insert ON audit_events;"
    )
    op.execute(
        "DROP POLICY audit_events_select ON audit_events;"
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
                OR (tenant_id IS NULL AND action = 'auth.rejected')
            );
        """
    )
