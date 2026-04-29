"""Authorization layer — real `get_actor_role` + role requirement helpers.

Two responsibilities split from authentication:

* `get_actor_role(ctx, session)` — resolves the effective role for the
  current request's actor using the workspace-over-tenant precedence
  rule from plan v0.4 §6.6.1.
* `require_role(minimum)` — FastAPI dependency factory that gates a
  route on a minimum role.
* `require_role_or_creator(minimum, creator_id_fn)` — creator exception
  variant, used for "cancel your own run" semantics.

Fast-path: if the middleware already attached a `role` attribute to
the `TenantContext` (which it does via `object.__setattr__(ctx, "role", role)`
after bootstrap), this module short-circuits the DB lookup and uses
the pre-resolved role. Tests that bypass the middleware and inject a
role directly on the ctx hit the same fast-path. This is the plan
§4.3 backwards-compat guarantee #3.

Precedence rules (plan v0.4 §6.6.1):
  1. Workspace-level membership wins when present.
  2. Tenant-level membership is the fallback.
  3. No membership at either level → 403 ``not_member_of_tenant``.

Role ordering (lowest to highest):
  viewer < member < admin < owner < service_account

The `service_account` slot is reserved; it sits above `owner` because
when a service account IS provisioned, it generally represents a
system-level actor with the most permissions. Regular human users
top out at `owner`.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Literal

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_tenant_context
from src.api.errors import ForbiddenRole, NotMemberOfTenant
from src.common.models import TenantContext
from src.memberships import PostgresMembershipRepository

__all__ = [
    "Role",
    "ROLE_ORDER",
    "role_rank",
    "get_actor_role",
    "resolve_actor_role",
    "require_role",
    "require_role_or_creator",
]


Role = Literal["viewer", "member", "admin", "owner", "service_account"]

# 5-role ladder from Turn 3 Step 0. Lowest to highest; higher index = more authority.
ROLE_ORDER: tuple[Role, ...] = (
    "viewer",
    "member",
    "admin",
    "owner",
    "service_account",
)


def role_rank(role: Role | str) -> int:
    """Return a numeric rank for comparing roles. Unknown roles get -1."""
    try:
        return ROLE_ORDER.index(role)  # type: ignore[arg-type]
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# get_actor_role — the real resolver
# ---------------------------------------------------------------------------


async def get_actor_role(
    ctx: TenantContext = Depends(get_tenant_context),
) -> Role:
    """Resolve the actor's effective role for the current request.

    FastAPI dependency: takes only `ctx`. This keeps FastAPI's
    introspector from seeing an ``AsyncSession`` parameter and
    trying to treat it as a request field (which would fail at
    route-registration time — AsyncSession isn't a Pydantic type).

    Fast-path: if `ctx.role` is already set (middleware populated it,
    or a test injected it via ``object.__setattr__``), skip the DB
    lookup and return it. Preserves backwards-compat invariant #3.

    DB path: opens its own session via `get_sessionmaker()`. Callers
    that already own a session should use ``resolve_actor_role(ctx,
    session)`` directly instead of this FastAPI-facing dep.
    """
    # Fast-path via middleware-set attribute.
    fast = getattr(ctx, "role", None)
    if fast is not None and role_rank(fast) >= 0:
        return fast  # type: ignore[return-value]

    # DB path — lazy import avoids DB import at module load.
    from src.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as owned_session:
        return await resolve_actor_role(ctx, owned_session)


async def resolve_actor_role(
    ctx: TenantContext,
    session: AsyncSession,
) -> Role:
    """Plain function (not a FastAPI dep) that does the precedence
    lookup given an already-open session.

    Use this from callers that own a session (e.g. middleware, unit
    tests with a test-scoped session, background jobs). The FastAPI
    route-layer should use ``get_actor_role`` instead.

    Precedence: workspace-level membership > tenant-level > denied.
    """
    # Fast-path (redundant with get_actor_role's check, but harmless
    # and keeps this function usable standalone).
    fast = getattr(ctx, "role", None)
    if fast is not None and role_rank(fast) >= 0:
        return fast  # type: ignore[return-value]

    repo = PostgresMembershipRepository()

    # 1. Workspace-specific membership wins.
    ws_role = await repo.get_role(
        tenant_id=ctx.tenant_id,
        user_id=ctx.actor.actor_id,
        workspace_id=ctx.workspace_id,
        session=session,
    )
    if ws_role is not None:
        return ws_role  # type: ignore[return-value]

    # 2. Tenant-level fallback.
    tenant_role = await repo.get_role(
        tenant_id=ctx.tenant_id,
        user_id=ctx.actor.actor_id,
        workspace_id=None,
        session=session,
    )
    if tenant_role is not None:
        return tenant_role  # type: ignore[return-value]

    # 3. No membership — fail-closed.
    raise NotMemberOfTenant(
        "Actor has no membership in the current workspace or tenant.",
        details={
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.workspace_id,
            "actor_id": ctx.actor.actor_id,
        },
    )


# ---------------------------------------------------------------------------
# require_role — FastAPI dependency factory
# ---------------------------------------------------------------------------


def require_role(minimum: Role) -> Callable:
    """Return a FastAPI dep that enforces `role >= minimum` on the actor.

    Usage::

        @router.post("/runs", dependencies=[Depends(require_role("member"))])
        async def create_run(...):
            ...

    The check compares ranks via ROLE_ORDER. If the actor's resolved
    role is below the minimum, raise ForbiddenRole (HTTP 403). The
    exception carries the required and actual role in ``details`` so
    audit and client code can reason about the failure.
    """
    min_rank = role_rank(minimum)
    if min_rank < 0:
        raise ValueError(f"Unknown minimum role: {minimum!r}")

    async def _checker(role: Role = Depends(get_actor_role)) -> None:
        if role_rank(role) < min_rank:
            raise ForbiddenRole(
                f"Action requires role >= {minimum!r}, actor has {role!r}.",
                details={"required_role": minimum, "actor_role": role},
            )

    return _checker


# ---------------------------------------------------------------------------
# require_role_or_creator — creator exception variant (for cancel-run)
# ---------------------------------------------------------------------------


def require_role_or_creator(
    minimum: Role,
    get_creator_id: Callable[[Request], Awaitable[str]],
) -> Callable:
    """Allow the action if actor is at `minimum` role OR is the resource creator.

    Plan v0.4 §6.6.4. Used for endpoints like ``POST /runs/{id}/cancel``
    where a lower-privilege user (``member``) should be able to cancel
    a run they themselves created, but not a run someone else created.

    The ``get_creator_id`` callable is an async function that pulls
    the creator's actor_id out of the request (typically by looking
    up the resource by path parameter). Implementations should return
    the creator's ``actor_id`` string; if the resource doesn't exist,
    they may raise whatever 404 the router expects.
    """
    min_rank = role_rank(minimum)
    if min_rank < 0:
        raise ValueError(f"Unknown minimum role: {minimum!r}")

    async def _checker(
        request: Request,
        role: Role = Depends(get_actor_role),
        ctx: TenantContext = Depends(get_tenant_context),
    ) -> None:
        if role_rank(role) >= min_rank:
            return  # sufficient role — creator check skipped

        # Below minimum role — check creator exception.
        creator_id = await get_creator_id(request)
        if creator_id == ctx.actor.actor_id:
            return  # creator exception applies

        raise ForbiddenRole(
            f"Action requires role >= {minimum!r} or creator match.",
            details={
                "required_role": minimum,
                "actor_role": role,
                "creator_exception_available": True,
            },
        )

    return _checker
