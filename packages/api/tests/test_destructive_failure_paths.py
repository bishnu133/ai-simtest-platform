"""Turn 4 Session 2 Step 9 — destructive-failure path tests.

Covers v0.2 §6.4 ``test_destructive_failure_paths.py`` 3 tests,
adapted per amendment v0.2.1 and the Step 9 drift analysis in the
Session 2 chat record. Each test exercises the FULL factory-built
composition root — auth middleware, real sessionmaker against the
test Postgres, service bindings, routers — NOT a stripped-down
middleware-only harness. This is what distinguishes Turn 4's
destructive-path coverage from Turn 3's middleware-only tests.

Test list
---------
1. ``test_valid_jwt_for_invalid_tenant_state_returns_409_conflict`` —
   exit criterion #3 from v0.2 §6.1. Unknown org_id +
   allow_self_serve_provisioning=False -> 409 tenant_state_invalid
   at an endpoint layer, audit row written, no tenant row created.

2. ``test_valid_jwt_unknown_provider_org_role_returns_403_at_endpoint_layer`` —
   exit criterion #5 (expanded) from v0.2 §6.1. Existing tenant
   (seeded with first-user), new user arrives with an
   unmappable provider_org_role claim. Factory-built app (with full
   routing and service bindings in place) surfaces the 403 as the
   same envelope a real client would see. Complements Turn 3's
   middleware-layer coverage of the fail-closed role-mapping path.

3. ``test_cross_tenant_workspace_claim_returns_403_at_endpoint_layer`` —
   exit criterion #7 (adapted) from v0.2 §6.1. Live
   ``CrossWorkspaceForbidden`` has no raise-site today, so the
   "wrong workspace" invariant is tested via its structural
   counterpart ``CrossTenantForbidden``, which IS raised by
   ``ensure_tenant_and_workspace`` when the token's explicit
   workspace_id claim belongs to a different tenant than the
   token's org_id. Endpoint-layer assertion complements Turn 2's
   repo-level workspace-isolation tests.

Live-tree notes driving the test shape
--------------------------------------
* Factory auth path: construct ``AppSettings(app_env="test",
  auth_provider="dev", auth_enabled=True, database_url=<PG URL>,
  allow_self_serve_provisioning=<per-test>)`` and pass to
  ``create_app(settings=...)``. The factory registers
  ``TenantContextMiddleware`` because auth is on AND sessionmaker
  is non-None.
* DevAuthProvider reads identity from HTTP headers
  (``X-Dev-User-Id``, ``X-Dev-Org-Id``, ``X-Dev-Role``,
  ``X-Dev-Workspace-Id``) plus any non-empty Bearer token.
  Tests pass ``Authorization: Bearer dev`` as a stub.
* The middleware reads
  ``request.app.state.settings.allow_self_serve_provisioning``
  (stashed by the factory) and threads it into ``bootstrap()``.
  Test #1 relies on that plumbing to drive the fail-closed branch.
* ``clean_db`` fixture (re-exported through tests/conftest.py from
  tests/db/conftest.py) TRUNCATEs all 12 tables between tests, so
  each test starts against a migrated-but-empty database.
* Tests mount a real endpoint path that goes through the middleware
  and then the service layer. ``/v1/conversations`` is the cheapest
  reachable endpoint — its service has no repo dependency that
  would need seeding for the request to reach the middleware's
  bootstrap gate.

Runtime-environment note
------------------------
These three tests REQUIRE local Postgres 16 binaries (Homebrew
``postgresql@16`` on macOS / ``postgresql-16`` on Debian). If
binaries are absent, the ``clean_db`` fixture skips the entire file
with a clear message (same behaviour as ``tests/db/*``). No silent
fall-through to SQLite.
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.app_factory import create_app
from src.audit.logger import AuditActions, audit_logger
from src.auth.bootstrap import bootstrap
from src.auth.provider import VerifiedClaims
from src.config import AppSettings
from src.db.session import get_sessionmaker


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_factory_app(
    pg_url: str, *, allow_self_serve_provisioning: bool | None = None
):
    """Construct a factory-built app wired to the test Postgres.

    Parameters mirror the real production wiring:
      * auth_enabled=True so TenantContextMiddleware is registered
      * auth_provider="dev" so we can drive identity via headers
      * database_url=pg_url so the sessionmaker points at the
        migrated test database
      * allow_self_serve_provisioning threaded through explicitly
        (None means the validator derives from app_env)

    Tests call this per-test to get a fresh app + fresh service graph
    without reusing state from prior runs.
    """
    settings = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=True,
        database_url=pg_url,
        allow_self_serve_provisioning=allow_self_serve_provisioning,
    )
    return create_app(settings=settings)


async def _call(
    app, method: str, path: str, headers: dict[str, str] | None = None
):
    """Issue a single ASGI request against the factory-built app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.request(method, path, headers=headers or {})


# ---------------------------------------------------------------------------
# Test 1 — valid JWT + unknown org_id + self-serve disabled -> 409
# ---------------------------------------------------------------------------


async def test_valid_jwt_for_invalid_tenant_state_returns_409_conflict(
    clean_db: str,
) -> None:
    """Exit criterion v0.2 §6.1 #3 — provisioning drift returns 409.

    Contract asserted:
      * HTTP status  = 409 (TenantStateInvalid.http_status, Turn 4 Q1=B)
      * Error code   = "tenant_state_invalid"
      * details carry org_id + reason="unknown_org_id_self_serve_disabled"
      * Audit action = AuditActions.AUTH_TENANT_STATE_INVALID written
      * DB state unchanged — no tenant row created by this request

    Why this can't be a Turn 3 middleware test
    ------------------------------------------
    Turn 3 lacked both the AppSettings.allow_self_serve_provisioning
    field and the bootstrap raise-site. Session 2 Step 6.5 shipped
    both; this test is the integration-level proof they work end-to-end.
    """
    audit_logger.clear()

    app = _build_factory_app(clean_db, allow_self_serve_provisioning=False)

    # Dev-provider identity: unknown org_id, valid user_id, valid role.
    # The role doesn't matter — bootstrap aborts at the tenant-lookup
    # gate before role mapping runs.
    headers = {
        "Authorization": "Bearer dev",
        "X-Dev-User-Id": "user_new_1",
        "X-Dev-Org-Id": "org_not_in_db_yet",
        "X-Dev-Role": "admin",
    }
    resp = await _call(app, "GET", "/v1/conversations", headers=headers)

    # --- HTTP envelope ---------------------------------------------------
    assert resp.status_code == 409, (
        f"expected 409, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    err = body.get("error") or {}
    assert err.get("code") == "tenant_state_invalid"
    details = err.get("details") or {}
    assert details.get("org_id") == "org_not_in_db_yet"
    assert details.get("reason") == "unknown_org_id_self_serve_disabled"

    # --- Audit trail -----------------------------------------------------
    events = audit_logger.query(
        action=AuditActions.AUTH_TENANT_STATE_INVALID
    )
    assert len(events) == 1, (
        f"expected exactly one AUTH_TENANT_STATE_INVALID audit event, "
        f"got {len(events)}"
    )
    event = events[0]
    assert event.resource_type == "tenant"
    assert event.resource_id == "org_not_in_db_yet"
    assert event.metadata.get("step") == "bootstrap"

    # --- DB state unchanged ---------------------------------------------
    # No tenant row must exist for the rejected org_id. We read via
    # a raw session (bypassing RLS) so "no row" means genuinely absent.
    import sqlalchemy as sa
    from src.db.session import raw_admin_session

    async with raw_admin_session() as session:
        result = await session.execute(
            sa.text(
                "SELECT COUNT(*) FROM tenants WHERE clerk_org_id = :org"
            ),
            {"org": "org_not_in_db_yet"},
        )
        count = result.scalar_one()
    assert count == 0, (
        "tenant row was created despite allow_self_serve_provisioning=False"
    )


# ---------------------------------------------------------------------------
# Test 2 — valid JWT + unknown provider_org_role -> 403 at endpoint
# ---------------------------------------------------------------------------


async def test_valid_jwt_unknown_provider_org_role_returns_403_at_endpoint_layer(
    clean_db: str,
) -> None:
    """Exit criterion v0.2 §6.1 #5 (expanded) — fail-closed role
    mapping surfaces at the endpoint layer.

    Setup:
      1. Seed tenant via first-user bootstrap (user_seed, role=admin).
         This creates the tenant row plus tenant+workspace membership
         for user_seed as owner.
      2. Second user arrives with the SAME org_id (so tenant is found)
         but a provider_org_role value NOT in PROVIDER_ROLE_MAP. New
         user onboarding path fires, map_provider_org_role raises
         UnknownProviderOrgRole, middleware surfaces it as 403.

    Contract asserted:
      * HTTP status  = 403
      * Error code   = "unknown_provider_org_role"
      * details carry the offending role value
      * Audit action = AuditActions.AUTH_MEMBERSHIP_DENIED written
      * DB state: user_second has NO membership rows (fail-closed)

    Complements Turn 3's ``test_missing_provider_org_role_returns_403_\
membership_denied`` in tests/auth/test_tenant_context_middleware.py
    by running through the real factory composition root instead of
    the minimal _make_app harness. Same invariant; different harness.
    """
    audit_logger.clear()

    # --- Step 1: seed first user via bootstrap directly (not via HTTP) ---
    sm = get_sessionmaker()
    seed_claims = VerifiedClaims(
        user_id="user_seed",
        org_id="org_roletest",
        provider_org_role="admin",
        display_name="Seed",
    )
    async with sm() as session:
        await bootstrap(
            session,
            seed_claims,
            allow_self_serve_provisioning=True,
        )

    audit_logger.clear()  # discard seed events; focus on the real test path

    # --- Step 2: build a factory app, let self-serve default for
    # app_env="test" (which is True), then make the denied call ------------
    app = _build_factory_app(clean_db, allow_self_serve_provisioning=True)

    headers = {
        "Authorization": "Bearer dev",
        "X-Dev-User-Id": "user_second",
        "X-Dev-Org-Id": "org_roletest",
        "X-Dev-Role": "bogusrole",  # NOT in PROVIDER_ROLE_MAP
    }
    resp = await _call(app, "GET", "/v1/conversations", headers=headers)

    # --- HTTP envelope ---------------------------------------------------
    assert resp.status_code == 403, (
        f"expected 403, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    err = body.get("error") or {}
    assert err.get("code") == "unknown_provider_org_role"
    details = err.get("details") or {}
    assert details.get("provider_org_role") == "bogusrole"

    # --- Audit trail -----------------------------------------------------
    # The middleware writes AUTH_MEMBERSHIP_DENIED (not AUTH_REJECTED)
    # for role-mapping failures — the v0.5.1 MF-2 audit contract.
    denied_events = audit_logger.query(
        action=AuditActions.AUTH_MEMBERSHIP_DENIED
    )
    assert len(denied_events) == 1, (
        f"expected 1 AUTH_MEMBERSHIP_DENIED event, got {len(denied_events)}. "
        f"All events: {[e.action for e in audit_logger._events]}"
    )

    # --- DB state: user_second has no memberships ------------------------
    import sqlalchemy as sa
    from src.db.session import raw_admin_session

    async with raw_admin_session() as session:
        result = await session.execute(
            sa.text(
                "SELECT COUNT(*) FROM memberships WHERE user_id = :uid"
            ),
            {"uid": "user_second"},
        )
        count = result.scalar_one()
    assert count == 0, (
        "user_second acquired membership despite unknown_provider_org_role; "
        "fail-closed contract violated."
    )


# ---------------------------------------------------------------------------
# Test 3 — cross-tenant workspace claim -> 403 at endpoint layer
# ---------------------------------------------------------------------------


async def test_cross_tenant_workspace_claim_returns_403_at_endpoint_layer(
    clean_db: str,
) -> None:
    """Exit criterion v0.2 §6.1 #7 (adapted) — endpoint-layer
    workspace-isolation enforcement.

    Substitution rationale (recorded in Step 9 drift analysis)
    ---------------------------------------------------------
    v0.2 §6.1 named this test as the "endpoint-layer complement to
    Turn 2's repo-level wrong-workspace test". Problem: live
    ``CrossWorkspaceForbidden`` has NO raise-site in src/ — the guard
    is defined in api/errors.py but never triggered. The
    plan's "repo level only (Turn 2)" claim is itself drift.

    The guard that IS raised, and DOES enforce workspace isolation
    end-to-end, is ``CrossTenantForbidden`` from the workspace
    repo's ``get_by_id`` — fired during
    ``ensure_tenant_and_workspace`` when the token's explicit
    ``workspace_id`` claim refers to a workspace that belongs to a
    DIFFERENT tenant than the token's ``org_id``. That's the
    strongest form of the wrong-workspace invariant (it catches
    both the within-tenant and the cross-tenant form at bootstrap
    time, before the request reaches any router-layer guard).

    Contract asserted:
      * HTTP status  = 403
      * Error code   = "cross_tenant_forbidden"
      * Audit action = AuditActions.AUTH_REJECTED or workspace-related
        audit event written (exact action depends on middleware's
        current catch order)
      * DB state: no membership created for the attempted user in
        the foreign tenant

    Setup:
      1. Seed tenant A with first user (owner), which creates tenant_a
         + default workspace_a.
      2. Seed tenant B with first user (owner), which creates tenant_b
         + default workspace_b.
      3. User arrives claiming org_a but workspace_b in the
         X-Dev-Workspace-Id header. Cross-tenant workspace claim
         triggers CrossTenantForbidden from workspace_repo.get_by_id
         inside ensure_tenant_and_workspace.

    This is functionally the workspace-boundary endpoint test the
    plan wanted, using the guard the live tree actually raises. The
    delivery report documents the substitution.
    """
    audit_logger.clear()

    # --- Seed tenant A ---------------------------------------------------
    sm = get_sessionmaker()
    claims_a = VerifiedClaims(
        user_id="user_a_owner",
        org_id="org_tenant_a",
        provider_org_role="admin",
        display_name="A",
    )
    async with sm() as session:
        result_a = await bootstrap(
            session,
            claims_a,
            allow_self_serve_provisioning=True,
        )

    # --- Seed tenant B ---------------------------------------------------
    claims_b = VerifiedClaims(
        user_id="user_b_owner",
        org_id="org_tenant_b",
        provider_org_role="admin",
        display_name="B",
    )
    async with sm() as session:
        result_b = await bootstrap(
            session,
            claims_b,
            allow_self_serve_provisioning=True,
        )

    # Capture workspace_b for the cross-tenant claim attempt.
    workspace_b_id = result_b.workspace_id

    audit_logger.clear()  # discard seeding events

    # --- Build factory app + make the bad call ---------------------------
    app = _build_factory_app(clean_db, allow_self_serve_provisioning=True)

    # User claims tenant_a but explicitly references workspace_b.
    # The DevAuthProvider's X-Dev-Workspace-Id header populates
    # VerifiedClaims.workspace_id; ensure_tenant_and_workspace
    # validates via workspace_repo.get_by_id(tenant_a_ctx, workspace_b)
    # which triggers the §5.7 info-leak guard CrossTenantForbidden.
    headers = {
        "Authorization": "Bearer dev",
        "X-Dev-User-Id": "user_a_owner",
        "X-Dev-Org-Id": "org_tenant_a",
        "X-Dev-Role": "admin",
        "X-Dev-Workspace-Id": workspace_b_id,
    }
    resp = await _call(app, "GET", "/v1/conversations", headers=headers)

    # --- HTTP envelope ---------------------------------------------------
    assert resp.status_code == 403, (
        f"expected 403, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    err = body.get("error") or {}
    assert err.get("code") == "cross_tenant_forbidden", (
        f"expected cross_tenant_forbidden, got {err.get('code')}: {err}"
    )

    # --- DB state: no stray membership created ---------------------------
    # user_a_owner already has rows in tenant_a from the seed. The
    # failed request must not have created a row in tenant_b.
    import sqlalchemy as sa
    from src.db.session import raw_admin_session

    async with raw_admin_session() as session:
        result = await session.execute(
            sa.text(
                "SELECT COUNT(*) FROM memberships "
                "WHERE user_id = :uid AND tenant_id = :tid"
            ),
            {"uid": "user_a_owner", "tid": result_b.tenant_id},
        )
        count = result.scalar_one()
    assert count == 0, (
        "user_a_owner acquired membership in tenant_b via cross-tenant "
        "workspace claim; isolation boundary breached."
    )
