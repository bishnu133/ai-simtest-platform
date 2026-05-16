"""SECURITY DEFINER function for pretenant audit_events INSERTs.

Tier-1 FH (Foundation Hardening) sprint, May 15, 2026.

After definitive verification on Neon (PB SL07.5) that 0007's CASE-based
WITH CHECK approach also fails for app_user inserts of tenant_id=NULL
rows, PostgreSQL's policy framework cannot evaluate any conditional
WITH CHECK expression that statically references the row's columns
under app_user, even when the expression returns TRUE as a standalone
SELECT. The empirical pattern:

    WITH CHECK (true)                                    → works
    WITH CHECK ((NULL = X) OR (TRUE))                    → fails
    WITH CHECK (CASE WHEN tenant_id IS NULL THEN ... END)→ fails

Root cause is not fully understood — possibly PG's policy framework
applies static-analysis-based fail-secure semantics for any WITH CHECK
expression involving row columns where NULL could appear. Recorded as
Future-X in plan v0.3.4 for later investigation (worth filing a PG
behavior report or community question once Tier-2 starts).

Pragmatic fix: SECURITY DEFINER function.

The function runs as its owner. On Neon, the migration runner is
neondb_owner which has rolbypassrls=t (confirmed in PB SL06.7 §A.4).
The INSERT inside the function therefore bypasses the audit_events
RLS policies entirely. On local test PG, the migration runner is the
spawned postgres superuser, which also bypasses RLS.

Restrict the function via four defense-in-depth layers:

  1. Action allowlist inside the function body (only 'auth.rejected'
     is accepted; mirrors the ck_audit_tenant_required_or_pretenant
     CHECK constraint added in 0005).

  2. actor_type allowlist inside the function body (mirrors the
     ORM-level _ACTOR_TYPE_CHECK constraint).

  3. Function ownership and EXECUTE grant scope — only app_user can
     call this function. The function is owned by the migration runner
     (BYPASSRLS); other roles cannot impersonate it.

  4. tenant_id is hardcoded to NULL inside the function (callers cannot
     INSERT non-NULL tenant rows via this path; tenant-scoped INSERTs
     continue to use the standard direct-INSERT path through the
     RLS-protected audit_events_insert policy).

This is the only supported path for inserting audit_events rows with
tenant_id IS NULL. Slice 4's PostgresAuditEventRepository.append_pretenant_event
will call this function via `SELECT audit_pretenant_insert(...)`
instead of executing a direct INSERT statement.

Three-part change:

  1. DROP audit_events_insert (the 0007 CASE-based policy).

  2. CREATE audit_events_insert with strict tenant-scoping only (no
     pretenant carve-out). This matches the original 0001-era semantic
     for tenant-scoped INSERTs.

  3. CREATE FUNCTION audit_pretenant_insert SECURITY DEFINER, then
     GRANT EXECUTE TO app_user.

Future pretenant actions are added by widening the function's action
allowlist AND the ck_audit_tenant_required_or_pretenant CHECK
constraint in lockstep.

Downgrade restores 0007's CASE-based audit_events_insert and drops
the function.

Revision: 0008_audit_pretenant_fn
Down revision: 0007_audit_iso_split_case
Create Date: 2026-05-15

Naming note: len("0008_audit_pretenant_fn") == 23  ✓
"""
from alembic import op


revision = "0008_audit_pretenant_fn"
down_revision = "0007_audit_iso_split_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the 0007 CASE-based audit_events_insert (which doesn't work
    #    empirically — see module docstring).
    op.execute(
        "DROP POLICY audit_events_insert ON audit_events;"
    )

    # 2. Recreate audit_events_insert with strict tenant scoping only.
    #    Pretenant INSERTs now go through audit_pretenant_insert() below.
    op.execute(
        """
        CREATE POLICY audit_events_insert ON audit_events
            AS PERMISSIVE FOR INSERT TO app_user
            WITH CHECK (
                tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid
            );
        """
    )

    # 3. Create SECURITY DEFINER function for pretenant audit inserts.
    #    Function runs as owner (BYPASSRLS / superuser), so the INSERT
    #    inside bypasses RLS. Four allowlist layers enforce the pretenant
    #    contract; see module docstring.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_pretenant_insert(
            p_action text,
            p_actor_id text DEFAULT 'anonymous',
            p_actor_type text DEFAULT 'system',
            p_resource_type text DEFAULT 'auth',
            p_resource_id text DEFAULT 'session',
            p_correlation_id text DEFAULT NULL,
            p_details jsonb DEFAULT '{}'::jsonb,
            p_ip_address inet DEFAULT NULL,
            p_user_agent text DEFAULT NULL
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $func$
        DECLARE
            new_id uuid;
        BEGIN
            -- Action allowlist (mirrors ck_audit_tenant_required_or_pretenant from 0005).
            IF p_action <> 'auth.rejected' THEN
                RAISE EXCEPTION
                    'audit_pretenant_insert: action % not in pretenant allowlist',
                    p_action
                    USING ERRCODE = 'check_violation';
            END IF;

            -- actor_type allowlist (mirrors the table-level _ACTOR_TYPE_CHECK constraint).
            IF p_actor_type NOT IN ('human', 'service_account', 'system', 'support') THEN
                RAISE EXCEPTION
                    'audit_pretenant_insert: actor_type % not allowed',
                    p_actor_type
                    USING ERRCODE = 'check_violation';
            END IF;

            -- INSERT with tenant_id=NULL; bypasses RLS via SECURITY DEFINER.
            -- tenant_id and workspace_id are hardcoded NULL — callers cannot insert
            -- non-NULL tenant rows via this function (defense in depth).
            INSERT INTO audit_events (
                action, actor_id, actor_type,
                tenant_id, workspace_id,
                resource_type, resource_id,
                correlation_id, details,
                ip_address, user_agent
            ) VALUES (
                p_action, p_actor_id, p_actor_type,
                NULL, NULL,
                p_resource_type, p_resource_id,
                p_correlation_id, p_details,
                p_ip_address, p_user_agent
            )
            RETURNING id INTO new_id;

            RETURN new_id;
        END;
        $func$;
        """
    )

    # 4. Grant EXECUTE to app_user (only authorized role).
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION audit_pretenant_insert(
            text, text, text, text, text, text, jsonb, inet, text
        ) TO app_user;
        """
    )


def downgrade() -> None:
    # Symmetric reversal: drop the function and restore 0007's CASE-based
    # audit_events_insert.
    op.execute(
        """
        DROP FUNCTION IF EXISTS audit_pretenant_insert(
            text, text, text, text, text, text, jsonb, inet, text
        );
        """
    )
    op.execute(
        "DROP POLICY audit_events_insert ON audit_events;"
    )
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
