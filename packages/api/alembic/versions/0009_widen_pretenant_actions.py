"""Widen pretenant action allowlist to include auth.tenant_state_invalid.

FH-Tier-1 Slice 7.5 (plan v0.2.1, §4 + §10.2 B.1).

This migration completes the 4-layer coordination required to add
'auth.tenant_state_invalid' to the pretenant audit path. The two
in-Python layers (per FH-S7.5 plan §5 + SR-2 policy):

  Layer 1: PretenantAction = Literal["auth.rejected",
                                     "auth.tenant_state_invalid"]
  Layer 2: PRETENANT_ACTION_ALLOWLIST frozenset (2-element)

are landed in src/audit/context.py at FH-S7.5 step B.2. This migration
lands the two DB layers:

  Layer 3: audit_pretenant_insert function body IF-block
  Layer 4: ck_audit_tenant_required_or_pretenant CHECK constraint

Both DB layers widen in lockstep within this single migration — per the
discipline established by migration 0005's docstring:

    "Future pretenant actions are added one at a time via subsequent
     migrations (never `LIKE 'auth.%'` — explicit per-action allow only,
     per master sprint plan v0.5.1 §8.5 discipline)."

Why M6 (TenantStateInvalid at bootstrap.py:144) requires pretenant
widening rather than the simpler tenant-path option-(a):

  The only raise site for TenantStateInvalid fires precisely when
  `existing is None` — i.e., no tenant row exists for the credential's
  org_id. The exception has no derivable tenant_id, so it cannot flow
  through the tenant-scoped audit_events_insert RLS policy. Per FH-S7.5
  Phase A discovery G9, tenant enrichment is structurally infeasible at
  this raise site. Pretenant routing via the SECURITY DEFINER bypass is
  the correct path.

Two-part upgrade:

  Op 1: CREATE OR REPLACE FUNCTION audit_pretenant_insert(...) widening
        the action allowlist check from

            p_action <> 'auth.rejected'

        to

            p_action NOT IN ('auth.rejected', 'auth.tenant_state_invalid')

        Using REPLACE (not DROP+CREATE) preserves the EXECUTE grant to
        app_user established by 0008. The function body INSERT, the
        actor_type allowlist, SECURITY DEFINER, search_path, and the
        tenant_id=NULL hardcoding all remain byte-identical to 0008's
        contract.

  Op 2: DROP CONSTRAINT + ADD CONSTRAINT for
        ck_audit_tenant_required_or_pretenant. Postgres does not support
        ALTER CONSTRAINT modifying a CHECK expression in place — must
        DROP and ADD. The constraint name is preserved unchanged
        (downstream tooling may reference it).

Downgrade reverses both operations symmetrically:
  - Reverse Op 2 first (restore 0005's narrower CHECK)
  - Reverse Op 1 (CREATE OR REPLACE FUNCTION restoring the narrow IF block)

Revision: 0009_widen_pretenant_actions
Down revision: 0008_audit_pretenant_fn
Create Date: 2026-05-21

Naming note: len("0009_widen_pretenant_actions") == 28  ✓ (within
alembic_version.version_num VARCHAR(32))
"""
from alembic import op


revision = "0009_widen_pretenant_actions"
down_revision = "0008_audit_pretenant_fn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Op 1 — widen audit_pretenant_insert action allowlist.
    # CREATE OR REPLACE preserves the EXECUTE grant established by 0008.
    # Function body (INSERT semantics, SECURITY DEFINER, search_path,
    # actor_type allowlist, tenant_id NULL hardcoding) is byte-identical
    # to 0008 except for the action IF-block widening.
    # ------------------------------------------------------------------
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
            -- Action allowlist (mirrors ck_audit_tenant_required_or_pretenant
            -- as widened in Op 2 below; Layer 3 and Layer 4 lockstep).
            IF p_action NOT IN ('auth.rejected', 'auth.tenant_state_invalid') THEN
                RAISE EXCEPTION
                    'audit_pretenant_insert: action % not in pretenant allowlist',
                    p_action
                    USING ERRCODE = 'check_violation';
            END IF;

            -- actor_type allowlist (unchanged from 0008; mirrors the
            -- table-level _ACTOR_TYPE_CHECK constraint).
            IF p_actor_type NOT IN ('human', 'service_account', 'system', 'support') THEN
                RAISE EXCEPTION
                    'audit_pretenant_insert: actor_type % not allowed',
                    p_actor_type
                    USING ERRCODE = 'check_violation';
            END IF;

            -- INSERT with tenant_id=NULL; bypasses RLS via SECURITY DEFINER
            -- (unchanged from 0008). tenant_id and workspace_id are
            -- hardcoded NULL — callers cannot insert non-NULL tenant rows
            -- via this function (defense in depth).
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

    # ------------------------------------------------------------------
    # Op 2 — widen ck_audit_tenant_required_or_pretenant CHECK constraint.
    # Postgres has no ALTER CONSTRAINT for in-place CHECK modification;
    # DROP + ADD with the original constraint name is the supported path.
    # Constraint name preserved unchanged.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE audit_events "
        "DROP CONSTRAINT ck_audit_tenant_required_or_pretenant;"
    )
    op.execute(
        """
        ALTER TABLE audit_events
            ADD CONSTRAINT ck_audit_tenant_required_or_pretenant
            CHECK (
                tenant_id IS NOT NULL
                OR action IN ('auth.rejected', 'auth.tenant_state_invalid')
            );
        """
    )


def downgrade() -> None:
    # Symmetric reversal: reverse Op 2 first (restore narrower CHECK), then
    # reverse Op 1 (restore narrower function IF-block). Order avoids any
    # transient state where the function would accept an action that the
    # CHECK would later reject.

    # Reverse Op 2 — restore 0005's narrower CHECK.
    op.execute(
        "ALTER TABLE audit_events "
        "DROP CONSTRAINT ck_audit_tenant_required_or_pretenant;"
    )
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

    # Reverse Op 1 — restore 0008's narrower function body.
    # Again CREATE OR REPLACE to preserve the EXECUTE grant.
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
            -- Action allowlist (0008-era narrow form).
            IF p_action <> 'auth.rejected' THEN
                RAISE EXCEPTION
                    'audit_pretenant_insert: action % not in pretenant allowlist',
                    p_action
                    USING ERRCODE = 'check_violation';
            END IF;

            IF p_actor_type NOT IN ('human', 'service_account', 'system', 'support') THEN
                RAISE EXCEPTION
                    'audit_pretenant_insert: actor_type % not allowed',
                    p_actor_type
                    USING ERRCODE = 'check_violation';
            END IF;

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
