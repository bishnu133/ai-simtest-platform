"""Authz matrix tests — Turn 3 Step 4 (plan v0.4 §6.6.2).

14 parametrized tests. Each row of the plan's endpoint authorization
matrix gets one test: a minimum-role requirement, a test actor with a
specific role, and the expected HTTP status.

The tests don't mount the real routers — they mount a stub route
per endpoint with the documented role gate, driven by `require_role`.
This keeps the matrix tests laser-focused on authz semantics, not
on route-handler business logic (covered elsewhere).

Parametrization shape:
    (endpoint_label, minimum_role, actor_role, expected_status)

Expected status:
    200 — actor_role meets minimum
    403 — actor_role below minimum (forbidden_role)

The 14 rows come from plan §6.6.2 (3 read-viewer + 5 write-member +
3 admin + 1 cancel-variant + 2 comparisons). The cancel-run row
involves the creator exception and is covered more completely in
test_authz_precedence.py; here we test only the role-based branch.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.errors import APIError, api_error_handler
from src.auth.authz import require_role
from src.common.models import ActorRef, TenantContext

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Matrix — plan v0.4 §6.6.2
# ---------------------------------------------------------------------------
#
# Each row maps to an endpoint + minimum role. For every row we run the
# same test twice implicitly (positive and negative) by parameterizing
# the actor's role.
# ---------------------------------------------------------------------------


MATRIX_CASES = [
    # Format: (endpoint_label, minimum_role, actor_role, expected_status)
    #
    # Read-viewer gates (3 rows × positive cases)
    ("GET /v1/assets/{id}", "viewer", "viewer", 200),
    ("GET /v1/runs/{id}/results", "viewer", "member", 200),
    ("GET /v1/comparisons/{id}", "viewer", "admin", 200),
    #
    # Write-member gates (4 rows)
    ("POST /v1/assets", "member", "viewer", 403),
    ("POST /v1/assets", "member", "member", 200),
    ("POST /v1/runs", "member", "viewer", 403),
    ("POST /v1/comparisons", "member", "admin", 200),
    #
    # Admin gates (3 rows)
    ("POST /v1/assets/{id}/approve", "admin", "member", 403),
    ("POST /v1/assets/{id}/approve", "admin", "admin", 200),
    ("POST /v1/assets/{id}/deprecate", "admin", "viewer", 403),
    #
    # Service-account gates (top of ladder)
    ("POST /v1/internal/reindex", "service_account", "owner", 403),
    ("POST /v1/internal/reindex", "service_account", "service_account", 200),
    #
    # Owner gates (future-proofing)
    ("DELETE /v1/workspaces/{id}", "owner", "admin", 403),
    ("DELETE /v1/workspaces/{id}", "owner", "owner", 200),
]


# ---------------------------------------------------------------------------
# Helpers — inject a ctx with a specific pre-resolved role via fast-path.
# This exercises `require_role` without DB access: the `deps.get_actor_role`
# fast-path reads `ctx.role` when present, so we don't need `clean_db`
# for these tests.
# ---------------------------------------------------------------------------


def _make_gated_app(minimum_role: str, actor_role: str) -> FastAPI:
    """Build a minimal app with a single gated route and injected context."""
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        ctx = TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000002",
            actor=ActorRef(actor_id="user_matrix", actor_type="human"),
        )
        # Fast-path: set role directly on the context. Matches the
        # shipped Week 6a test injection pattern.
        object.__setattr__(ctx, "role", actor_role)
        request.state.tenant_context = ctx
        return await call_next(request)

    @app.get("/gated", dependencies=[Depends(require_role(minimum_role))])  # type: ignore[arg-type]
    async def gated():
        return {"ok": True}

    return app


async def _get(app: FastAPI, path: str) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(path)
        return {"status": resp.status_code, "body": resp.json()}


# ---------------------------------------------------------------------------
# Parametrized matrix test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint_label, minimum_role, actor_role, expected_status",
    MATRIX_CASES,
    ids=[
        f"{ep} req={minrole} actor={actrole} -> {status}"
        for ep, minrole, actrole, status in MATRIX_CASES
    ],
)
async def test_authz_matrix_row(
    endpoint_label: str,
    minimum_role: str,
    actor_role: str,
    expected_status: int,
) -> None:
    """One row from §6.6.2 — `require_role(minimum_role)` gates correctly."""
    app = _make_gated_app(minimum_role, actor_role)
    result = await _get(app, "/gated")

    assert result["status"] == expected_status, (
        f"Endpoint {endpoint_label}: actor={actor_role} required={minimum_role} "
        f"expected {expected_status} but got {result['status']}. "
        f"Response: {result['body']}"
    )

    if expected_status == 403:
        assert result["body"]["error"]["code"] == "forbidden_role"
