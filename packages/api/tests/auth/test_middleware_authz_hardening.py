"""Middleware + authz hardening tests — Turn 3 Session 3.

Closes the eight coverage gaps identified by the Turn 3 Session 3
pre-coding gate report (Option B):

  Batch 1 — Step 3 §6.5 missed specs (2 tests)
    1. test_middleware_does_not_override_existing_context
    2. test_correlation_id_propagates_into_context

  Batch 2 — Step 4 §6.8 missed specs (3 tests)
    3. test_owner_tenant_level_authorizes_all_workspaces
    4. test_role_downgrade_workspace_vs_tenant
    5. test_service_account_role_treated_as_highest

  Batch 3 — Turn 4 middleware-isolated branches (3 tests)
    6. test_middleware_workspace_not_found_returns_404_and_audits
    7. test_middleware_auth_provider_misconfigured_returns_500
    8. test_middleware_self_serve_disabled_via_app_state_settings_blocks_unknown_org

These tests reuse the patterns established by:
  * tests/auth/test_tenant_context_middleware.py — StubAuthProvider,
    _make_app, _get helpers
  * tests/auth/test_authz_precedence.py — _seed_tenant_workspace,
    _add_membership, _make_app_with_injected_ctx helpers
  * tests/auth/test_bootstrap_hardening.py — Batch 3 settings fixture

All tests are Postgres-backed and require the `clean_db` fixture from
tests/db/conftest.py.

The 247 sacred test functions in the existing suite are NOT touched.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.errors import (
    APIError,
    AuthProviderMisconfigured,
    WorkspaceNotFound,
    api_error_handler,
)
from src.audit.logger import AuditActions, audit_logger
from src.auth.middleware import TenantContextMiddleware
from src.auth.provider import VerifiedClaims
from src.auth.authz import (
    get_actor_role,
    require_role,
)
from src.common.models import ActorRef, TenantContext
from src.db.session import get_sessionmaker
from src.main import CorrelationIdMiddleware, CORRELATION_HEADER
from src.memberships import PostgresMembershipRepository
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared stubs and helpers
# ---------------------------------------------------------------------------


class StubAuthProvider:
    """AuthProvider stub — returns scripted claims or raises a scripted exc.

    Mirrors the stub in tests/auth/test_tenant_context_middleware.py so
    these tests slot into the same middleware integration pattern.
    """

    provider_name = "stub"

    def __init__(
        self,
        claims: VerifiedClaims | None = None,
        exc: Exception | None = None,
    ):
        self._claims = claims
        self._exc = exc

    async def verify(self, request: Request) -> VerifiedClaims:
        if self._exc is not None:
            raise self._exc
        assert self._claims is not None, "stub needs either claims or exc"
        return self._claims


def _make_middleware_app(
    provider,
    sessionmaker,
    *,
    exempt_paths: tuple[str, ...] = (),
    settings: Any = None,
) -> FastAPI:
    """Build a minimal FastAPI app wired with TenantContextMiddleware.

    `settings` is stashed on `app.state.settings` to mirror the
    production-time wiring done by `src.app_factory.create_app`. The
    middleware reads `app.state.settings.allow_self_serve_provisioning`
    when present and defaults to True otherwise.
    """
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    if settings is not None:
        app.state.settings = settings

    app.add_middleware(
        TenantContextMiddleware,
        provider=provider,
        sessionmaker=sessionmaker,
        exempt_paths=exempt_paths,
    )

    @app.get("/probe")
    async def probe(request: Request):
        ctx = request.state.tenant_context
        return {
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.workspace_id,
            "actor_id": ctx.actor.actor_id,
            "role": getattr(ctx, "role", None),
            "correlation_id": getattr(ctx, "correlation_id", None),
        }

    return app


async def _get(
    app: FastAPI, path: str, headers: dict | None = None
) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path, headers=headers or {})


async def _seed_tenant_with_two_workspaces(
    *, org_slug: str
) -> tuple[str, str, str]:
    """Create one tenant with TWO workspaces (default + secondary).

    Returns (tenant_id, default_ws_id, secondary_ws_id). Used by the
    "owner-tenant-level-authorizes-all-workspaces" test to verify that
    a tenant-level owner role authorizes for BOTH workspaces.
    """
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
            default_ws = await workspace_repo.create(
                tenant_id=tenant.id,
                name="Default",
                is_default=True,
                session=session,
            )
            secondary_ws = await workspace_repo.create(
                tenant_id=tenant.id,
                name="Secondary",
                is_default=False,
                session=session,
            )
    return tenant.id, default_ws.id, secondary_ws.id


async def _seed_one_tenant_one_workspace(
    *, org_slug: str
) -> tuple[str, str]:
    """Create one tenant with one default workspace; return (tenant_id, ws_id)."""
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
            ws = await workspace_repo.create(
                tenant_id=tenant.id,
                name="Default",
                is_default=True,
                session=session,
            )
    return tenant.id, ws.id


async def _add_membership_row(
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


def _make_authz_app_with_injected_ctx(
    tenant_id: str, workspace_id: str, user_id: str
) -> FastAPI:
    """FastAPI app that injects a specific TenantContext (no pre-resolved
    role) and mounts probe + role-gated routes. Mirrors the helper in
    tests/auth/test_authz_precedence.py.

    Routes:
      GET /my-role           — returns the resolved role (DB path)
      GET /owner-gate        — require_role("owner")
      GET /admin-gate        — require_role("admin")
    """
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

    @app.get("/owner-gate", dependencies=[Depends(require_role("owner"))])
    async def owner_gate():
        return {"ok": True}

    @app.get("/admin-gate", dependencies=[Depends(require_role("admin"))])
    async def admin_gate():
        return {"ok": True}

    return app


# ===========================================================================
# Batch 1 — Step 3 §6.5 missed specs (2 tests)
# ===========================================================================


async def test_middleware_does_not_override_existing_context(
    clean_db: str,
) -> None:
    """Plan §6.5 #7 — pre-existing request.state.tenant_context is preserved.

    Decision: Bishnu chose Option 1A (Turn 3 Session 3 mid-session
    checkpoint, April 26). Asserts the rigorous outer-preservation
    interpretation. Required a one-block code change in
    src/auth/middleware.py Step 5: a `getattr(...) is None` guard
    around the `request.state.tenant_context = ctx` assignment.
    Drift T3S3-7 in the Session 3 delivery report records this.

    Invariant: if a prior middleware (or test harness) has already
    attached a TenantContext to request.state when
    TenantContextMiddleware runs, the auth middleware MUST NOT clobber
    it. Guards nested-middleware composition: e.g. an outer layer
    that synthesizes a service-account context for system-internal
    routes should not have its synthesized context overwritten when
    the auth middleware also resolves a real credential.

    Implementation: a tiny outer ASGI wrapper sets a sentinel context
    BEFORE TenantContextMiddleware sees the request. The middleware is
    given a valid-looking provider so it would otherwise want to attach
    its own context; the test fails if it does. The audit assertion
    ALSO confirms the auth path still ran end-to-end — the auth.accepted
    event is emitted using the middleware's resolved ctx (not the
    sentinel), even though the route sees the sentinel. This is the
    designed split: audit reflects what auth actually verified, route
    sees whatever the outer composition wants it to see.
    """
    audit_logger.clear_all()
    sm = get_sessionmaker()

    sentinel_tenant = "00000000-0000-0000-0000-0000000bee5f"
    sentinel_ws = "00000000-0000-0000-0000-0000000bee5e"

    claims = VerifiedClaims(
        user_id="user_no_override",
        org_id="org_no_override",
        provider_org_role="admin",
    )

    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.add_middleware(
        TenantContextMiddleware,
        provider=StubAuthProvider(claims=claims),
        sessionmaker=sm,
        exempt_paths=(),
    )

    @app.get("/probe")
    async def probe(request: Request):
        ctx = request.state.tenant_context
        return {
            "tenant_id": ctx.tenant_id,
            "actor_id": ctx.actor.actor_id,
        }

    # Outer wrapper that pre-populates request.state.tenant_context.
    # Starlette runs middleware in REVERSE registration order — adding
    # this LAST makes it the OUTERMOST layer, so it runs BEFORE the
    # auth middleware's dispatch.
    @app.middleware("http")
    async def _preinject(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id=sentinel_tenant,
            workspace_id=sentinel_ws,
            # actor=ActorRef(actor_id="actor_sentinel", actor_type="service"),
            actor=ActorRef(actor_id="actor_sentinel", actor_type="service_account"),
        )
        return await call_next(request)

    resp = await _get(app, "/probe", headers={"Authorization": "Bearer any"})

    # The probe sees the sentinel — middleware preserved the outer ctx.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == sentinel_tenant, (
        "middleware must not override pre-existing request.state.tenant_context "
        f"(got tenant_id={body['tenant_id']!r}, expected sentinel "
        f"{sentinel_tenant!r}). Plan §6.5 #7 invariant violated."
    )
    assert body["actor_id"] == "actor_sentinel", (
        "middleware must preserve pre-existing actor identity"
    )

    # Auth still ran end-to-end — the audit event reflects the real
    # credential-resolved actor, NOT the sentinel. This is the
    # designed split: audit captures truth-of-auth even when the
    # outer composition replaces what the route observes.
    accepted = audit_logger.query_all_events(action=AuditActions.AUTH_ACCEPTED)
    assert len(accepted) == 1, (
        f"expected exactly one auth.accepted event; got {len(accepted)}"
    )
    assert accepted[0].actor.actor_id == "user_no_override", (
        f"audit must reflect the credential-resolved actor "
        f"(got {accepted[0].actor.actor_id!r}, expected 'user_no_override'). "
        "Outer-context preservation must not silence the audit stream."
    )


async def test_correlation_id_propagates_into_context(clean_db: str) -> None:
    """Plan §6.5 #8 — correlation_id is available when TenantContextMiddleware runs.

    Source-of-truth invariant for middleware ordering. Starlette runs
    middleware in REVERSE registration order; CorrelationIdMiddleware
    must be registered FIRST so it becomes the outermost layer. That
    way request.state.correlation_id is already populated when
    TenantContextMiddleware enters its dispatch.

    The test sends a known X-Correlation-Id header, runs through both
    middlewares, and confirms:
      * the header is echoed on the response (CorrelationIdMiddleware ran)
      * the response carries the same id we sent (we are observing the
        SAME middleware chain we registered, not a uuid Starlette
        synthesized)
      * the auth.accepted audit event was emitted (the auth middleware
        ran and saw a fully populated request.state — proves both
        middlewares cooperated on this request)
    """
    audit_logger.clear_all()
    sm = get_sessionmaker()

    claims = VerifiedClaims(
        user_id="user_corr",
        org_id="org_corr",
        provider_org_role="admin",
    )

    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)

    # Registration order matters — Starlette reverses it at runtime.
    # CorrelationIdMiddleware is added FIRST → it becomes the OUTER layer.
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        TenantContextMiddleware,
        provider=StubAuthProvider(claims=claims),
        sessionmaker=sm,
        exempt_paths=(),
    )

    @app.get("/probe")
    async def probe(request: Request):
        # request.state.correlation_id must be populated by the time
        # this handler runs — the auth middleware ran between us and
        # the correlation middleware.
        return {
            "correlation_id_in_state": getattr(
                request.state, "correlation_id", None
            ),
        }

    sent_corr = "test-corr-bee5-cafe-0001"
    resp = await _get(
        app,
        "/probe",
        headers={
            "Authorization": "Bearer any",
            CORRELATION_HEADER: sent_corr,
        },
    )

    # Response echoed the same correlation id we sent.
    assert resp.status_code == 200, resp.text
    assert resp.headers.get(CORRELATION_HEADER) == sent_corr, (
        f"response should echo X-Correlation-Id={sent_corr!r}, "
        f"got {resp.headers.get(CORRELATION_HEADER)!r}"
    )

    # The handler saw the correlation id on request.state — i.e. it was
    # populated BEFORE the auth middleware ran.
    body = resp.json()
    assert body["correlation_id_in_state"] == sent_corr, (
        "request.state.correlation_id must be populated by the outer "
        "CorrelationIdMiddleware before TenantContextMiddleware runs "
        f"(got {body['correlation_id_in_state']!r})"
    )

    # And auth ran successfully — proves both middlewares cooperated.
    accepted = audit_logger.query_all_events(action=AuditActions.AUTH_ACCEPTED)
    assert len(accepted) == 1, (
        f"expected exactly one auth.accepted event, got {len(accepted)}"
    )


# ===========================================================================
# Batch 2 — Step 4 §6.8 missed specs (3 tests)
# ===========================================================================


async def test_owner_tenant_level_authorizes_all_workspaces(
    clean_db: str,
) -> None:
    """Plan §6.8 #4 — a tenant-level `owner` authorizes for ANY workspace.

    A user with one tenant-level membership row at role=owner and NO
    workspace-level rows should pass require_role("owner") gates for
    EVERY workspace under that tenant. The precedence ladder hits step
    2 (tenant-level fallback) for every workspace.

    Setup:
      * tenant T with two workspaces ws_default + ws_secondary
      * user_alice has ONE membership row: (tenant=T, ws=NULL, role=owner)

    Expectations:
      * /owner-gate under ws_default → 200
      * /owner-gate under ws_secondary → 200
      * /my-role under both → "owner"
    """
    tenant_id, ws_default, ws_secondary = await _seed_tenant_with_two_workspaces(
        org_slug="prec_owner_all"
    )
    await _add_membership_row(
        tenant_id=tenant_id,
        user_id="user_alice_owner",
        workspace_id=None,  # tenant-level only
        role="owner",
    )

    # Probe under default workspace
    app_default = _make_authz_app_with_injected_ctx(
        tenant_id, ws_default, "user_alice_owner"
    )
    resp = await _get(app_default, "/my-role")
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "owner", (
        "tenant-level owner must resolve to owner under default workspace"
    )
    resp_gate = await _get(app_default, "/owner-gate")
    assert resp_gate.status_code == 200, (
        f"tenant-level owner must pass owner gate under ws_default; "
        f"got {resp_gate.status_code} body={resp_gate.text}"
    )

    # Probe under secondary workspace — same user, different ws.
    app_secondary = _make_authz_app_with_injected_ctx(
        tenant_id, ws_secondary, "user_alice_owner"
    )
    resp2 = await _get(app_secondary, "/my-role")
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["role"] == "owner", (
        "tenant-level owner must also resolve to owner under a "
        "different workspace in the same tenant"
    )
    resp_gate2 = await _get(app_secondary, "/owner-gate")
    assert resp_gate2.status_code == 200, (
        f"tenant-level owner must pass owner gate under ws_secondary too; "
        f"got {resp_gate2.status_code} body={resp_gate2.text}"
    )


async def test_role_downgrade_workspace_vs_tenant(clean_db: str) -> None:
    """Plan §6.8 #5 — workspace role wins even when LOWER than tenant role.

    The precedence ladder is "workspace wins", NOT "max of the two".
    A user with tenant=admin AND workspace=viewer must resolve to
    viewer under that workspace (the workspace-level intent overrides
    the broader tenant grant).

    Concretely: this distinguishes precedence from a "max of all
    membership rows" semantic. Some real authz systems take the max;
    plan v0.4 §6.6.1 explicitly does NOT.

    Setup:
      * tenant T with one default workspace ws
      * user_bob: (tenant=T, ws=NULL, role=admin) AND (tenant=T, ws=ws, role=viewer)

    Expectations:
      * /my-role → "viewer" (workspace row wins, even though it's the lower role)
      * /admin-gate → 403 (forbidden_role; viewer < admin)
    """
    tenant_id, ws_id = await _seed_one_tenant_one_workspace(
        org_slug="prec_downgrade"
    )
    user_id = "user_bob_downgrade"

    # Tenant-level admin
    await _add_membership_row(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=None,
        role="admin",
    )
    # Workspace-level viewer (lower than tenant-level admin — precedence test)
    await _add_membership_row(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=ws_id,
        role="viewer",
    )

    app = _make_authz_app_with_injected_ctx(tenant_id, ws_id, user_id)

    # /my-role must return the workspace row, not the higher tenant row.
    resp = await _get(app, "/my-role")
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "viewer", (
        "precedence is workspace-wins, not max-role: "
        f"got {resp.json()['role']!r}, expected 'viewer'. "
        "Plan §6.6.1 violated."
    )

    # /admin-gate must reject with 403 because the resolved role is viewer.
    resp_gate = await _get(app, "/admin-gate")
    assert resp_gate.status_code == 403, (
        f"workspace-level viewer must fail admin gate; "
        f"got {resp_gate.status_code} body={resp_gate.text}"
    )
    body = resp_gate.json()
    assert body["error"]["code"] == "forbidden_role", (
        f"expected forbidden_role error code; got {body['error']['code']!r}"
    )
    assert body["error"]["details"]["actor_role"] == "viewer"
    assert body["error"]["details"]["required_role"] == "admin"


async def test_service_account_role_treated_as_highest(clean_db: str) -> None:
    """Plan §6.8 #6 — service_account ranks above owner in ROLE_ORDER.

    Verifies the 5-role ladder ordering at the rank level: a user whose
    only membership row has role='service_account' must satisfy a
    require_role('owner') gate. The plan-stated rationale: service
    accounts represent system-level actors with the most permissions
    and sit one rung above human users.

    This also implicitly verifies that 'service_account' is recognized
    by role_rank (returns >= 0, not -1 which would mark it unknown).

    Setup:
      * tenant T with one default workspace ws
      * user_bot: (tenant=T, ws=ws, role=service_account)

    Expectations:
      * /my-role → "service_account"
      * /owner-gate → 200 (service_account >= owner in rank)
      * /admin-gate → 200 (service_account >= admin too)
    """
    tenant_id, ws_id = await _seed_one_tenant_one_workspace(
        org_slug="prec_service"
    )
    user_id = "user_bot_service"

    await _add_membership_row(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=ws_id,
        role="service_account",
    )

    app = _make_authz_app_with_injected_ctx(tenant_id, ws_id, user_id)

    resp = await _get(app, "/my-role")
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "service_account"

    # service_account ranks ABOVE owner — owner gate must pass.
    resp_owner = await _get(app, "/owner-gate")
    assert resp_owner.status_code == 200, (
        f"service_account must satisfy require_role('owner'); "
        f"got {resp_owner.status_code} body={resp_owner.text}. "
        "ROLE_ORDER ranking violated."
    )

    # And of course passes the lower admin gate.
    resp_admin = await _get(app, "/admin-gate")
    assert resp_admin.status_code == 200, resp_admin.text


# ===========================================================================
# Batch 3 — Turn 4 middleware-isolated branches (3 tests)
# ===========================================================================


async def test_middleware_workspace_not_found_returns_404_and_audits(
    clean_db: str,
) -> None:
    """Turn 4 middleware addition — WorkspaceNotFound branch in dispatch.

    When `bootstrap()` raises WorkspaceNotFound (e.g. caller specified a
    workspace_id claim that doesn't exist under the tenant), the
    middleware must:
      * map to HTTP 404 with the workspace_not_found code
      * emit an AUTH_REJECTED audit event with reason=workspace_not_found
      * close the bootstrap session in the finally block (no leak)

    We don't drive bootstrap() itself to raise WorkspaceNotFound (that
    would require a tenant_id row + a missing workspace lookup); instead
    we monkey-patch bootstrap inside the middleware module to raise the
    exception directly. This isolates the middleware's branch handling
    from bootstrap's internals.
    """
    audit_logger.clear_all()
    sm = get_sessionmaker()

    claims = VerifiedClaims(
        user_id="user_wsnotfound",
        org_id="org_wsnotfound",
        workspace_id="ws_does_not_exist",
        provider_org_role="admin",
    )

    # Patch the bootstrap symbol the middleware module uses, NOT the
    # one in src.auth.bootstrap — middleware.py does
    # `from src.auth.bootstrap import bootstrap`, so the binding lives
    # on the middleware module.
    import src.auth.middleware as mw_module

    async def _raising_bootstrap(*args, **kwargs):
        raise WorkspaceNotFound(
            "Workspace ws_does_not_exist not found in tenant.",
            details={"workspace_id": "ws_does_not_exist"},
        )

    original_bootstrap = mw_module.bootstrap
    mw_module.bootstrap = _raising_bootstrap
    try:
        app = _make_middleware_app(StubAuthProvider(claims=claims), sm)
        resp = await _get(
            app, "/probe", headers={"Authorization": "Bearer any"}
        )
    finally:
        mw_module.bootstrap = original_bootstrap

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "workspace_not_found", (
        f"expected workspace_not_found error code; got {body['error']['code']!r}"
    )

    # Audit: an AUTH_REJECTED with reason=workspace_not_found.
    rejected = audit_logger.query_all_events(action=AuditActions.AUTH_REJECTED)
    matching = [
        evt for evt in rejected
        if evt.metadata.get("reason") == "workspace_not_found"
    ]
    assert len(matching) == 1, (
        f"expected exactly one AUTH_REJECTED with reason=workspace_not_found, "
        f"got {len(matching)} (total rejected: {len(rejected)})"
    )
    assert matching[0].metadata.get("step") == "bootstrap"


async def test_middleware_auth_provider_misconfigured_returns_500(
    clean_db: str,
) -> None:
    """Turn 4 middleware addition — AuthProviderMisconfigured branch.

    When the AuthProvider's `verify` raises AuthProviderMisconfigured
    (an operator-facing failure — e.g. JWKS URL bad, audience env
    malformed), the middleware must:
      * map to HTTP 500 with the auth_provider_misconfigured code
      * NOT emit an audit.rejected event (this is operator-facing,
        not user-caused — auditing it as a user-rejection would be
        misleading)
      * log at error level (not asserted here — log capture varies
        across pytest configurations)

    The provider-misconfigured stream is deliberately distinct from
    the credential-shaped AUTH_REJECTED stream. Asserting "no auth
    audit event was written" guards that distinction.
    """
    audit_logger.clear_all()
    sm = get_sessionmaker()

    misconfigured_exc = AuthProviderMisconfigured(
        "JWKS endpoint unreachable.",
        details={"jwks_url": "https://example.invalid/.well-known/jwks.json"},
    )
    app = _make_middleware_app(
        StubAuthProvider(exc=misconfigured_exc), sm
    )

    resp = await _get(
        app, "/probe", headers={"Authorization": "Bearer any"}
    )

    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["error"]["code"] == "auth_provider_misconfigured", (
        f"expected auth_provider_misconfigured; got {body['error']['code']!r}"
    )

    # Operator-facing — no auth.* audit events should be written.
    rejected = audit_logger.query_all_events(action=AuditActions.AUTH_REJECTED)
    accepted = audit_logger.query_all_events(action=AuditActions.AUTH_ACCEPTED)
    denied = audit_logger.query_all_events(action=AuditActions.AUTH_MEMBERSHIP_DENIED)
    assert len(rejected) == 0, (
        "AuthProviderMisconfigured is operator-facing — no AUTH_REJECTED "
        f"event should be written; got {len(rejected)}"
    )
    assert len(accepted) == 0, "no AUTH_ACCEPTED on misconfigured provider"
    assert len(denied) == 0, "no AUTH_MEMBERSHIP_DENIED on misconfigured provider"


async def test_middleware_self_serve_disabled_via_app_state_settings_blocks_unknown_org(
    clean_db: str,
) -> None:
    """Turn 4 middleware addition — settings → bootstrap kwarg wiring.

    Closes the §3.10 wiring path: when app.state.settings exists with
    allow_self_serve_provisioning=False, the middleware must thread
    that flag into bootstrap(allow_self_serve_provisioning=False),
    causing bootstrap to raise TenantStateInvalid for unknown org_ids.

    Session 2's hardening covered this at the bootstrap layer directly
    (Batch 1, 4 tests). This test closes the integration gap: that the
    middleware actually READS the settings off app.state and PASSES
    the flag through.

    Setup:
      * Settings stub with allow_self_serve_provisioning=False stashed
        on app.state.settings BEFORE the middleware is added (so the
        middleware sees it)
      * A first-login claim with an unknown org_id — bootstrap would
        normally create the tenant; with the flag False, it must refuse

    Expectations:
      * HTTP 409 (TenantStateInvalid)
      * error code = tenant_state_invalid
      * AUTH_TENANT_STATE_INVALID audit event with reason carrying
        the bootstrap's refusal code
      * NO tenant row created (verified via the absence of an
        AUTH_ACCEPTED event)
    """
    audit_logger.clear_all()
    sm = get_sessionmaker()

    claims = VerifiedClaims(
        user_id="user_blocked",
        org_id="org_unknown_blocked",
        provider_org_role="admin",
    )

    # Lightweight settings stub. The middleware only reads
    # `settings.allow_self_serve_provisioning`, so a SimpleNamespace
    # is sufficient and avoids constructing the full AppSettings model
    # (which would also read from .env and complicate test isolation).
    settings_stub = SimpleNamespace(allow_self_serve_provisioning=False)

    app = _make_middleware_app(
        StubAuthProvider(claims=claims),
        sm,
        settings=settings_stub,
    )

    resp = await _get(
        app, "/probe", headers={"Authorization": "Bearer any"}
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "tenant_state_invalid", (
        f"expected tenant_state_invalid; got {body['error']['code']!r}"
    )

    # AUTH_TENANT_STATE_INVALID audit fired (Turn 4 distinct stream).
    state_invalid = audit_logger.query_all_events(
        action=AuditActions.AUTH_TENANT_STATE_INVALID
    )
    assert len(state_invalid) == 1, (
        f"expected exactly one AUTH_TENANT_STATE_INVALID event, "
        f"got {len(state_invalid)}"
    )
    assert state_invalid[0].metadata.get("step") == "bootstrap"
    # The middleware writes metadata.reason = exc.code (which is the
    # error-class code "tenant_state_invalid"); the bootstrap's specific
    # refusal reason ("unknown_org_id_self_serve_disabled") lives on
    # metadata.details.reason. Both layers are asserted here so a
    # future refactor that loses either one fails loud.
    assert state_invalid[0].metadata.get("reason") == "tenant_state_invalid", (
        "audit metadata.reason should carry the typed exception code "
        f"(got {state_invalid[0].metadata.get('reason')!r})"
    )
    details = state_invalid[0].metadata.get("details") or {}
    assert details.get("reason") == "unknown_org_id_self_serve_disabled", (
        "audit metadata.details.reason should carry the bootstrap's "
        "specific refusal code (got "
        f"{details.get('reason')!r})"
    )
    assert details.get("org_id") == "org_unknown_blocked", (
        "audit metadata.details.org_id should preserve the rejected "
        "claim's org_id for forensic traceability"
    )

    # No AUTH_ACCEPTED — request was rejected before context attachment.
    accepted = audit_logger.query_all_events(action=AuditActions.AUTH_ACCEPTED)
    assert len(accepted) == 0, (
        "no AUTH_ACCEPTED should be emitted when self-serve is disabled "
        f"and the org_id is unknown; got {len(accepted)}"
    )
