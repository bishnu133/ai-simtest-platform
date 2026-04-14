"""Shared FastAPI dependencies for Week 6a routers (v1.2.2 §11.4).

One canonical pattern for tenant context extraction, role gating, and
entitlement checks. Routers in `runs/`, `conversations/`, `results/`, and
`comparisons/` all import from here so the auth surface is consistent.

The auth middleware itself is deferred — for Week 6a, tests inject
`request.state.tenant_context` directly via FastAPI dependency override,
matching the assets router pattern from Week 5.

Roles (Week 6a §11.4 matrix):
    viewer < member < admin < service_account
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, Request

from src.api.errors import ForbiddenRole, TenantContextMissing
from src.common.models import TenantContext


Role = Literal["viewer", "member", "admin", "service_account"]

# Role hierarchy: index 0 is lowest privilege.
_ROLE_ORDER: list[Role] = ["viewer", "member", "admin", "service_account"]


def _role_rank(role: Role) -> int:
    return _ROLE_ORDER.index(role)


async def get_tenant_context(request: Request) -> TenantContext:
    """Extract the TenantContext set by middleware (or test override).

    Fail-closed: if no context is present, raise TenantContextMissing (401).
    """
    ctx = getattr(request.state, "tenant_context", None)
    if ctx is None:
        raise TenantContextMissing("No tenant context on request")
    return ctx


def get_actor_role(ctx: TenantContext = Depends(get_tenant_context)) -> Role:
    """Resolve the actor's role for the current workspace.

    For Week 6a, role is read from a `role` attribute on the test-injected
    context if present, defaulting to 'member'. Production wiring resolves
    this via membership lookup; the dependency shape doesn't change.
    """
    role = getattr(ctx, "role", None) or "member"
    if role not in _ROLE_ORDER:
        raise ForbiddenRole(f"Unknown role '{role}'")
    return role  # type: ignore[return-value]


def require_role(minimum: Role):
    """Dependency factory: gate an endpoint at a minimum role.

    Usage:
        @router.get("/...", dependencies=[Depends(require_role("admin"))])
    """
    minimum_rank = _role_rank(minimum)

    def _checker(role: Role = Depends(get_actor_role)) -> None:
        if _role_rank(role) < minimum_rank:
            raise ForbiddenRole(
                f"Action requires role >= {minimum}, actor has '{role}'",
                details={"required_role": minimum, "actor_role": role},
            )

    return _checker


def check_entitlement(action: str):
    """Entitlement gate (no-op in Week 6a, wired to EntitlementEngine later).

    The dependency exists so future enforcement is mechanical:
        @router.post("/...", dependencies=[Depends(check_entitlement("comparison.create"))])
    """

    def _noop(ctx: TenantContext = Depends(get_tenant_context)) -> None:
        return None

    return _noop
