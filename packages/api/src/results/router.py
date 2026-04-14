"""Results router — 5 endpoints (Week 6a Turn 2, v1.2.2 §4).

Note on /timeline: this endpoint is sourced from the audit log filtered
by resource_type='run' and a fixed lifecycle action whitelist. When the
run_events table lands, the data source switches but the response
contract does NOT change. (v1.2.2 §11.7 transitional commitment.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.deps import get_tenant_context
from src.common.models import TenantContext
from src.results.models import (
    CoverageMetric,
    DashboardSummary,
    FailurePattern,
    JudgeBreakdown,
    RunTimelineResponse,
)
from src.results.service import DashboardService

router = APIRouter(tags=["results"])


def _get_dashboard_service() -> DashboardService:
    raise RuntimeError("DashboardService dependency must be overridden by app wiring")


@router.get("/runs/{run_id}/dashboard", response_model=DashboardSummary)
async def get_dashboard(
    run_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: DashboardService = Depends(_get_dashboard_service),
):
    return await svc.build_dashboard_summary(ctx, run_id)


@router.get("/runs/{run_id}/judges", response_model=list[JudgeBreakdown])
async def get_judges(
    run_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: DashboardService = Depends(_get_dashboard_service),
):
    summary = await svc.build_dashboard_summary(ctx, run_id)
    return summary.judges


@router.get("/runs/{run_id}/failures", response_model=list[FailurePattern])
async def get_failures(
    run_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: DashboardService = Depends(_get_dashboard_service),
):
    summary = await svc.build_dashboard_summary(ctx, run_id)
    return summary.top_failures


@router.get("/runs/{run_id}/coverage", response_model=list[CoverageMetric])
async def get_coverage(
    run_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: DashboardService = Depends(_get_dashboard_service),
):
    summary = await svc.build_dashboard_summary(ctx, run_id)
    return summary.coverage


@router.get("/runs/{run_id}/timeline", response_model=RunTimelineResponse)
async def get_timeline(
    run_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: DashboardService = Depends(_get_dashboard_service),
):
    """Run lifecycle timeline.

    **Note:** In Week 6a, this endpoint reads from the audit log filtered
    by `resource_type="run"` and the run-lifecycle action whitelist. When
    the `run_events` table lands per the Control Plane / Execution Plane
    architecture, the data source will switch but the response contract
    will not change. UI built against this endpoint will not require
    migration.
    """
    return await svc.get_timeline(ctx, run_id)
