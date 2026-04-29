"""Dashboard artifact repository (v1.2.2 §S2 §11.8).

Returns assembled artifacts, NOT raw rows. The shape is intentionally
projection-friendly so a future precomputed dashboard projection store
is a drop-in replacement.

Turn 2.5 plan v0.2.1 §3.3 + MF-3 + RC-5 + AM-4 + AM-5 add:

  * ``upsert_artifacts(ctx, run_id, *, overview, judges, failures, coverage)``
    — production write path. Postgres impl performs run-existence
    validation (RC-5: RunNotFound / CrossWorkspaceForbidden /
    CrossTenantForbidden) before writing; in-memory impl is permissive
    (see AM-5 divergence note).

  * ``PostgresDashboardArtifactRepository`` — first Postgres adapter
    for the dashboard read/write path, gated by the
    ``use_postgres_dashboard_artifacts`` feature switch.

  * Universal ``session: AsyncSession | None = None`` kwarg on every
    method per AM-4, mirroring the Cat-B repos pattern.
"""
from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import (
    CrossTenantForbidden,
    CrossWorkspaceForbidden,
    RunNotFound,
)
from src.common.models import TenantContext
from src.db.models import DashboardArtifact as DashboardArtifactORM
from src.db.models import Run as RunORM
from src.db.session import raw_admin_session, tenant_scoped_session
from src.results.models import (
    CoverageMetric,
    FailurePattern,
    JudgeBreakdown,
    RunOverview,
)


# Discriminator values used in the dashboard_artifacts.artifact_type column.
# Single source of truth — referenced by reads, writes, and tests.
_ARTIFACT_TYPE_OVERVIEW = "overview"
_ARTIFACT_TYPE_JUDGES = "judges"
_ARTIFACT_TYPE_FAILURES = "failures"
_ARTIFACT_TYPE_COVERAGE = "coverage"


class DashboardArtifactRepository(Protocol):
    async def get_overview(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> RunOverview | None: ...

    async def get_judges(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[JudgeBreakdown]: ...

    async def get_failures(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[FailurePattern]: ...

    async def get_coverage(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[CoverageMetric]: ...

    async def upsert_artifacts(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        overview: RunOverview,
        judges: list[JudgeBreakdown] | None = None,
        failures: list[FailurePattern] | None = None,
        coverage: list[CoverageMetric] | None = None,
        session: AsyncSession | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryDashboardArtifactRepository:
    """In-memory implementation. Replaced by ``PostgresDashboardArtifactRepository``
    when ``AppSettings.use_postgres_dashboard_artifacts=True``.

    AM-5 Path A (Turn 2.6 plan v0.2.1 §4 Step 6):
      This impl validates run existence iff a ``run_existence:
      RunExistenceReading`` is injected at construction time. The default
      (None) preserves Week-6a permissive test behaviour, so existing
      tests that construct a bare ``InMemoryDashboardArtifactRepository()``
      continue to work without changes. ``app_factory._bind_services``
      injects the ``run_repo`` as ``run_existence`` in the in-memory
      branch only — the Postgres impl validates at the DB layer.
    """

    def __init__(
        self,
        run_existence: "RunExistenceReading | None" = None,
    ) -> None:
        self._overviews: dict[tuple[str, str], RunOverview] = {}
        self._judges: dict[tuple[str, str], list[JudgeBreakdown]] = {}
        self._failures: dict[tuple[str, str], list[FailurePattern]] = {}
        self._coverage: dict[tuple[str, str], list[CoverageMetric]] = {}
        self._run_existence = run_existence

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
        """Backwards-compatible test convenience.

        Preserved verbatim for the existing tests that call ``seed()``.
        ``upsert_artifacts`` is the production-shape equivalent (async,
        kwarg-only).

        ``seed()`` is a test helper and intentionally bypasses the AM-5
        run-existence check — tests that need to seed dashboard rows for
        runs the in-memory run repo doesn't track must be able to do so.
        """
        k = self._k(ctx, run_id)
        self._overviews[k] = overview
        self._judges[k] = judges or []
        self._failures[k] = failures or []
        self._coverage[k] = coverage or []

    async def upsert_artifacts(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        overview: RunOverview,
        judges: list[JudgeBreakdown] | None = None,
        failures: list[FailurePattern] | None = None,
        coverage: list[CoverageMetric] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Production write path (MF-3).

        AM-5 Path A: when ``run_existence`` was injected at construction
        time, validate run existence before upserting artifacts. Mirrors
        ``PostgresDashboardArtifactRepository.upsert_artifacts`` MF-3
        behaviour. When ``run_existence`` is None (default), the
        validation is skipped — preserves the v0.1 test surface.
        """
        if self._run_existence is not None:
            # Re-raise as RunNotFound regardless of which exception type
            # the underlying repo throws so the contract matches Postgres.
            try:
                await self._run_existence.get(ctx, run_id, session=session)
            except RunNotFound:
                raise RunNotFound(
                    f"Cannot upsert artifacts: run {run_id} not found "
                    f"in workspace {ctx.workspace_id}"
                )

        k = self._k(ctx, run_id)
        self._overviews[k] = overview
        self._judges[k] = judges or []
        self._failures[k] = failures or []
        self._coverage[k] = coverage or []

    async def get_overview(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> RunOverview | None:
        return self._overviews.get(self._k(ctx, run_id))

    async def get_judges(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> list[JudgeBreakdown]:
        return self._judges.get(self._k(ctx, run_id), [])

    async def get_failures(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> list[FailurePattern]:
        return self._failures.get(self._k(ctx, run_id), [])

    async def get_coverage(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> list[CoverageMetric]:
        return self._coverage.get(self._k(ctx, run_id), [])


# ---------------------------------------------------------------------------
# Postgres implementation (Turn 2.5 plan v0.2.1 §3.3 D-DashRepo)
# ---------------------------------------------------------------------------


class PostgresDashboardArtifactRepository:
    """SQLAlchemy 2.x async implementation of ``DashboardArtifactRepository``.

    Backing schema is the ``dashboard_artifacts`` table — a single table
    with an ``artifact_type`` discriminator and a ``payload`` JSONB
    column. One row per (tenant, workspace, run, artifact_type). The
    four artifact kinds are::

        artifact_type='overview'  → payload = {RunOverview fields}
        artifact_type='judges'    → payload = {"items": [JudgeBreakdown, ...]}
        artifact_type='failures'  → payload = {"items": [FailurePattern, ...]}
        artifact_type='coverage'  → payload = {"items": [CoverageMetric, ...]}

    Read paths use ``tenant_scoped_session`` so RLS policies on the
    ``dashboard_artifacts`` table apply automatically. Workspace
    dual-filter via explicit ``WHERE tenant_id=... AND workspace_id=...``
    clauses (defense in depth).

    §5.7 / D-Cwf info-leak guard on ``get_overview`` (the only by-id
    getter): cross-workspace probe within the same tenant raises
    ``CrossWorkspaceForbidden``. The list-shaped getters
    (``get_judges``, ``get_failures``, ``get_coverage``) return ``[]``
    for unknown runs without probing — see plan v0.2.1 §4.3 test 5.

    ``upsert_artifacts`` writes (or replaces) all four artifact kinds
    inside a single transaction. RC-5 validation runs the same probe
    ladder as ``PostgresRunRepository.get`` before writing, so a write
    against a missing or mis-scoped run raises the right domain error
    rather than a partial write that fails on FK at flush time.

    Session-kwarg contract (AM-4) is identical to ``PostgresRunRepository``:
      * ``session=None``  → repo opens its own ``tenant_scoped_session``.
      * ``session=...``   → caller has already scoped the session;
                            repo flushes only.
    """

    # ----- get_overview (by-id, with D-Cwf probe) --------------------------

    async def get_overview(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> RunOverview | None:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._get_overview_inside(s, ctx, run_id)
        else:
            return await self._get_overview_inside(session, ctx, run_id)

    async def _get_overview_inside(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
    ) -> RunOverview | None:
        primary = await s.execute(
            select(DashboardArtifactORM.payload).where(
                DashboardArtifactORM.run_id == run_id,
                DashboardArtifactORM.tenant_id == ctx.tenant_id,
                DashboardArtifactORM.workspace_id == ctx.workspace_id,
                DashboardArtifactORM.artifact_type == _ARTIFACT_TYPE_OVERVIEW,
            )
        )
        payload = primary.scalar_one_or_none()
        if payload is not None:
            return RunOverview(**payload)

        # D-Cwf probe: an overview exists for this run in a different
        # workspace under the same tenant?
        ws_probe = await s.execute(
            select(DashboardArtifactORM.id).where(
                DashboardArtifactORM.run_id == run_id,
                DashboardArtifactORM.tenant_id == ctx.tenant_id,
                DashboardArtifactORM.artifact_type == _ARTIFACT_TYPE_OVERVIEW,
            )
        )
        if ws_probe.scalar_one_or_none() is not None:
            raise CrossWorkspaceForbidden(
                f"Run {run_id} dashboard belongs to a different workspace"
            )

        return None

    # ----- list-shaped getters (no probe; [] on miss) ----------------------

    async def get_judges(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[JudgeBreakdown]:
        items = await self._get_list_payload(
            ctx, run_id, _ARTIFACT_TYPE_JUDGES, session
        )
        return [JudgeBreakdown(**i) for i in items]

    async def get_failures(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[FailurePattern]:
        items = await self._get_list_payload(
            ctx, run_id, _ARTIFACT_TYPE_FAILURES, session
        )
        return [FailurePattern(**i) for i in items]

    async def get_coverage(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[CoverageMetric]:
        items = await self._get_list_payload(
            ctx, run_id, _ARTIFACT_TYPE_COVERAGE, session
        )
        return [CoverageMetric(**i) for i in items]

    async def _get_list_payload(
        self,
        ctx: TenantContext,
        run_id: str,
        artifact_type: str,
        session: AsyncSession | None,
    ) -> list[dict[str, Any]]:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._get_list_payload_inside(
                    s, ctx, run_id, artifact_type
                )
        else:
            return await self._get_list_payload_inside(
                session, ctx, run_id, artifact_type
            )

    async def _get_list_payload_inside(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
        artifact_type: str,
    ) -> list[dict[str, Any]]:
        result = await s.execute(
            select(DashboardArtifactORM.payload).where(
                DashboardArtifactORM.run_id == run_id,
                DashboardArtifactORM.tenant_id == ctx.tenant_id,
                DashboardArtifactORM.workspace_id == ctx.workspace_id,
                DashboardArtifactORM.artifact_type == artifact_type,
            )
        )
        payload = result.scalar_one_or_none()
        if payload is None:
            return []
        return list(payload.get("items", []))

    # ----- upsert_artifacts (RC-5 validation + transactional write) --------

    async def upsert_artifacts(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        overview: RunOverview,
        judges: list[JudgeBreakdown] | None = None,
        failures: list[FailurePattern] | None = None,
        coverage: list[CoverageMetric] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Production write path (MF-3 + RC-5).

        Validates that the run exists in the same (tenant_id,
        workspace_id) before writing. On miss, raises one of:

          * ``RunNotFound`` — run does not exist anywhere.
          * ``CrossWorkspaceForbidden`` — run is in a different workspace
            within the same tenant.
          * ``CrossTenantForbidden`` — run is in a different tenant.

        Validation is inseparable from the write path: exposing it as a
        public ``validate_run_exists()`` would invite callers to do their
        own probe-then-write race. Keeping it inside ``upsert_artifacts``
        makes the contract atomic.

        On success, replaces all four artifact kinds for this run in a
        single transaction (delete-existing + insert-new per kind).
        """
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                await self._upsert_inside(
                    s, ctx, run_id,
                    overview=overview,
                    judges=judges,
                    failures=failures,
                    coverage=coverage,
                )
        else:
            await self._upsert_inside(
                session, ctx, run_id,
                overview=overview,
                judges=judges,
                failures=failures,
                coverage=coverage,
            )

    async def _upsert_inside(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
        *,
        overview: RunOverview,
        judges: list[JudgeBreakdown] | None,
        failures: list[FailurePattern] | None,
        coverage: list[CoverageMetric] | None,
    ) -> None:
        # RC-5: probe ladder for run-existence + scope validation. Same
        # shape as PostgresRunRepository.get's probe ladder.
        await self._validate_run_in_scope(s, ctx, run_id)

        # Delete-existing-then-insert-new for each artifact kind. The
        # four kinds are written as one logical document — partial
        # updates would create inconsistent dashboards.
        await s.execute(
            delete(DashboardArtifactORM).where(
                DashboardArtifactORM.run_id == run_id,
                DashboardArtifactORM.tenant_id == ctx.tenant_id,
                DashboardArtifactORM.workspace_id == ctx.workspace_id,
                DashboardArtifactORM.artifact_type.in_(
                    [
                        _ARTIFACT_TYPE_OVERVIEW,
                        _ARTIFACT_TYPE_JUDGES,
                        _ARTIFACT_TYPE_FAILURES,
                        _ARTIFACT_TYPE_COVERAGE,
                    ]
                ),
            )
        )

        rows: list[dict[str, Any]] = [
            {
                "tenant_id": ctx.tenant_id,
                "workspace_id": ctx.workspace_id,
                "run_id": run_id,
                "artifact_type": _ARTIFACT_TYPE_OVERVIEW,
                "payload": overview.model_dump(mode="json"),
            },
            {
                "tenant_id": ctx.tenant_id,
                "workspace_id": ctx.workspace_id,
                "run_id": run_id,
                "artifact_type": _ARTIFACT_TYPE_JUDGES,
                "payload": {
                    "items": [j.model_dump(mode="json") for j in (judges or [])]
                },
            },
            {
                "tenant_id": ctx.tenant_id,
                "workspace_id": ctx.workspace_id,
                "run_id": run_id,
                "artifact_type": _ARTIFACT_TYPE_FAILURES,
                "payload": {
                    "items": [f.model_dump(mode="json") for f in (failures or [])]
                },
            },
            {
                "tenant_id": ctx.tenant_id,
                "workspace_id": ctx.workspace_id,
                "run_id": run_id,
                "artifact_type": _ARTIFACT_TYPE_COVERAGE,
                "payload": {
                    "items": [c.model_dump(mode="json") for c in (coverage or [])]
                },
            },
        ]
        await s.execute(pg_insert(DashboardArtifactORM).values(rows))
        await s.flush()

    async def _validate_run_in_scope(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
    ) -> None:
        """RC-5 probe ladder. Identical shape to
        ``PostgresRunRepository._get_inside_session``.
        """
        primary = await s.execute(
            select(RunORM.id).where(
                RunORM.id == run_id,
                RunORM.tenant_id == ctx.tenant_id,
                RunORM.workspace_id == ctx.workspace_id,
            )
        )
        if primary.scalar_one_or_none() is not None:
            return  # in-scope; happy path

        # Cross-workspace probe (same tenant).
        ws_probe = await s.execute(
            select(RunORM.id).where(
                RunORM.id == run_id,
                RunORM.tenant_id == ctx.tenant_id,
            )
        )
        if ws_probe.scalar_one_or_none() is not None:
            raise CrossWorkspaceForbidden(
                f"Run {run_id} belongs to a different workspace"
            )

        # Cross-tenant probe — must use raw_admin_session (RLS bypass).
        async with raw_admin_session() as admin_s:
            tenant_probe = await admin_s.execute(
                select(RunORM.id).where(RunORM.id == run_id)
            )
            if tenant_probe.scalar_one_or_none() is not None:
                raise CrossTenantForbidden(
                    f"Run {run_id} belongs to a different tenant"
                )

        raise RunNotFound(f"Run {run_id} not found")
