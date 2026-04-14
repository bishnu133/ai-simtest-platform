"""Dashboard artifact repository (v1.2.2 §S2 §11.8).

Returns assembled artifacts, NOT raw rows. The shape is intentionally
projection-friendly so a future precomputed dashboard projection store
is a drop-in replacement.

Week 6a uses an in-memory implementation. Tests seed it directly.
"""
from __future__ import annotations

from typing import Protocol

from src.common.models import TenantContext
from src.results.models import (
    CoverageMetric,
    FailurePattern,
    JudgeBreakdown,
    RunOverview,
)


class DashboardArtifactRepository(Protocol):
    async def get_overview(self, ctx: TenantContext, run_id: str) -> RunOverview | None: ...
    async def get_judges(self, ctx: TenantContext, run_id: str) -> list[JudgeBreakdown]: ...
    async def get_failures(self, ctx: TenantContext, run_id: str) -> list[FailurePattern]: ...
    async def get_coverage(self, ctx: TenantContext, run_id: str) -> list[CoverageMetric]: ...


class InMemoryDashboardArtifactRepository:
    def __init__(self) -> None:
        self._overviews: dict[tuple[str, str], RunOverview] = {}
        self._judges: dict[tuple[str, str], list[JudgeBreakdown]] = {}
        self._failures: dict[tuple[str, str], list[FailurePattern]] = {}
        self._coverage: dict[tuple[str, str], list[CoverageMetric]] = {}

    def _k(self, ctx: TenantContext, run_id: str) -> tuple[str, str]:
        return (ctx.tenant_id, run_id)

    def seed(
        self,
        ctx: TenantContext,
        run_id: str,
        overview: RunOverview,
        judges: list[JudgeBreakdown] | None = None,
        failures: list[FailurePattern] | None = None,
        coverage: list[CoverageMetric] | None = None,
    ) -> None:
        k = self._k(ctx, run_id)
        self._overviews[k] = overview
        self._judges[k] = judges or []
        self._failures[k] = failures or []
        self._coverage[k] = coverage or []

    async def get_overview(self, ctx, run_id):
        return self._overviews.get(self._k(ctx, run_id))

    async def get_judges(self, ctx, run_id):
        return self._judges.get(self._k(ctx, run_id), [])

    async def get_failures(self, ctx, run_id):
        return self._failures.get(self._k(ctx, run_id), [])

    async def get_coverage(self, ctx, run_id):
        return self._coverage.get(self._k(ctx, run_id), [])
