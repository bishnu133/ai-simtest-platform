"""Week 6a Turn 3: comparisons router tests (11 tests, v1.2.2 Correction 3)."""
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
    FailClosedComparisonProvider,
    InMemoryComparisonRepository,
    InMemoryIdempotencyStore,
)
from src.comparisons.router import _get_comparison_service, router as cmp_router
from src.runs import InMemoryRunRepository, RunRecord, RunService, RunStatus


class _FakeProvider:
    async def compute(self, record):
        from src.comparisons.models import RegressionSignal

        return ([RegressionSignal(type="pass_rate_drop", severity="warning", metric="overall", payload={})], None)


def _ctx(tenant: str, role: str = "member") -> TenantContext:
    ctx = TenantContext(
        tenant_id=tenant,
        workspace_id="w_main",
        actor=ActorRef(actor_id=f"u_{tenant}", actor_type="human"),
    )
    object.__setattr__(ctx, "role", role)
    return ctx


@pytest.fixture
def setup():
    audit_logger.clear_all()
    run_repo = InMemoryRunRepository()
    cmp_repo = InMemoryComparisonRepository()
    run_svc = RunService(run_repo)

    # Seed two completed runs in tenant_a
    ctx_a = _ctx("t_a")
    for rid in ("r1", "r2"):
        asyncio.new_event_loop().run_until_complete(
            run_repo.create(
                RunRecord(
                    run_id=rid, tenant_id="t_a", workspace_id="w_main",
                    status=RunStatus.COMPLETED, completed_at=utcnow(),
                )
            )
        )

    cmp_svc = ComparisonService(cmp_repo, run_svc, _FakeProvider(), InMemoryIdempotencyStore())
    fail_svc = ComparisonService(cmp_repo, run_svc, FailClosedComparisonProvider(), InMemoryIdempotencyStore())

    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(cmp_router, prefix="/v1")
    app.dependency_overrides[_get_comparison_service] = lambda: cmp_svc
    app.state._test_ctx = ctx_a
    app.dependency_overrides[get_tenant_context] = lambda: app.state._test_ctx
    app.state._svc = cmp_svc
    app.state._fail_svc = fail_svc
    return app, TestClient(app)


def test_create_returns_201_with_provenance(setup):
    app, client = setup
    r = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["left_provenance"]["run_id"] == "r1"
    assert body["right_provenance"]["run_id"] == "r2"
    # §11.6 reserved fields are null
    assert body["verdict"] is None
    assert body["evidence_count"] is None
    assert body["metric_deltas"] is None
    assert body["comparison_profile"] is None
    # §M2 hybrid storage rule: result_ref None in 6a
    assert body["result_ref"] is None


def test_create_ineligible_when_run_not_completed(setup):
    app, client = setup
    asyncio.new_event_loop().run_until_complete(
        app.state._svc._runs._repo.create(
            RunRecord(run_id="r3", tenant_id="t_a", workspace_id="w_main", status=RunStatus.RUNNING)
        )
    )
    r = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r3"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "comparison_ineligible"


def test_create_provider_unavailable_returns_503(setup):
    app, client = setup
    app.dependency_overrides[_get_comparison_service] = lambda: app.state._fail_svc
    r = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "comparison_not_supported"


def test_create_role_denied_for_viewer(setup):
    app, client = setup
    app.state._test_ctx = _ctx("t_a", role="viewer")
    r = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "forbidden_role"
    assert body["error"]["details"]["actor_role"] == "viewer"


def test_get_comparison_happy_path(setup):
    app, client = setup
    cid = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}).json()["id"]
    r = client.get(f"/v1/comparisons/{cid}")
    assert r.status_code == 200
    assert r.json()["id"] == cid


def test_get_comparison_404(setup):
    _, client = setup
    r = client.get("/v1/comparisons/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "comparison_not_found"


def test_get_comparison_cross_tenant_denial(setup):
    app, client = setup
    cid = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}).json()["id"]
    app.state._test_ctx = _ctx("t_b")
    r = client.get(f"/v1/comparisons/{cid}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cross_tenant_forbidden"


def test_list_comparisons_empty(setup):
    _, client = setup
    r = client.get("/v1/comparisons")
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None, "has_more": False}


def test_list_comparisons_with_items(setup):
    _, client = setup
    client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"})
    r = client.get("/v1/comparisons")
    assert len(r.json()["items"]) == 2


def test_regression_signals_endpoint(setup):
    _, client = setup
    cid = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}).json()["id"]
    r = client.get(f"/v1/comparisons/{cid}/regression-signals")
    assert r.status_code == 200
    sigs = r.json()
    assert len(sigs) == 1
    assert sigs[0]["type"] == "pass_rate_drop"


def test_regression_signals_cross_tenant_denial(setup):
    app, client = setup
    cid = client.post("/v1/comparisons", json={"left_run_id": "r1", "right_run_id": "r2"}).json()["id"]
    app.state._test_ctx = _ctx("t_b")
    r = client.get(f"/v1/comparisons/{cid}/regression-signals")
    assert r.status_code == 403


# --- FH-Tier-2 Slice 2: secret-leak reject at the HTTP boundary (additive) ---

def test_create_secret_config_rejected_422(setup):
    app, client = setup
    resp = client.post(
        "/v1/comparisons",
        json={"left_run_id": "r1", "right_run_id": "r2", "config": {"api_key": "sk_live_x"}},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "secret_leak_detected"
    assert body["error"]["details"]["field_path"] == "api_key"


def test_create_secret_config_emits_audit(setup):
    app, client = setup
    client.post(
        "/v1/comparisons",
        json={"left_run_id": "r1", "right_run_id": "r2", "config": {"api_key": "sk_live_x"}},
    )
    leak = [e for e in audit_logger.query_all_events()
            if e.action == "comparison.create_rejected_secret_leak"]
    assert len(leak) == 1
    assert leak[0].resource_id == "comparison:create"


def test_create_secret_config_does_not_call_service(setup):
    from unittest.mock import AsyncMock
    app, client = setup
    app.state._svc.create_comparison = AsyncMock()
    resp = client.post(
        "/v1/comparisons",
        json={"left_run_id": "r1", "right_run_id": "r2", "config": {"password": "hunter2"}},
    )
    assert resp.status_code == 422
    app.state._svc.create_comparison.assert_not_called()


def test_create_clean_config_succeeds_201(setup):
    app, client = setup
    resp = client.post(
        "/v1/comparisons",
        json={"left_run_id": "r1", "right_run_id": "r2", "config": {"threshold": 0.8}},
    )
    assert resp.status_code == 201
