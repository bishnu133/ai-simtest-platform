"""Dashboard service — retrieval-only assembly (v1.2.2 §S2 query budget).

Query budget for build_dashboard_summary: max 5 repository reads, 0 storage fetches.
"""
from __future__ import annotations

from src.api.errors import RunNotFound
from src.audit.logger import AuditActions, audit_logger
from src.common.models import TenantContext, utcnow
from src.results.cache import DashboardCache
from src.results.models import (
    DashboardSummary,
    RunTimelineEvent,
    RunTimelineResponse,
)
from src.results.repository import DashboardArtifactRepository
from src.runs.service import RunService

# Whitelist of run-lifecycle action codes for /timeline (v1.2.2 §M2 Codex).
_TIMELINE_ACTION_WHITELIST = {
    AuditActions.RUN_QUEUED,
    AuditActions.RUN_STARTED,
    AuditActions.RUN_COMPLETED,
    AuditActions.RUN_FAILED,
    AuditActions.RUN_CANCELLED,
}


class DashboardService:
    def __init__(
        self,
        run_service: RunService,
        artifact_repo: DashboardArtifactRepository,
        cache: DashboardCache,
    ):
        self._runs = run_service
        self._artifacts = artifact_repo
        self._cache = cache

    async def build_dashboard_summary(
        self, ctx: TenantContext, run_id: str
    ) -> DashboardSummary:
        cached = self._cache.get(run_id)
        if cached is not None:
            return cached

        # Read 1: run record (raises RunNotFound or CrossTenantForbidden)
        run = await self._runs.get_run(ctx, run_id)
        # Reads 2-5: dashboard artifacts (5 reads total — within budget)
        overview = await self._artifacts.get_overview(ctx, run_id)
        if overview is None:
            raise RunNotFound(f"No dashboard artifacts for run {run_id}")
        judges = await self._artifacts.get_judges(ctx, run_id)
        failures = await self._artifacts.get_failures(ctx, run_id)
        coverage = await self._artifacts.get_coverage(ctx, run_id)

        summary = DashboardSummary(
            overview=overview,
            judges=judges,
            top_failures=failures[:5],
            coverage=coverage,
            engine_version=run.engine_version,
            asset_versions_used=run.metadata.get("asset_versions_used", []),
            run_status=run.status,
            generated_at=utcnow(),
            artifact_generated_at=run.completed_at or run.started_at or run.created_at,
        )
        self._cache.set(run_id, summary)
        return summary

    async def get_timeline(
        self, ctx: TenantContext, run_id: str
    ) -> RunTimelineResponse:
        # Verify the run exists and is accessible (raises 404/403 cleanly)
        await self._runs.get_run(ctx, run_id)

        events = audit_logger.query(
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            resource_id=run_id,
        )
        whitelisted = [
            RunTimelineEvent(
                action=e.action,
                actor_id=e.actor.actor_id,
                created_at=e.occurred_at,
                metadata=e.metadata,
            )
            for e in events
            if e.action in _TIMELINE_ACTION_WHITELIST and e.resource_type == "run"
        ]
        whitelisted.sort(key=lambda ev: ev.created_at)
        return RunTimelineResponse(run_id=run_id, events=whitelisted)
