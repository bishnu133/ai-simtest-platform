"""Week 6a Turn 2: results router tests (11 tests)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_tenant_context
from src.api.errors import APIError, api_error_handler
from src.audit.logger import AuditActions, audit_logger
from src.common.models import ActorRef, TenantContext, utcnow
from src.results.cache import DashboardCache
from src.results.models import (
    CoverageMetric,
    FailurePattern,
    JudgeBreakdown,
    RunOverview,
)
from src.results.repository import InMemoryDashboardArtifactRepository
from src.results.router import _get_dashboard_service, router as results_router
from src.results.service import DashboardService
from src.runs import InMemoryRunRepository, RunRecord, RunService, RunStatus


@pytest.fixture
def actor() -> ActorRef:
    return ActorRef(actor_id="alice", actor_type="human")


@pytest.fixture
def ctx_a(actor) -> TenantContext:
    return TenantContext(tenant_id="t_a", workspace_id="w_main", actor=actor)


@pytest.fixture
def ctx_b(actor) -> TenantContext:
    return TenantContext(
        tenant_id="t_b",
        workspace_id="w_main",
        actor=ActorRef(actor_id="bob", actor_type="human"),
    )


@pytest.fixture
def app(ctx_a):
    audit_logger.clear_all()
    run_repo = InMemoryRunRepository()
    artifact_repo = InMemoryDashboardArtifactRepository()
    cache = DashboardCache()
    run_svc = RunService(run_repo)
    dash_svc = DashboardService(run_svc, artifact_repo, cache)

    # Seed a run + artifacts in tenant_a
    run = RunRecord(
        run_id="run_1",
        tenant_id="t_a",
        workspace_id="w_main",
        status=RunStatus.COMPLETED,
        completed_at=utcnow(),
    )
    import asyncio

    asyncio.new_event_loop().run_until_complete(run_repo.create(run))
    artifact_repo.seed(
        ctx_a,
        "run_1",
        overview=RunOverview(
            run_id="run_1", status=RunStatus.COMPLETED, pass_rate=0.85, total_conversations=100
        ),
        judges=[JudgeBreakdown(judge_name="grounding", pass_count=85, fail_count=15)],
        failures=[FailurePattern(cluster_id="c1", label="off-topic", count=10)],
        coverage=[CoverageMetric(dimension="topic", covered=8, total=10)],
    )

    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(results_router, prefix="/v1")
    app.dependency_overrides[_get_dashboard_service] = lambda: dash_svc

    # tenant context override — default to tenant_a
    app.state._test_ctx = ctx_a
    app.dependency_overrides[get_tenant_context] = lambda: app.state._test_ctx

    app.state._run_repo = run_repo
    app.state._cache = cache
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_dashboard_happy_path_with_provenance(client, app):
    resp = client.get("/v1/runs/run_1/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overview"]["run_id"] == "run_1"
    assert body["engine_version"] == "engine_v1"
    assert body["run_status"] == "completed"
    assert body["data_source_version"] == "in_memory_v1"
    assert "artifact_generated_at" in body
    assert "generated_at" in body


def test_dashboard_404_for_unknown_run(client, app):
    resp = client.get("/v1/runs/missing/dashboard")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"


def test_dashboard_cross_tenant_denial(client, app, ctx_b):
    app.state._test_ctx = ctx_b
    resp = client.get("/v1/runs/run_1/dashboard")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "cross_tenant_forbidden"


def test_dashboard_cache_hit_on_second_call(client, app):
    r1 = client.get("/v1/runs/run_1/dashboard")
    cached_at_1 = r1.json()["generated_at"]
    r2 = client.get("/v1/runs/run_1/dashboard")
    assert r2.json()["generated_at"] == cached_at_1  # served from cache


def test_dashboard_cache_invalidate_clears_entry(client, app):
    client.get("/v1/runs/run_1/dashboard")
    assert app.state._cache.get("run_1") is not None
    app.state._cache.invalidate("run_1")
    assert app.state._cache.get("run_1") is None


def test_judges_endpoint(client):
    resp = client.get("/v1/runs/run_1/judges")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["judge_name"] == "grounding"


def test_failures_endpoint(client):
    resp = client.get("/v1/runs/run_1/failures")
    assert resp.status_code == 200
    assert resp.json()[0]["cluster_id"] == "c1"


def test_coverage_endpoint(client):
    resp = client.get("/v1/runs/run_1/coverage")
    assert resp.status_code == 200
    assert resp.json()[0]["dimension"] == "topic"


def test_judges_404_for_unknown_run(client):
    resp = client.get("/v1/runs/nope/judges")
    assert resp.status_code == 404


def test_timeline_filters_to_run_lifecycle_whitelist(client, app, ctx_a):
    audit_logger.clear_all()
    audit_logger.write(ctx_a, AuditActions.RUN_QUEUED, "run", "run_1")
    audit_logger.write(ctx_a, AuditActions.RUN_STARTED, "run", "run_1")
    audit_logger.write(ctx_a, AuditActions.RUN_COMPLETED, "run", "run_1")
    # Non-whitelisted noise that must be filtered out:
    audit_logger.write(ctx_a, AuditActions.ASSET_CREATED, "run", "run_1")
    audit_logger.write(ctx_a, AuditActions.CONVERSATION_STORED, "conversation", "run_1")

    resp = client.get("/v1/runs/run_1/timeline")
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()["events"]]
    assert actions == ["run.queued", "run.started", "run.completed"]


def test_timeline_cross_tenant_denial(client, app, ctx_b):
    app.state._test_ctx = ctx_b
    resp = client.get("/v1/runs/run_1/timeline")
    assert resp.status_code == 403


def test_run_timeline_includes_async_emitted_run_state_events(actor):
    """FH-S7.6 B.3.1 contract: F-16's query_all_events() migration in
    src/results/service.py enables the run timeline to surface audit
    events emitted via the ASYNC path (aemit_tenant_event from
    runs/service.py:80, Slice 8 Pattern B).

    Pre-B.3 the timeline reader used the sync query() API which (per
    Q-8 negative finding) reads only sync _events. Async-bound events
    were invisible, so the timeline silently missed real run-state
    transitions. Post-B.3 query_all_events() reads BOTH sync _events
    AND the async-bound InMemoryAuditEventRepository.

    End-to-end path exercised:
      RunService.transition(ctx, run_id, RUNNING)
        -> runs/service.py:80 aemit_tenant_event (async)
        -> bound InMemoryAuditEventRepository
      GET /v1/runs/{run_id}/timeline
        -> results/service.py query_all_events (B.3-migrated reader)
        -> response includes the async-emitted RUN_STARTED event

    Uses UUID-format tenant/workspace IDs to bypass F-18 C5 (Slice 8
    UUID-parse regression in to_tenant_audit_event). C5 is orthogonal
    to the F-16 contract being pinned here; the UUID workaround keeps
    runs/service.py:80 from raising and lets B.3.1 demonstrate F-16
    correctness in isolation."""
    import asyncio

    # UUID-format ctx avoids F-18 C5 in runs/service.py:80.
    tenant_uuid = "00000000-0000-0000-0000-0000000000aa"
    workspace_uuid = "00000000-0000-0000-0000-0000000000bb"

    ctx_uuid = TenantContext(
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        actor=actor,
    )

    # CRITICAL: clear_all (not just .clear()) — Q-8 negative means we
    # must reset both sync _events and async repository for true clean
    # state. The existing app fixture in this file uses .clear() which
    # only resets sync _events; that gets migrated in B.5b.
    audit_logger.clear_all()

    # Build minimal app: real RunService (so transition exercises the
    # actual runs/service.py:80 aemit path) + real DashboardService
    # (so /timeline exercises the B.3-migrated reader).
    run_repo = InMemoryRunRepository()
    artifact_repo = InMemoryDashboardArtifactRepository()
    cache = DashboardCache()
    run_svc = RunService(run_repo)
    dash_svc = DashboardService(run_svc, artifact_repo, cache)

    run = RunRecord(
        run_id="run_b31",
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        status=RunStatus.QUEUED,
    )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run_repo.create(run))
        # Trigger Slice 8 Pattern B async emit at runs/service.py:80.
        # _STATUS_TO_AUDIT_ACTION[RUNNING] = RUN_STARTED (whitelisted
        # in _TIMELINE_ACTION_WHITELIST + resource_type="run" — exactly
        # what the timeline endpoint surfaces).
        loop.run_until_complete(
            run_svc.transitions.transition(ctx_uuid, "run_b31", RunStatus.RUNNING)
        )
    finally:
        loop.close()

    # Test app with ctx_uuid override (mirrors existing fixture pattern
    # at line ~82 of this file; direct lambda since no mid-test ctx
    # mutation is needed).
    test_app = FastAPI()
    test_app.add_exception_handler(APIError, api_error_handler)
    test_app.include_router(results_router, prefix="/v1")
    test_app.dependency_overrides[_get_dashboard_service] = lambda: dash_svc
    test_app.dependency_overrides[get_tenant_context] = lambda: ctx_uuid

    client = TestClient(test_app)
    resp = client.get("/v1/runs/run_b31/timeline")

    # Pre-B.3: this response would have events=[] because the
    # transition's async emit was invisible to sync query().
    # Post-B.3: query_all_events surfaces the async-bound event.
    assert resp.status_code == 200, resp.text

    actions = [e["action"] for e in resp.json()["events"]]
    assert AuditActions.RUN_STARTED in actions, (
        "F-16 contract violation: run timeline must include the "
        "RUN_STARTED event emitted via the async path from "
        "runs/service.py:80. Pre-B.3 sync-only query() would have "
        f"returned []; post-B.3 query_all_events() should surface it. "
        f"Got actions: {actions}"
    )
