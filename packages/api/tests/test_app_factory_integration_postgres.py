"""Turn 4 Session 2 Step 11 — end-to-end Postgres integration test.

Single test: build a factory app against local Postgres, drive a
real HTTP request through the complete five-step auth pipeline, and
assert that tenant + workspace + membership rows land in the DB and
the dashboard endpoint returns 200 with the expected payload.

Scope rationale
---------------
This is the ONLY integration test in the final Session 2 deliverable.
The SQLite-variant originally spec'd in v0.2 §6.5 was dropped (β) at
the Step 10 decision point — the Alembic migrations use
``CREATE EXTENSION pgcrypto``, ``PARTITION BY RANGE``, and
``ENABLE ROW LEVEL SECURITY``, all of which are Postgres-specific and
would require a lossy ``Base.metadata.create_all()`` substitute that
adds maintenance burden and drift risk for marginal extra coverage.
The Postgres test here exercises the same five-step pipeline against
the real schema with real RLS — the Session 2 close-out target.

See the session 2 delivery report §"Step 10 — dropped by design" for
the full rationale.

Contract asserted by this single test
-------------------------------------
Happy path — a dev-authenticated request flows end-to-end:

  * ``TenantContextMiddleware`` extracts the bearer token
  * ``DevAuthProvider.verify`` returns VerifiedClaims from headers
  * ``bootstrap`` creates tenant + workspace + owner membership
    (first-user path, because the DB is empty under ``clean_db``)
  * ``TenantContext`` attaches to ``request.state``
  * results router + DashboardService + RunService resolve the
    seeded run, return 200 with ``DashboardSummary`` payload
  * ``auth.accepted`` audit row written by the middleware

Physical DB state asserted via raw admin session:
  * exactly one tenant row for the org_id
  * exactly one workspace row for that tenant, is_default=True
  * exactly one tenant-scope membership + one workspace-scope
    membership for the first user, both with role=owner

Seeding shape
-------------
Per amendment §7.5/§7.6: we bootstrap the tenant via the sessionmaker
BEFORE the HTTP request so we can capture the tenant_id/workspace_id
the bootstrap resolves. Then we seed the RunRecord and
DashboardOverview against those exact IDs so the endpoint request
can find them. The HTTP request goes through bootstrap a second
time — which is idempotent and takes the "existing row" fast path
— and reaches the router.

Why we don't just skip the first bootstrap and seed after the HTTP
call: the HTTP call would 404 on the missing run, defeating the
"200 happy path" contract. The two-phase setup lets us test the
real 200 path end-to-end.

Runtime-environment note
------------------------
Requires local Postgres 16 binaries (Homebrew ``postgresql@16`` on
macOS, ``postgresql-16`` on Debian). If binaries are absent, the
``clean_db`` fixture skips this entire file with a clear message —
no silent fall-through to a weaker backend.
"""
from __future__ import annotations

import sqlalchemy as sa
import pytest
from httpx import ASGITransport, AsyncClient

from src.app_factory import create_app
from src.audit.logger import AuditActions, audit_logger
from src.auth.bootstrap import bootstrap
from src.auth.provider import VerifiedClaims
from src.config import AppSettings
from src.db.session import get_sessionmaker, raw_admin_session
from src.results.models import RunOverview
from src.runs.models import RunRecord, RunStatus

pytestmark = pytest.mark.asyncio

async def test_factory_built_app_serves_one_authenticated_request_end_to_end_postgres(
    clean_db: str,
) -> None:
    """Full Postgres + Clerk-shaped end-to-end through the factory.

    This is the Session 2 close-out test: factory composition root,
    real Postgres (migrated schema + RLS), dev auth provider, one
    HTTP request, 200 response, persisted DB rows, audit trail.

    If this test passes, the Turn 4 composition-root contract is
    validated against the real infrastructure boundary that Turn 4.5
    will exercise with Neon + Clerk.
    """
    audit_logger.clear()

    # ------------------------------------------------------------------
    # Phase 1 — pre-seed tenant + workspace + membership so we know
    # which tenant_id / workspace_id to attach to the RunRecord.
    # Uses the sessionmaker directly (outside the HTTP pipeline) so
    # bootstrap runs exactly once for the seed, and the HTTP request
    # later takes the idempotent "tenant already exists" fast path.
    # ------------------------------------------------------------------
    sm = get_sessionmaker()
    first_user_claims = VerifiedClaims(
        user_id="user_integration_owner",
        org_id="org_integration_test",
        provider_org_role="admin",
        display_name="Integration Test Owner",
    )
    async with sm() as session:
        seed_result = await bootstrap(
            session,
            first_user_claims,
            allow_self_serve_provisioning=True,
        )

    assert seed_result.is_first_user is True
    assert seed_result.role == "owner"
    tenant_id = seed_result.tenant_id
    workspace_id = seed_result.workspace_id

    audit_logger.clear()  # discard seed events; focus on the HTTP-request audit

    # ------------------------------------------------------------------
    # Phase 2 — build the factory app. Auth on, real sessionmaker
    # bound to the test Postgres by the _configure_engine autouse
    # fixture, dev provider because integration tests don't forge
    # real Clerk JWTs.
    # ------------------------------------------------------------------
    settings = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=True,
        database_url=clean_db,
        allow_self_serve_provisioning=True,
    )
    app = create_app(settings=settings)

    # ------------------------------------------------------------------
    # Phase 3 — seed run + dashboard artifact against the exact
    # tenant_id / workspace_id bootstrap resolved. This uses the
    # in-memory repos the factory constructed in _bind_services;
    # they're reachable via app.state (Turn 4 amendment §4.1).
    # ------------------------------------------------------------------
    run_id = "run_integration_1"
    run_record = RunRecord(
        run_id=run_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status=RunStatus.COMPLETED,
        engine_version="engine_v1_test",
    )
    run_repo = app.state.run_service._repo
    await run_repo.create(run_record)

    # The dashboard endpoint needs at least a RunOverview artifact, or
    # DashboardService raises RunNotFound on the missing overview.
    # Seed the minimum shape to produce a 200.
    from src.common.models import ActorRef, TenantContext

    seed_ctx = TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=ActorRef(
            actor_id=first_user_claims.user_id, actor_type="human"
        ),
    )
    overview = RunOverview(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        pass_rate=0.95,
        total_conversations=42,
    )
    # The factory's DashboardService holds the artifact repo at
    # ._artifacts; same accessor the live tests use.
    art_repo = app.state.dashboard_service._artifacts
    art_repo.seed(seed_ctx, run_id, overview)

    # ------------------------------------------------------------------
    # Phase 4 — issue the HTTP request with dev-auth headers that
    # identify the pre-seeded user. The middleware's bootstrap call
    # takes the existing-row fast path (tenant already there,
    # membership rows already there), attaches TenantContext to
    # request.state, and the endpoint returns 200.
    # ------------------------------------------------------------------
    headers = {
        "Authorization": "Bearer dev-integration",
        "X-Dev-User-Id": first_user_claims.user_id,
        "X-Dev-Org-Id": first_user_claims.org_id,
        "X-Dev-Role": "admin",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/v1/runs/{run_id}/dashboard", headers=headers)

    # ------------------------------------------------------------------
    # Phase 5 — assertions. HTTP envelope, audit trail, DB state.
    # ------------------------------------------------------------------

    # (a) HTTP 200 with expected payload shape.
    assert resp.status_code == 200, (
        f"expected 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["overview"]["run_id"] == run_id
    assert body["overview"]["status"] == "completed"
    assert body["overview"]["pass_rate"] == 0.95
    assert body["overview"]["total_conversations"] == 42
    assert body["engine_version"] == "engine_v1_test"
    assert body["run_status"] == "completed"

    # (b) Correlation-ID header echoed back (CorrelationIdMiddleware
    # is registered by the factory; this confirms the middleware
    # stack actually ran over this response path).
    assert "X-Correlation-Id" in resp.headers or "x-correlation-id" in {
        k.lower() for k in resp.headers.keys()
    }

    # (c) Audit trail — auth.accepted written by the middleware.
    # The seed events were cleared before the HTTP call, so the
    # accepted event here is the one that came out of the request.
    # FH-S7.5 B.5 fix-forward: M8 (auth.accepted) was migrated from
    # sync audit_logger.write() to async aemit_tenant_event_safe(),
    # so .query() (sync-only view) no longer sees AUTH_ACCEPTED events.
    # query_all_events() is the Slice 7 unified view across sync + async
    # paths. The broader .query()/_events migration across other tests
    # is tracked as a Slice 7 tail item (memory: F-16).
    accepted = audit_logger.query_all_events(action=AuditActions.AUTH_ACCEPTED)
    assert len(accepted) >= 1, (
        f"expected at least one AUTH_ACCEPTED event, got {len(accepted)}. "
        f"All events: {[e.action for e in audit_logger.query_all_events()]}"
    )

    # The audited actor matches the user who made the request.
    assert any(
        e.actor.actor_id == first_user_claims.user_id for e in accepted
    )

    # (d) DB state — persistent rows are real, tenant-scoped,
    # and exactly the ones bootstrap created. We use raw_admin_session
    # so RLS doesn't hide rows we expect to see.
    async with raw_admin_session() as session:
        tenant_count = (
            await session.execute(
                sa.text(
                    "SELECT COUNT(*) FROM tenants "
                    "WHERE clerk_org_id = :org"
                ),
                {"org": first_user_claims.org_id},
            )
        ).scalar_one()
        workspace_count = (
            await session.execute(
                sa.text(
                    "SELECT COUNT(*) FROM workspaces "
                    "WHERE tenant_id = :tid AND is_default = true"
                ),
                {"tid": tenant_id},
            )
        ).scalar_one()
        # Bootstrap creates BOTH a tenant-level (workspace_id IS NULL)
        # and a workspace-level (workspace_id = <default>) owner
        # membership for the first user.
        membership_rows = (
            await session.execute(
                sa.text(
                    "SELECT workspace_id, role FROM memberships "
                    "WHERE user_id = :uid AND tenant_id = :tid "
                    "ORDER BY workspace_id NULLS FIRST"
                ),
                {"uid": first_user_claims.user_id, "tid": tenant_id},
            )
        ).all()

    assert tenant_count == 1, (
        f"expected 1 tenant row, got {tenant_count}"
    )
    assert workspace_count == 1, (
        f"expected 1 default workspace row, got {workspace_count}"
    )
    assert len(membership_rows) == 2, (
        f"expected 2 membership rows (tenant + workspace), got "
        f"{len(membership_rows)}: {membership_rows}"
    )
    # Both should be owner-role per the first-user-bootstrap rule.
    assert all(row.role == "owner" for row in membership_rows), (
        f"expected all memberships role=owner, got: "
        f"{[(r.workspace_id, r.role) for r in membership_rows]}"
    )
    # One row tenant-scope (workspace_id NULL), one workspace-scope.
    workspace_ids = [row.workspace_id for row in membership_rows]
    assert workspace_ids[0] is None, (
        f"expected first (ordered) membership row to be tenant-level "
        f"(workspace_id NULL), got: {workspace_ids}"
    )
    assert str(workspace_ids[1]) == workspace_id, (
        f"expected workspace-scope membership to match the default "
        f"workspace ({workspace_id}), got: {workspace_ids[1]!r}"
    )
