"""Week 6a Turn 3: pagination contract tests (4 tests, v1.2.2 §S4).

Note on `cmp_<i>` IDs (Turn 2.7 Drift 2):
    These tests construct ComparisonRecord(id=f"cmp_{i}", ...) values
    and write them DIRECTLY to InMemoryComparisonRepository, bypassing
    ComparisonService.create_comparison(). The ids are intentional
    sort-order fixtures (cmp_0, cmp_1, cmp_2 — the assertions check
    LIFO ordering: ["cmp_2", "cmp_1", "cmp_0"]), NOT examples of the
    service-generated id format. ComparisonService now mints
    str(uuid.uuid4()) ids per Turn 2.7 Drift 2; the cmp_<i> shape lives
    here only because plain InMemory writes accept any string id and
    short integer-suffixed ids make the sort assertion easier to read.
    Do not "fix" these to UUIDs without also rewriting the assertions.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_tenant_context
from src.api.errors import APIError, api_error_handler
from src.audit.logger import audit_logger
from src.common.models import ActorRef, TenantContext, utcnow
from src.comparisons import (
    ComparisonRecord,
    ComparisonService,
    ComparisonStatus,
    InMemoryComparisonRepository,
    InMemoryIdempotencyStore,
    RunProvenance,
)
from src.comparisons.router import _get_comparison_service, router as cmp_router
from src.runs import InMemoryRunRepository, RunService, RunStatus
from datetime import timedelta


@pytest.fixture
def setup():
    audit_logger.clear()
    cmp_repo = InMemoryComparisonRepository()
    run_repo = InMemoryRunRepository()
    cmp_svc = ComparisonService(cmp_repo, RunService(run_repo), provider=None, idempotency=InMemoryIdempotencyStore())
    # Pre-populate 3 records with controlled timestamps for sort verification
    base = utcnow()
    for i in range(3):
        rec = ComparisonRecord(
            id=f"cmp_{i}", workspace_id="w_main", tenant_id="t_a",
            left_run_id="r1", right_run_id="r2",
            status=ComparisonStatus.COMPLETED,
            created_at=base + timedelta(seconds=i),  # i=2 is newest
            left_provenance=RunProvenance(run_id="r1", engine_version="v1", run_status=RunStatus.COMPLETED),
            right_provenance=RunProvenance(run_id="r2", engine_version="v1", run_status=RunStatus.COMPLETED),
        )
        asyncio.new_event_loop().run_until_complete(cmp_repo.create(rec))

    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(cmp_router, prefix="/v1")
    app.dependency_overrides[_get_comparison_service] = lambda: cmp_svc
    ctx = TenantContext(tenant_id="t_a", workspace_id="w_main",
                        actor=ActorRef(actor_id="u", actor_type="human"))
    app.dependency_overrides[get_tenant_context] = lambda: ctx
    return TestClient(app)


def test_default_sort_order_created_at_desc(setup):
    """v1.2.2 §S4 — list returns created_at DESC."""
    body = setup.get("/v1/comparisons").json()
    ids = [item["id"] for item in body["items"]]
    # Newest first: cmp_2, cmp_1, cmp_0
    assert ids == ["cmp_2", "cmp_1", "cmp_0"]


def test_cursor_round_trip_with_has_more(setup):
    page1 = setup.get("/v1/comparisons?limit=1").json()
    assert len(page1["items"]) == 1
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None
    # Cursor is opaque base64 — clients must not parse it, but we verify shape
    decoded = json.loads(base64.urlsafe_b64decode(page1["next_cursor"].encode()).decode())
    assert "offset" in decoded


def test_pagination_stable_across_pages(setup):
    p1 = setup.get("/v1/comparisons?limit=1").json()
    p2 = setup.get(f"/v1/comparisons?limit=1&cursor={p1['next_cursor']}").json()
    p3 = setup.get(f"/v1/comparisons?limit=1&cursor={p2['next_cursor']}").json()
    all_ids = [p1["items"][0]["id"], p2["items"][0]["id"], p3["items"][0]["id"]]
    assert all_ids == ["cmp_2", "cmp_1", "cmp_0"]
    assert p3["has_more"] is False
    assert p3["next_cursor"] is None


def test_malformed_cursor_returns_422_invalid_cursor(setup):
    r = setup.get("/v1/comparisons?cursor=NOT_VALID_BASE64!!!")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_cursor"
