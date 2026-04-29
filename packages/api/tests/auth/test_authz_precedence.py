"""Authz precedence tests — Turn 3 Step 4 (plan v0.4 §6.6.1).

Six tests covering the workspace-over-tenant role precedence logic and
the creator-exception variant used for cancel-run:

  1. Workspace-specific role wins over tenant-level role
  2. Tenant-level role used as fallback when no workspace row
  3. No membership at either level → NotMemberOfTenant (403)
  4. Creator exception: a viewer-who-is-creator passes `admin` gate
  5. Non-creator viewer fails the same gate
  6. Admin role satisfies the gate regardless of creator match

These tests exercise the real `get_actor_role`, `require_role`, and
`require_role_or_creator` dependencies against a Postgres-backed
membership repository, so `clean_db` is required.

Test infrastructure mirrors `test_bootstrap_lifecycle.py`.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.errors import APIError, api_error_handler
from src.auth.authz import (
    get_actor_role,
    require_role,
    require_role_or_creator,
)
from src.common.models import ActorRef, TenantContext
from src.db.session import get_sessionmaker
from src.memberships import PostgresMembershipRepository
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers — set up a tenant/workspace/user with specific memberships
# ---------------------------------------------------------------------------


async def _seed_tenant_workspace(
    *, org_slug: str, user_id: str
) -> tuple[str, str]:
    """Create a fresh tenant + default workspace, return (tenant_id, workspace_id)."""
    sm = get_sessionmaker()
    tenant_repo = PostgresTenantRepository()
    workspace_repo = PostgresWorkspaceRepository()

    async with sm() as session:
        async with session.begin():
            tenant = await tenant_repo.create(
                name=f"Tenant-{org_slug}",
                slug=org_slug,
                clerk_org_id=f"org_{org_slug}",
                session=session,
            )
            workspace = await workspace_repo.create(
                tenant_id=tenant.id,
                name="Default",
                is_default=True,
                session=session,
            )
    return tenant.id, workspace.id


async def _add_membership(
    *,
    tenant_id: str,
    user_id: str,
    workspace_id: str | None,
    role: str,
) -> None:
    sm = get_sessionmaker()
    repo = PostgresMembershipRepository()
    async with sm() as session:
        async with session.begin():
            await repo.create(
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=workspace_id,
                role=role,
                session=session,
            )


def _make_app_with_injected_ctx(
    tenant_id: str, workspace_id: str, user_id: str
) -> FastAPI:
    """Build a FastAPI app that injects a specific TenantContext and
    mounts probe + gated routes. The context has NO pre-resolved `role`,
    forcing the DB path in `get_actor_role`."""
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        ctx = TenantContext(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=ActorRef(actor_id=user_id, actor_type="human"),
        )
        request.state.tenant_context = ctx
        return await call_next(request)

    @app.get("/my-role")
    async def my_role(role: str = Depends(get_actor_role)):
        return {"role": role}

    @app.get("/admin-gate", dependencies=[Depends(require_role("admin"))])
    async def admin_gate():
        return {"ok": True}

    @app.get("/member-gate", dependencies=[Depends(require_role("member"))])
    async def member_gate():
        return {"ok": True}

    # For creator-exception tests: a "cancel" route that requires admin
    # OR the caller must match the run's creator.
    async def _get_fake_creator_id(request: Request) -> str:
        # In real code this would fetch initiated_by_actor_id; for tests,
        # we encode it in a query param.
        return request.query_params.get("creator", "user_nobody")

    @app.get(
        "/cancel",
        dependencies=[
            Depends(require_role_or_creator("admin", _get_fake_creator_id))
        ],
    )
    async def cancel():
        return {"ok": True}

    return app


async def _get(app: FastAPI, path: str) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(path)
        return {"status": resp.status_code, "body": resp.json()}


# ---------------------------------------------------------------------------
# 1. Workspace role wins over tenant role
# ---------------------------------------------------------------------------


async def test_workspace_role_overrides_tenant_role(clean_db: str) -> None:
    """Tenant admin + workspace viewer → viewer in that workspace."""
    tenant_id, workspace_id = await _seed_tenant_workspace(
        org_slug="prec1", user_id="user_alice"
    )
    await _add_membership(
        tenant_id=tenant_id,
        user_id="user_alice",
        workspace_id=None,
        role="admin",
    )
    await _add_membership(
        tenant_id=tenant_id,
        user_id="user_alice",
        workspace_id=workspace_id,
        role="viewer",
    )

    app = _make_app_with_injected_ctx(tenant_id, workspace_id, "user_alice")
    result = await _get(app, "/my-role")

    assert result["status"] == 200
    assert result["body"]["role"] == "viewer", (
        "workspace-level viewer must override tenant-level admin"
    )


# ---------------------------------------------------------------------------
# 2. Tenant role used as fallback
# ---------------------------------------------------------------------------


async def test_tenant_role_used_when_no_workspace_membership(
    clean_db: str,
) -> None:
    """Tenant owner + no workspace row → owner resolves in workspace."""
    tenant_id, workspace_id = await _seed_tenant_workspace(
        org_slug="prec2", user_id="user_bob"
    )
    await _add_membership(
        tenant_id=tenant_id,
        user_id="user_bob",
        workspace_id=None,
        role="owner",
    )
    # NO workspace-level row inserted.

    app = _make_app_with_injected_ctx(tenant_id, workspace_id, "user_bob")
    result = await _get(app, "/my-role")

    assert result["status"] == 200
    assert result["body"]["role"] == "owner"


# ---------------------------------------------------------------------------
# 3. No membership at either level → NotMemberOfTenant (403)
# ---------------------------------------------------------------------------


async def test_no_membership_at_either_level_denied(clean_db: str) -> None:
    """No rows anywhere → 403 not_member_of_tenant."""
    tenant_id, workspace_id = await _seed_tenant_workspace(
        org_slug="prec3", user_id="user_carol"
    )
    # NO memberships for user_carol at all.

    app = _make_app_with_injected_ctx(tenant_id, workspace_id, "user_carol")
    result = await _get(app, "/my-role")

    assert result["status"] == 403
    assert result["body"]["error"]["code"] == "not_member_of_tenant"


# ---------------------------------------------------------------------------
# 4. Creator exception: viewer can cancel own run
# ---------------------------------------------------------------------------


async def test_creator_can_cancel_own_run_as_viewer(clean_db: str) -> None:
    """A viewer-who-is-creator passes the require_role_or_creator('admin') gate."""
    tenant_id, workspace_id = await _seed_tenant_workspace(
        org_slug="prec4", user_id="user_dave"
    )
    await _add_membership(
        tenant_id=tenant_id,
        user_id="user_dave",
        workspace_id=workspace_id,
        role="viewer",
    )

    app = _make_app_with_injected_ctx(tenant_id, workspace_id, "user_dave")
    # The fake `get_creator_id` returns the `creator` query param — match it
    # to the injected actor_id to trip the creator exception.
    result = await _get(app, "/cancel?creator=user_dave")

    assert result["status"] == 200


# ---------------------------------------------------------------------------
# 5. Non-creator viewer cannot cancel — negative case
# ---------------------------------------------------------------------------


async def test_non_creator_viewer_cannot_cancel(clean_db: str) -> None:
    """A viewer who is NOT the creator fails the gate."""
    tenant_id, workspace_id = await _seed_tenant_workspace(
        org_slug="prec5", user_id="user_eve"
    )
    await _add_membership(
        tenant_id=tenant_id,
        user_id="user_eve",
        workspace_id=workspace_id,
        role="viewer",
    )

    app = _make_app_with_injected_ctx(tenant_id, workspace_id, "user_eve")
    # Creator is someone else entirely.
    result = await _get(app, "/cancel?creator=user_different")

    assert result["status"] == 403
    assert result["body"]["error"]["code"] == "forbidden_role"


# ---------------------------------------------------------------------------
# 6. Admin can cancel any run
# ---------------------------------------------------------------------------


async def test_admin_can_cancel_any_run(clean_db: str) -> None:
    """An admin actor always passes the gate regardless of creator match."""
    tenant_id, workspace_id = await _seed_tenant_workspace(
        org_slug="prec6", user_id="user_frank"
    )
    await _add_membership(
        tenant_id=tenant_id,
        user_id="user_frank",
        workspace_id=workspace_id,
        role="admin",
    )

    app = _make_app_with_injected_ctx(tenant_id, workspace_id, "user_frank")
    # Creator is someone else — admin role alone should still pass.
    result = await _get(app, "/cancel?creator=user_someone_else")

    assert result["status"] == 200
