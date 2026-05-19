"""Bootstrap lifecycle — tenant/workspace/membership provisioning on login.

Entry point: `bootstrap(session, claims)` — owns the transaction.

Sub-functions:
  * `ensure_tenant_and_workspace(session, claims)` — get-or-create tenant,
    get-or-create default workspace (or honor explicit workspace claim)
  * `ensure_membership(session, tenant_id, workspace_id, claims)` — the
    v0.4 §5.6 precedence-only fast-path:
      1. Workspace-level membership exists → return its role
      2. Tenant-level membership exists → return its role
      3. Neither → new-user onboarding: map provider_org_role,
         fail-closed, create both rows, return mapped role

Transaction semantics:
  * bootstrap() wraps the whole sequence in `async with session.begin():`
  * At-most-one retry on DuplicateTenant or DuplicateMembership
    (concurrent-first-login races collapse on the second attempt
    because the winning transaction committed the row the losing
    attempt then sees and takes the existing-row path)

Audit emission:
  * TENANT_CREATED — emitted by ensure_tenant_and_workspace on
    first-use tenant provisioning (via bootstrap result, which the
    middleware consumes)
  * WORKSPACE_CREATED — ditto for default workspace provisioning
  * AUTH_BOOTSTRAP_CREATED_TENANT — emitted by bootstrap() itself so
    first-use flows are traceable in the auth audit stream
  * AUTH_MEMBERSHIP_DENIED — emitted by ensure_membership when the
    new-user onboarding path hits MissingProviderOrgRole or
    UnknownProviderOrgRole (fail-closed deny, traceable)

Fail-closed:
  * Only genuinely-new users (no memberships at all) can trip the
    provider_org_role fail-closed path. Existing members authorize
    via the precedence fast-path regardless of provider_org_role state.
  * First-ever user in a tenant bootstraps as `owner` without
    consulting provider_org_role at all (plan §6.5 first-user rule).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import (
    CrossTenantForbidden,
    DuplicateMembership,
    DuplicateTenant,
    MissingProviderOrgRole,
    TenantStateInvalid,
    UnknownProviderOrgRole,
    WorkspaceNotFound,
)
from src.audit._compat import to_tenant_audit_event
from src.audit.logger import AuditActions, AuditEvent, audit_logger
from src.auth.provider import VerifiedClaims
from src.auth.role_mapping import map_provider_org_role
from src.common.models import ActorRef, utcnow
from src.db.models import Membership as MembershipORM
from src.memberships import PostgresMembershipRepository
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

__all__ = [
    "BootstrapResult",
    "bootstrap",
    "ensure_tenant_and_workspace",
    "ensure_membership",
]


# Max retries on concurrent-first-login duplicate-constraint races.
# v0.4 §5.6.1: exactly one retry. If the second attempt also hits a
# duplicate, that's a genuine bug, surface as 500.
_MAX_BOOTSTRAP_RETRIES = 1


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of a bootstrap() call.

    Middleware uses (tenant_id, workspace_id) to build the TenantContext
    and `role` to seed the optional fast-path on TenantContext (the
    `object.__setattr__(ctx, "role", role)` pattern — so the first
    `get_actor_role` call on this request doesn't need to re-hit the
    DB).
    """

    tenant_id: str
    workspace_id: str
    role: str
    is_first_user: bool = False


# ---------------------------------------------------------------------------
# ensure_tenant_and_workspace — get-or-create tenant + workspace
# ---------------------------------------------------------------------------


async def ensure_tenant_and_workspace(
    session: AsyncSession,
    claims: VerifiedClaims,
    *,
    allow_self_serve_provisioning: bool = True,
) -> tuple[str, str, bool]:
    """Resolve (tenant_id, workspace_id) from VerifiedClaims.

    Returns (tenant_id, workspace_id, tenant_just_created).

    1. Look up tenant by claims.org_id. If absent:
       - If ``allow_self_serve_provisioning`` is True, create it.
       - Otherwise, raise ``TenantStateInvalid`` (409) — the credential
         is valid but no matching tenant exists and this environment
         does not permit automatic provisioning.
    2. If claims.workspace_id is set, validate it (must belong to the
       resolved tenant; cross-tenant claims raise CrossTenantForbidden).
    3. Otherwise, look up the default workspace for this tenant. If
       none exists (i.e., we JUST created the tenant), create one
       flagged is_default=True.

    Parameters
    ----------
    allow_self_serve_provisioning:
        Plan §3.10 policy flag. When False, an unknown org_id is
        treated as provisioning drift and raises TenantStateInvalid.
        Defaults to True to preserve legacy call sites; production
        callers thread the AppSettings value through explicitly.
    """
    tenant_repo = PostgresTenantRepository()
    workspace_repo = PostgresWorkspaceRepository()

    tenant_just_created = False
    existing = await tenant_repo.get_by_clerk_org_id(
        claims.org_id, session=session
    )
    if existing is None:
        # Turn 4 §3.10.2 — fail-closed provisioning gate. If self-serve
        # is disabled for this environment, an unknown org_id is
        # provisioning drift, not a green-field bootstrap signal.
        if not allow_self_serve_provisioning:
            raise TenantStateInvalid(
                "No tenant exists for this credential's org_id and "
                "self-serve provisioning is disabled for this "
                "environment.",
                details={
                    "org_id": claims.org_id,
                    "reason": "unknown_org_id_self_serve_disabled",
                },
            )
        # Derive a stable slug from the org_id. Clerk org_ids look
        # like "org_2abc..." — strip the prefix so slugs are readable.
        slug = claims.org_id.removeprefix("org_") or claims.org_id
        slug = slug.lower()[:64]  # schema cap is 64 chars
        created = await tenant_repo.create(
            name=claims.display_name or claims.org_id,
            slug=slug,
            clerk_org_id=claims.org_id,
            session=session,
        )
        tenant_id = created.id
        tenant_just_created = True
    else:
        tenant_id = existing.id

    # Workspace resolution
    if claims.workspace_id is not None:
        # Explicit workspace claim — validate ownership.
        # Build a minimal TenantContext for the cross-tenant info-leak
        # guard (workspace_repo.get_by_id requires a ctx).
        from src.common.models import TenantContext

        ctx = TenantContext(
            tenant_id=tenant_id,
            workspace_id=claims.workspace_id,  # placeholder for context shape
            actor=ActorRef(actor_id=claims.user_id, actor_type="human"),
        )
        # This call raises CrossTenantForbidden if the claimed workspace
        # belongs to a different tenant — exactly the §5.7 info-leak
        # guard we want during bootstrap.
        ws = await workspace_repo.get_by_id(
            ctx, claims.workspace_id, session=session
        )
        return tenant_id, ws.id, tenant_just_created

    # No explicit claim — use or create the default workspace
    default_ws = await workspace_repo.get_default_for_tenant(
        tenant_id, session=session
    )
    if default_ws is not None:
        return tenant_id, default_ws.id, tenant_just_created

    # No default yet — create one. Tenant MUST have been just created
    # for this to happen (a pre-existing tenant would have a default
    # workspace from its own first-use bootstrap).
    created_ws = await workspace_repo.create(
        tenant_id=tenant_id,
        name="Default Workspace",
        is_default=True,
        session=session,
    )
    return tenant_id, created_ws.id, tenant_just_created


# ---------------------------------------------------------------------------
# ensure_membership — plan v0.4 §5.6 precedence-only fast-path
# ---------------------------------------------------------------------------


async def ensure_membership(
    session: AsyncSession,
    tenant_id: str,
    workspace_id: str,
    claims: VerifiedClaims,
) -> str:
    """Resolve the actor's role using existing memberships first.

    v0.4 §5.6 precedence ladder:
      1. Workspace-level row for (tenant, user, workspace) → return its role
      2. Tenant-level row for (tenant, user, None) → return its role
      3. Neither → NEW-USER ONBOARDING: map provider_org_role,
         fail-closed on missing/unknown, create BOTH rows, return
         mapped role

    The fast-path means returning users stay authorized even if
    provider_org_role is absent or weird on a given request —
    existing memberships are the authority (plan v0.4 MF-1).

    Audit emission:
      * AUTH_MEMBERSHIP_DENIED is emitted ONLY on the step-3 fail-closed
        paths (missing/unknown provider_org_role during new-user
        onboarding). The fast-path is silent because it's the hot
        path on every authenticated request — auditing every fast-path
        auth would drown the audit stream.
    """
    repo = PostgresMembershipRepository()
    user_id = claims.user_id

    # Step 1: workspace-level row wins
    ws_role = await repo.get_role(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=workspace_id,
        session=session,
    )
    if ws_role is not None:
        return ws_role

    # Step 2: tenant-level row as fallback
    tenant_role = await repo.get_role(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=None,
        session=session,
    )
    if tenant_role is not None:
        return tenant_role

    # Step 3: no memberships exist — true onboarding path.
    # This is the ONLY path that requires provider_org_role.
    try:
        mapped_role = map_provider_org_role(claims)
    except (MissingProviderOrgRole, UnknownProviderOrgRole) as exc:
        # Emit audit BEFORE raising so the deny is traceable.
        # Build a minimal context for audit emission (Slice 7 — async path
        # via aemit_tenant_event_safe; ctx still required for the
        # to_tenant_audit_event helper).
        from src.common.models import TenantContext

        deny_ctx = TenantContext(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=ActorRef(actor_id=user_id, actor_type="human"),
        )
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event(
                ctx=deny_ctx,
                action=AuditActions.AUTH_MEMBERSHIP_DENIED,
                resource_type="membership",
                resource_id=user_id,
                metadata={
                    "reason": exc.code,
                    "provider_org_role": claims.provider_org_role,
                    "org_id": claims.org_id,
                },
            )
        )
        raise

    # Create both tenant-level and workspace-level rows per §6.5
    # dual-membership rule.
    await repo.create(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=None,
        role=mapped_role,
        session=session,
    )
    await repo.create(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=workspace_id,
        role=mapped_role,
        session=session,
    )
    return mapped_role


# ---------------------------------------------------------------------------
# First-user detection helper
# ---------------------------------------------------------------------------


async def _tenant_has_any_member(
    session: AsyncSession, tenant_id: str
) -> bool:
    """Return True if ANY membership row exists for this tenant.

    Used by bootstrap() to detect the first-user-as-owner case. This
    is distinct from "current user has a membership" — we want to
    know whether the TENANT has any inhabitants at all. If not, the
    incoming user is the first and bootstraps as owner.
    """
    result = await session.execute(
        select(MembershipORM.id)
        .where(MembershipORM.tenant_id == tenant_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# bootstrap — entry point, owns the transaction + retry policy
# ---------------------------------------------------------------------------


async def bootstrap(
    session: AsyncSession,
    claims: VerifiedClaims,
    *,
    allow_self_serve_provisioning: bool = True,
    _attempt: int = 0,
) -> BootstrapResult:
    """Entry point for Turn 3 middleware. Idempotent + retry-safe.

    Transaction boundary: one `async with session.begin():` wraps
    tenant + workspace + membership work. Partial failures roll back
    cleanly.

    Retry policy: at most one retry on DuplicateTenant or
    DuplicateMembership. Those duplicates happen when two concurrent
    first-logins race on the same org_id or user_id. The winning
    transaction commits the row; the losing transaction retries and
    takes the "existing row" path on the second attempt.

    Parameters
    ----------
    allow_self_serve_provisioning:
        Plan §3.10 policy flag, threaded through to
        ``ensure_tenant_and_workspace``. Defaults to True to preserve
        legacy call sites. The middleware reads this from
        ``request.app.state.settings.allow_self_serve_provisioning``
        (set by ``app_factory.create_app``) and passes it in.
    """
    try:
        async with session.begin():
            tenant_id, workspace_id, tenant_just_created = (
                await ensure_tenant_and_workspace(
                    session,
                    claims,
                    allow_self_serve_provisioning=(
                        allow_self_serve_provisioning
                    ),
                )
            )

            # First-user-as-owner check: if NO memberships exist for
            # this tenant at all, current user is the first.
            has_members = await _tenant_has_any_member(session, tenant_id)

            if not has_members:
                # First user ever — bootstrap as owner, skip
                # provider_org_role mapping entirely. This is the plan
                # §6.5 first-user rule.
                repo = PostgresMembershipRepository()
                await repo.create(
                    tenant_id=tenant_id,
                    user_id=claims.user_id,
                    workspace_id=None,
                    role="owner",
                    session=session,
                )
                await repo.create(
                    tenant_id=tenant_id,
                    user_id=claims.user_id,
                    workspace_id=workspace_id,
                    role="owner",
                    session=session,
                )
                role = "owner"
                is_first_user = True
            else:
                role = await ensure_membership(
                    session, tenant_id, workspace_id, claims
                )
                is_first_user = False

            result = BootstrapResult(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                role=role,
                is_first_user=is_first_user,
            )

    except (DuplicateTenant, DuplicateMembership):
        if _attempt >= _MAX_BOOTSTRAP_RETRIES:
            # Second-attempt duplicate is a genuine bug, not a race.
            raise
        # Retry exactly once. The losing transaction's rollback already
        # happened inside the `async with session.begin():` context.
        return await bootstrap(
            session,
            claims,
            allow_self_serve_provisioning=allow_self_serve_provisioning,
            _attempt=_attempt + 1,
        )

    # After commit, emit tenant-created audit event if this call
    # provisioned a new tenant. We do this OUTSIDE the transaction
    # block so the audit log only reflects committed state — we never
    # claim a tenant was created when the transaction ultimately
    # rolled back.
    if tenant_just_created:
        commit_ctx = _build_actor_ctx(claims, result)
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event(
                ctx=commit_ctx,
                action=AuditActions.AUTH_BOOTSTRAP_CREATED_TENANT,
                resource_type="tenant",
                resource_id=result.tenant_id,
                metadata={
                    "org_id": claims.org_id,
                    "user_id": claims.user_id,
                    "is_first_user": result.is_first_user,
                },
            )
        )
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event(
                ctx=commit_ctx,
                action=AuditActions.TENANT_CREATED,
                resource_type="tenant",
                resource_id=result.tenant_id,
                metadata={"org_id": claims.org_id},
            )
        )

    return result


def _build_actor_ctx(claims: VerifiedClaims, result: BootstrapResult):
    """Build a TenantContext for audit emission from the bootstrap result."""
    from src.common.models import TenantContext

    return TenantContext(
        tenant_id=result.tenant_id,
        workspace_id=result.workspace_id,
        actor=ActorRef(actor_id=claims.user_id, actor_type="human"),
    )
