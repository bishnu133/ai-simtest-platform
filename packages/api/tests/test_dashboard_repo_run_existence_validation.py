"""AM-5 Path A — InMemoryDashboardArtifactRepository run-existence
validation tests (Turn 2.6 Step 6).

Composition (Turn 2.6 plan v0.2.1 §4 Step 6 + §7.2):
  (a) when a RunExistenceReading is injected and the run is absent,
      upsert_artifacts raises RunNotFound. Mirrors
      PostgresDashboardArtifactRepository.upsert_artifacts MF-3 behaviour.
  (b) when no RunExistenceReading is injected (default), upsert_artifacts
      skips validation and writes — preserves Week-6a permissive test
      surface.

Both tests are non-DB; (a) uses a stub RunExistenceReading.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import RunNotFound
from src.common.models import ActorRef, TenantContext
from src.results.models import RunOverview
from src.results.repository import InMemoryDashboardArtifactRepository
from src.runs import RunExistenceReading, RunRecord, RunStatus


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        actor=ActorRef(actor_id="test-user", actor_type="human"),
    )


def _overview(run_id: str = "r1") -> RunOverview:
    """Minimal viable RunOverview for upsert_artifacts."""
    return RunOverview(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        pass_rate=1.0,
        total_conversations=10,
    )


# ---------------------------------------------------------------------------
# Test (a) — validation raises RunNotFound when run_existence is injected
# ---------------------------------------------------------------------------


class _AlwaysAbsent:
    """Stub RunExistenceReading that raises RunNotFound for every get()."""

    async def get(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord:
        raise RunNotFound(f"Run {run_id} not found (stub)")


async def test_in_memory_dashboard_upsert_validates_run_existence_when_injected() -> None:
    """When ``run_existence`` is injected and reports the run is absent,
    ``upsert_artifacts`` must raise ``RunNotFound``.

    AM-5 Path A: this brings the in-memory impl into parity with
    ``PostgresDashboardArtifactRepository.upsert_artifacts`` MF-3
    behaviour, so test setups that inject a run repo can rely on
    consistent behaviour across both impls.
    """
    stub: RunExistenceReading = _AlwaysAbsent()
    repo = InMemoryDashboardArtifactRepository(run_existence=stub)
    ctx = _ctx()

    with pytest.raises(RunNotFound, match="run-x"):
        await repo.upsert_artifacts(
            ctx,
            "run-x",
            overview=_overview("run-x"),
        )


# ---------------------------------------------------------------------------
# Test (b) — skip validation when run_existence is None (default)
# ---------------------------------------------------------------------------


async def test_in_memory_dashboard_upsert_skips_validation_when_not_injected() -> None:
    """When ``run_existence`` is None (default), ``upsert_artifacts``
    must NOT call any run-existence probe and must succeed even for a
    run id that doesn't exist anywhere.

    Preserves the v0.1 / Week-6a permissive surface so existing tests
    that construct bare ``InMemoryDashboardArtifactRepository()`` keep
    passing without changes.
    """
    repo = InMemoryDashboardArtifactRepository()  # default: run_existence=None
    ctx = _ctx()

    # Must succeed — no run repo seeded, no run-existence injected.
    await repo.upsert_artifacts(
        ctx,
        "phantom-run",
        overview=_overview("phantom-run"),
    )

    # Verify the overview landed in the in-memory store.
    overview = await repo.get_overview(ctx, "phantom-run")
    assert overview is not None
    assert overview.run_id == "phantom-run"
