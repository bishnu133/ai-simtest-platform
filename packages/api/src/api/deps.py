"""Shared FastAPI dependencies (plan §6.6 + Turn 3 Step 4 wiring).

Turn 3 edit: `get_actor_role` becomes a thin wrapper that delegates to
`src.auth.authz.get_actor_role`. The public name and signature are
preserved so existing router tests and other dependencies that import
`from src.api.deps import get_actor_role` continue working unchanged.

The fast-path (read `ctx.role` if present) is preserved — it was there
in the Week 6a shipped code to enable router tests that inject a role
via `object.__setattr__(ctx, "role", ...)`, and the Turn 3 `authz`
module honors the same fast-path first. So every shipped router test
continues to pass while production traffic now flows through real
membership lookup when there's no pre-resolved role.

The Role literal extends to 5 elements per Turn 3 Step 0:
    viewer < member < admin < owner < service_account

Existing callers that import `Role` get the extended type automatically.
No string values of existing roles changed.
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, Request

from src.api.errors import ForbiddenRole, TenantContextMissing
from src.common.models import TenantContext


# ---------------------------------------------------------------------------
# Role ladder — 5-role version from Turn 3 Step 0
# ---------------------------------------------------------------------------

Role = Literal["viewer", "member", "admin", "owner", "service_account"]

# Preserved list-shape for compatibility; tests that grep this constant
# still find it. Lowest at index 0.
_ROLE_ORDER: list[Role] = [
    "viewer",
    "member",
    "admin",
    "owner",
    "service_account",
]


def _role_rank(role: Role) -> int:
    return _ROLE_ORDER.index(role)


# ---------------------------------------------------------------------------
# get_tenant_context — unchanged from Week 6a
# ---------------------------------------------------------------------------


async def get_tenant_context(request: Request) -> TenantContext:
    """Extract the TenantContext set by middleware (or test override).

    Fail-closed: if no context is present, raise TenantContextMissing (401).
    """
    ctx = getattr(request.state, "tenant_context", None)
    if ctx is None:
        raise TenantContextMissing("No tenant context on request")
    return ctx


# ---------------------------------------------------------------------------
# get_actor_role — delegates to the real resolver in src.auth.authz
# ---------------------------------------------------------------------------
#
# The import is intentionally lazy (inside the function body) to avoid a
# circular import at module load time: src.auth.authz imports get_tenant_context
# from this module.
# ---------------------------------------------------------------------------


async def get_actor_role(
    ctx: TenantContext = Depends(get_tenant_context),
) -> Role:
    """Resolve the actor's effective role.

    Fast-path: if the middleware (or a test) has attached a `role`
    attribute to the context via ``object.__setattr__(ctx, "role", ...)``,
    use it directly. This covers:

    * Production requests after TenantContextMiddleware runs bootstrap
      and caches the resolved role on the ctx.
    * Router tests that bypass middleware entirely and inject a role
      for unit-testing authz gates. The Week 6a test pattern.

    Otherwise delegate to `src.auth.authz.get_actor_role` which hits the
    Postgres membership repository. The authz function opens its own
    session via `get_sessionmaker()` when the DB path is needed, so
    this delegating dependency doesn't resolve any session dep eagerly.
    """
    fast = getattr(ctx, "role", None)
    if fast is not None:
        if fast not in _ROLE_ORDER:
            raise ForbiddenRole(
                f"Unknown role '{fast}' on tenant context",
                details={"role": fast},
            )
        return fast  # type: ignore[return-value]

    # Production path — delegate to the real resolver.
    # Local import avoids circular dependency (authz imports get_tenant_context).
    from src.auth.authz import get_actor_role as _real_get_actor_role

    return await _real_get_actor_role(ctx=ctx)


# ---------------------------------------------------------------------------
# require_role — unchanged from Week 6a (uses the delegating get_actor_role)
# ---------------------------------------------------------------------------


def require_role(minimum: Role):
    """Dependency factory: gate an endpoint at a minimum role.

    Usage:
        @router.get("/...", dependencies=[Depends(require_role("admin"))])
    """
    minimum_rank = _role_rank(minimum)

    async def _checker(role: Role = Depends(get_actor_role)) -> None:
        if _role_rank(role) < minimum_rank:
            raise ForbiddenRole(
                f"Action requires role >= {minimum}, actor has '{role}'",
                details={"required_role": minimum, "actor_role": role},
            )

    return _checker


# ---------------------------------------------------------------------------
# check_entitlement — unchanged from Week 6a (no-op, wired to engine later)
# ---------------------------------------------------------------------------


def check_entitlement(action: str):
    """Entitlement gate (no-op in Week 6a, wired to EntitlementEngine later).

    Preserved as a pass-through to avoid breaking router signatures that
    already depend on it.
    """

    async def _checker() -> None:
        return None

    return _checker
