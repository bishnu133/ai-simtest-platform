"""B.5b-ii — ComparisonService propagates typed API errors (Q6); soft-fails others."""
from __future__ import annotations

import pytest

from src.common.models import ActorRef, TenantContext, utcnow
from src.comparisons import (
    ComparisonService,
    ComparisonStatus,
    InMemoryComparisonRepository,
    InMemoryIdempotencyStore,
)
from src.comparisons.provider import ComparisonDataNotReady, ProviderUnavailable
from src.runs import InMemoryRunRepository, RunRecord, RunService, RunStatus


def _ctx():
    return TenantContext(
        tenant_id="t_a", workspace_id="w_main",
        actor=ActorRef(actor_id="alice", actor_type="human"),
    )


async def _service(provider):
    run_repo = InMemoryRunRepository()
    for rid in ("r1", "r2"):
        await run_repo.create(RunRecord(
            run_id=rid, tenant_id="t_a", workspace_id="w_main",
            status=RunStatus.COMPLETED, completed_at=utcnow()))
    return ComparisonService(
        InMemoryComparisonRepository(), RunService(run_repo), provider,
        InMemoryIdempotencyStore())


class _NotReady:
    async def compute(self, record):
        raise ComparisonDataNotReady("snapshots not ready")


class _Unavailable:
    async def compute(self, record):
        raise ProviderUnavailable("no provider")


class _Boom:
    async def compute(self, record):
        raise ValueError("boom")


@pytest.mark.asyncio
async def test_reraises_comparison_data_not_ready_as_409():
    svc = await _service(_NotReady())
    with pytest.raises(ComparisonDataNotReady) as ei:
        await svc.create_comparison(_ctx(), "r1", "r2")
    assert ei.value.http_status == 409 and ei.value.code == "comparison_data_not_ready"


@pytest.mark.asyncio
async def test_still_reraises_provider_unavailable_as_503():
    svc = await _service(_Unavailable())
    with pytest.raises(ProviderUnavailable) as ei:
        await svc.create_comparison(_ctx(), "r1", "r2")
    assert ei.value.http_status == 503


@pytest.mark.asyncio
async def test_generic_exception_still_soft_fails():
    svc = await _service(_Boom())
    record = await svc.create_comparison(_ctx(), "r1", "r2")
    assert record.status == ComparisonStatus.FAILED
    assert "boom" in (record.error or "")
