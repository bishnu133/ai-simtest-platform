"""Week 6a Turn 3: comparison lifecycle contract tests (7 tests)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_tenant_context
from src.api.errors import APIError, api_error_handler
from src.audit.logger import audit_logger
from src.common.models import ActorRef, TenantContext, utcnow
from src.comparisons import (
    ComparisonService,
    ComparisonStatus,
    FailClosedComparisonProvider,
    InMemoryComparisonRepository,
    InMemoryIdempotencyStore,
)
from src.comparisons.router import _get_comparison_service, router as cmp_router
from src.runs import InMemoryRunRepository, RunRecord, RunService, RunStatus


class _FakeProvider:
    async def compute(self, record):
        from src.comparisons.models import RegressionSignal
        return ([RegressionSignal(type="judge_drift", severity="info", metric="grounding", payload={})], None)


def _ctx_member():
    ctx = TenantContext(
        tenant_id="t_a", workspace_id="w_main",
        actor=ActorRef(actor_id="alice", actor_type="human"),
    )
    object.__setattr__(ctx, "role", "member")
    return ctx


@pytest.fixture
def setup():
    audit_logger.clear()
    run_repo = InMemoryRunRepository()
    cmp_repo = InMemoryComparisonRepository()
    idem = InMemoryIdempotencyStore()
    run_svc = RunService(run_repo)
    for rid in ("r1", "r2"):
        asyncio.new_event_loop().run_until_complete(
            run_repo.create(
                RunRecord(run_id=rid, tenant_id="t_a", workspace_id="w_main",
                          status=RunStatus.COMPLETED, completed_at=utcnow())
            )
        )
    cmp_svc = ComparisonService(cmp_repo, run_svc, _FakeProvider(), idem)
    fail_svc = ComparisonService(cmp_repo, run_svc, FailClosedComparisonProvider(), idem)
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(cmp_router, prefix="/v1")
    app.dependency_overrides[_get_comparison_service] = lambda: cmp_svc
    app.dependency_overrides[get_tenant_context] = _ctx_member
    app.state._cmp = cmp_svc
    app.state._fail = fail_svc
    return app, TestClient(app)


def test_full_lifecycle_pending_running_completed(setup):
    _, client = setup
    body = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}).json()
    # Synchronous execution in 6a — should land in COMPLETED with started_at + completed_at set
    assert body["status"] == ComparisonStatus.COMPLETED.value
    assert body["started_at"] is not None
    assert body["completed_at"] is not None
    assert body["error"] is None


def test_response_shape_snapshot_all_reserved_fields_present(setup):
    _, client = setup
    body = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}).json()
    expected_keys = {
        "id", "workspace_id", "tenant_id", "left_run_id", "right_run_id",
        "status", "created_at", "started_at", "completed_at", "error",
        "engine_version", "regression_signals",
        "left_provenance", "right_provenance",
        "verdict", "evidence_count", "metric_deltas", "comparison_profile",
        "result_ref",
    }
    assert set(body.keys()) == expected_keys
    # Source-of-truth rule: result_ref None, regression_signals populated
    assert body["result_ref"] is None
    assert body["regression_signals"] is not None


def test_idempotent_replay_returns_200_with_identical_body(setup):
    app, client = setup
    headers = {"Idempotency-Key": "key-abc"}
    r1 = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}, headers=headers)
    assert r1.status_code == 201
    r2 = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]


def test_idempotency_conflict_on_same_key_different_body(setup):
    _, client = setup
    headers = {"Idempotency-Key": "key-xyz"}
    client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}, headers=headers)
    r2 = client.post("/v1/comparisons", json={"left_run_id": "r2", "right_run_id": "r1"}, headers=headers)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_conflict"
    assert "original_request_hash" in r2.json()["error"]["details"]


def test_provider_unavailable_returns_503_comparison_not_supported(setup):
    """Named test for §S3 — explicit 503 path assertion."""
    app, client = setup
    app.dependency_overrides[_get_comparison_service] = lambda: app.state._fail
    r = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "comparison_not_supported"


def test_eligibility_run_not_completed(setup):
    app, client = setup
    asyncio.new_event_loop().run_until_complete(
        app.state._cmp._runs._repo.create(
            RunRecord(run_id="r_running", tenant_id="t_a", workspace_id="w_main", status=RunStatus.RUNNING)
        )
    )
    r = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r_running"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "comparison_ineligible"
    assert r.json()["error"]["details"]["reason"] == "run_not_completed"


def test_audit_event_emitted_on_create(setup):
    _, client = setup
    audit_logger.clear()
    client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    events = audit_logger.query(action="comparison.created")
    assert len(events) == 1
