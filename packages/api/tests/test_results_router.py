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
    audit_logger.clear()
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
    audit_logger.clear()
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
