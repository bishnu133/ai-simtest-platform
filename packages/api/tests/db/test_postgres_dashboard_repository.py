"""PostgresDashboardArtifactRepository tests — Turn 2.5 Step 3 (11 tests).

Composition (Turn 2.5 plan v0.2.1 §4.3):
  * v0.1 repo behavior: tests 1-5
  * RC-5 upsert validation: tests 6, 7, 8
  * RC-1 wiring W3, W4: tests 9, 10
  * RC-6 guardrail W7: test 11
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import (
    CrossTenantForbidden,
    CrossWorkspaceForbidden,
    RunNotFound,
)
from src.app_factory import FatalConfigurationError, create_app
from src.common.models import ActorRef, TenantContext
from src.config import AppSettings
from src.results import (
    CoverageMetric,
    FailurePattern,
    InMemoryDashboardArtifactRepository,
    JudgeBreakdown,
    PostgresDashboardArtifactRepository,
    RunOverview,
)
from src.runs import RunStatus

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _ctx(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=ActorRef(actor_id="test-user", actor_type="human"),
    )


async def _seed_tenant_workspace(
    admin_session: AsyncSession, slug: str
) -> tuple[str, str]:
    """Insert a tenant + a default workspace; return (tenant_id, workspace_id)."""
    t_result = await admin_session.execute(
        text(
            "INSERT INTO tenants (name, slug) "
            "VALUES (:name, :slug) RETURNING id"
        ),
        {"name": f"Tenant {slug}", "slug": slug},
    )
    tenant_id = str(t_result.scalar_one())
    w_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'default', true) RETURNING id"
        ),
        {"tid": tenant_id},
    )
    workspace_id = str(w_result.scalar_one())
    await admin_session.commit()
    return tenant_id, workspace_id


async def _seed_run(
    admin_session: AsyncSession,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    status: str = "queued",
) -> None:
    await admin_session.execute(
        text(
            "INSERT INTO runs (id, tenant_id, workspace_id, status, "
            "initiated_by_actor_id, engine_version) "
            "VALUES (:id, :tid, :wid, :st, 'system', 'engine_v1')"
        ),
        {"id": run_id, "tid": tenant_id, "wid": workspace_id, "st": status},
    )
    await admin_session.commit()


def _overview(run_id: str, status: RunStatus = RunStatus.COMPLETED) -> RunOverview:
    return RunOverview(
        run_id=run_id,
        status=status,
        pass_rate=0.85,
        total_conversations=100,
    )


# ---------------------------------------------------------------------------
# Test 1 — upsert + read all four artifact kinds (v0.1)
# ---------------------------------------------------------------------------


async def test_upsert_artifacts_persists_overview_judges_failures_coverage(
    admin_session: AsyncSession,
) -> None:
    """Single upsert_artifacts call writes all four kinds; all four
    get_* methods return them."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-step3-1")
    rid = "11111111-2222-2222-2222-111111111111"
    await _seed_run(admin_session, tid, wid, rid, status="completed")

    repo = PostgresDashboardArtifactRepository()
    ctx = _ctx(tid, wid)
    await repo.upsert_artifacts(
        ctx,
        rid,
        overview=_overview(rid),
        judges=[
            JudgeBreakdown(judge_name="grounding", pass_count=80, fail_count=20, avg_score=0.85),
        ],
        failures=[
            FailurePattern(cluster_id="c1", label="off-topic", count=5),
        ],
        coverage=[
            CoverageMetric(dimension="topic", covered=8, total=10),
        ],
    )

    overview = await repo.get_overview(ctx, rid)
    assert overview is not None
    assert overview.run_id == rid
    assert overview.pass_rate == 0.85

    judges = await repo.get_judges(ctx, rid)
    assert len(judges) == 1
    assert judges[0].judge_name == "grounding"

    failures = await repo.get_failures(ctx, rid)
    assert len(failures) == 1
    assert failures[0].cluster_id == "c1"

    coverage = await repo.get_coverage(ctx, rid)
    assert len(coverage) == 1
    assert coverage[0].dimension == "topic"


# ---------------------------------------------------------------------------
# Test 2 — upsert replaces (UPSERT semantic) (v0.1)
# ---------------------------------------------------------------------------


async def test_upsert_artifacts_replaces_existing_artifacts_for_same_run(
    admin_session: AsyncSession,
) -> None:
    """Second upsert_artifacts for the same run replaces (not appends)
    the dependent rows."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-step3-2")
    rid = "22222222-3333-3333-3333-222222222222"
    await _seed_run(admin_session, tid, wid, rid, status="completed")

    repo = PostgresDashboardArtifactRepository()
    ctx = _ctx(tid, wid)

    # First write — 2 judges
    await repo.upsert_artifacts(
        ctx,
        rid,
        overview=_overview(rid),
        judges=[
            JudgeBreakdown(judge_name="j1", pass_count=10, fail_count=0),
            JudgeBreakdown(judge_name="j2", pass_count=5, fail_count=5),
        ],
    )
    assert len(await repo.get_judges(ctx, rid)) == 2

    # Second write — 1 judge (different one)
    await repo.upsert_artifacts(
        ctx,
        rid,
        overview=_overview(rid),
        judges=[
            JudgeBreakdown(judge_name="j3", pass_count=20, fail_count=0),
        ],
    )
    after = await repo.get_judges(ctx, rid)
    assert len(after) == 1, "Second upsert must REPLACE, not APPEND"
    assert after[0].judge_name == "j3"


# ---------------------------------------------------------------------------
# Test 3 — get_overview happy path (v0.1)
# ---------------------------------------------------------------------------


async def test_get_overview_within_tenant_returns_record(
    admin_session: AsyncSession,
) -> None:
    """Happy-path read after upsert."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-step3-3")
    rid = "33333333-4444-4444-4444-333333333333"
    await _seed_run(admin_session, tid, wid, rid)

    repo = PostgresDashboardArtifactRepository()
    ctx = _ctx(tid, wid)
    await repo.upsert_artifacts(ctx, rid, overview=_overview(rid))

    overview = await repo.get_overview(ctx, rid)
    assert overview is not None
    assert overview.run_id == rid


# ---------------------------------------------------------------------------
# Test 4 — D-Cwf cross-workspace probe on get_overview (v0.1)
# ---------------------------------------------------------------------------


async def test_get_overview_cross_workspace_within_tenant_raises_cross_workspace_forbidden(
    admin_session: AsyncSession,
) -> None:
    """Overview exists under (T1, W2), probed via (T1, W1) →
    CrossWorkspaceForbidden (D-Cwf)."""
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-step3-4")
    # Second workspace under T1
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": t1},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    rid = "44444444-5555-5555-5555-444444444444"
    await _seed_run(admin_session, t1, w2, rid)

    repo = PostgresDashboardArtifactRepository()
    # Write artifact under W2
    await repo.upsert_artifacts(_ctx(t1, w2), rid, overview=_overview(rid))

    # Probe from W1 — must raise CrossWorkspaceForbidden
    with pytest.raises(CrossWorkspaceForbidden):
        await repo.get_overview(_ctx(t1, w1), rid)


# ---------------------------------------------------------------------------
# Test 5 — list-shaped getters return [] for unknown (v0.1)
# ---------------------------------------------------------------------------


async def test_get_judges_failures_coverage_return_empty_for_unknown_run(
    admin_session: AsyncSession,
) -> None:
    """List-shaped reads for unknown run return [] (not error). Unlike
    get_overview, they don't probe — that semantic is documented in the
    repository class docstring."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-step3-5")
    repo = PostgresDashboardArtifactRepository()
    ctx = _ctx(tid, wid)
    rid = "55555555-6666-6666-6666-555555555555"

    assert await repo.get_judges(ctx, rid) == []
    assert await repo.get_failures(ctx, rid) == []
    assert await repo.get_coverage(ctx, rid) == []


# ---------------------------------------------------------------------------
# Test 6 — RC-5 RunNotFound
# ---------------------------------------------------------------------------


async def test_upsert_artifacts_raises_run_not_found_when_run_does_not_exist(
    admin_session: AsyncSession,
) -> None:
    """RC-5: run R1 does not exist anywhere → upsert raises RunNotFound."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-step3-6")
    repo = PostgresDashboardArtifactRepository()
    ctx = _ctx(tid, wid)
    rid = "66666666-7777-7777-7777-666666666666"

    with pytest.raises(RunNotFound):
        await repo.upsert_artifacts(ctx, rid, overview=_overview(rid))


# ---------------------------------------------------------------------------
# Test 7 — RC-5 CrossWorkspaceForbidden on upsert
# ---------------------------------------------------------------------------


async def test_upsert_artifacts_raises_cross_workspace_forbidden_when_run_in_different_workspace_same_tenant(
    admin_session: AsyncSession,
) -> None:
    """RC-5: run R1 is in (T1, W2). Caller upserts artifacts via
    ctx={T1, W1} → CrossWorkspaceForbidden."""
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-step3-7")
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": t1},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    rid = "77777777-8888-8888-8888-777777777777"
    await _seed_run(admin_session, t1, w2, rid)

    repo = PostgresDashboardArtifactRepository()
    with pytest.raises(CrossWorkspaceForbidden):
        await repo.upsert_artifacts(_ctx(t1, w1), rid, overview=_overview(rid))


# ---------------------------------------------------------------------------
# Test 8 — RC-5 CrossTenantForbidden on upsert
# ---------------------------------------------------------------------------


async def test_upsert_artifacts_raises_cross_tenant_forbidden_when_run_in_different_tenant(
    admin_session: AsyncSession,
) -> None:
    """RC-5: run R1 is in T2. Caller upserts artifacts via ctx={T1, W1}
    → CrossTenantForbidden."""
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-step3-8")
    t2, w_t2 = await _seed_tenant_workspace(admin_session, "t2-step3-8")
    rid = "88888888-9999-9999-9999-888888888888"
    await _seed_run(admin_session, t2, w_t2, rid)

    repo = PostgresDashboardArtifactRepository()
    with pytest.raises(CrossTenantForbidden):
        await repo.upsert_artifacts(_ctx(t1, w1), rid, overview=_overview(rid))


# ---------------------------------------------------------------------------
# Test 9 — W3 default uses InMemoryDashboardArtifactRepository
# ---------------------------------------------------------------------------


async def test_app_factory_default_uses_in_memory_dashboard_artifact_repository() -> None:
    """W3: create_app(default settings) → app.state.dashboard_artifact_repo
    is InMemoryDashboardArtifactRepository.

    Explicit database_url=None defeats the conftest autouse-fixture's
    DATABASE_URL env-var pollution.
    """
    app = create_app(
        settings=AppSettings(app_env="test", database_url=None)
    )
    assert isinstance(
        app.state.dashboard_artifact_repo,
        InMemoryDashboardArtifactRepository,
    )


# ---------------------------------------------------------------------------
# Test 10 — W4 use_postgres_dashboard_artifacts wires Postgres impl
# ---------------------------------------------------------------------------


async def test_app_factory_with_use_postgres_dashboard_artifacts_uses_postgres_repository(
    pg_url: str,
    clean_db: str,
) -> None:
    """W4: AppSettings(use_postgres_dashboard_artifacts=True,
    database_url=pg_url) → app.state.dashboard_artifact_repo is
    PostgresDashboardArtifactRepository."""
    app = create_app(
        settings=AppSettings(
            app_env="test",
            database_url=pg_url,
            use_postgres_dashboard_artifacts=True,
        )
    )
    assert isinstance(
        app.state.dashboard_artifact_repo,
        PostgresDashboardArtifactRepository,
    )


# ---------------------------------------------------------------------------
# Test 11 — W7 guardrail: use_postgres_dashboard_artifacts without DB raises
# ---------------------------------------------------------------------------


async def test_use_postgres_dashboard_artifacts_without_database_url_raises_fatal_configuration_error() -> None:
    """W7: AppSettings(use_postgres_dashboard_artifacts=True,
    database_url=None) → create_app raises FatalConfigurationError.

    Explicit database_url=None defeats the conftest autouse-fixture's
    DATABASE_URL env-var pollution.
    """
    with pytest.raises(FatalConfigurationError, match="database_url"):
        create_app(
            settings=AppSettings(
                app_env="test",
                database_url=None,
                use_postgres_dashboard_artifacts=True,
            )
        )
