"""Week 6a Turn 1: shared API dependencies tests (v1.2.2 §11.4)."""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.deps import (
    check_entitlement,
    get_actor_role,
    get_tenant_context,
    require_role,
)
from src.api.errors import APIError, api_error_handler
from src.common.models import ActorRef, TenantContext


@pytest.fixture
def actor() -> ActorRef:
    return ActorRef(actor_id="u1", actor_type="human")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    return app


def test_get_tenant_context_returns_state_value(actor: ActorRef):
    app = _make_app()

    @app.get("/whoami")
    async def whoami(ctx: TenantContext = Depends(get_tenant_context)):
        return {"tenant_id": ctx.tenant_id}

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="t1", workspace_id="w1", actor=actor
        )
        return await call_next(request)

    client = TestClient(app)
    resp = client.get("/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"tenant_id": "t1"}


def test_get_tenant_context_missing_returns_401():
    app = _make_app()

    @app.get("/whoami")
    async def whoami(ctx: TenantContext = Depends(get_tenant_context)):
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/whoami")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "tenant_context_missing"


def test_require_role_blocks_lower_privilege(actor: ActorRef):
    app = _make_app()

    @app.get("/admin", dependencies=[Depends(require_role("admin"))])
    async def admin_only():
        return {"ok": True}

    @app.middleware("http")
    async def inject(request, call_next):
        ctx = TenantContext(tenant_id="t1", workspace_id="w1", actor=actor)
        # Attach role attr — Pydantic v2 frozen=False on TenantContext allows this
        object.__setattr__(ctx, "role", "viewer")
        request.state.tenant_context = ctx
        return await call_next(request)

    client = TestClient(app)
    resp = client.get("/admin")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "forbidden_role"
    assert body["error"]["details"]["required_role"] == "admin"
    assert body["error"]["details"]["actor_role"] == "viewer"


def test_require_role_allows_equal_or_higher(actor: ActorRef):
    app = _make_app()

    @app.get("/member", dependencies=[Depends(require_role("member"))])
    async def member_plus():
        return {"ok": True}

    @app.middleware("http")
    async def inject(request, call_next):
        ctx = TenantContext(tenant_id="t1", workspace_id="w1", actor=actor)
        object.__setattr__(ctx, "role", "admin")
        request.state.tenant_context = ctx
        return await call_next(request)

    client = TestClient(app)
    resp = client.get("/member")
    assert resp.status_code == 200
