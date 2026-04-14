"""Week 6a Turn 1: runs repository + RunStateTransition tests (v1.2.2 §M1, Correction 2)."""
from __future__ import annotations

import pytest

from src.api.errors import CrossTenantForbidden, RunNotFound
from src.audit.logger import AuditActions, audit_logger
from src.runs import (
    InMemoryRunRepository,
    RunRecord,
    RunRepository,
    RunStatus,
    set_dashboard_invalidator,
)
from src.runs.service import RunStateTransition


@pytest.fixture
def repo() -> InMemoryRunRepository:
    return InMemoryRunRepository()


@pytest.fixture
def sample_run() -> RunRecord:
    return RunRecord(
        run_id="run_001",
        tenant_id="tenant_a",
        workspace_id="workspace_main",
    )


@pytest.mark.asyncio
async def test_create_and_get_round_trip(repo, sample_run, ctx_tenant_a):
    await repo.create(sample_run)
    fetched = await repo.get(ctx_tenant_a, "run_001")
    assert fetched.run_id == "run_001"
    assert fetched.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_get_nonexistent_raises_not_found(repo, ctx_tenant_a):
    with pytest.raises(RunNotFound):
        await repo.get(ctx_tenant_a, "missing")


@pytest.mark.asyncio
async def test_cross_tenant_get_raises_forbidden(repo, sample_run, ctx_tenant_b):
    await repo.create(sample_run)  # belongs to tenant_a
    with pytest.raises(CrossTenantForbidden):
        await repo.get(ctx_tenant_b, "run_001")


def test_update_status_not_in_public_protocol():
    """Static guard: _update_status must NOT be on the public protocol surface (v1.2.2 §M1, Correction 1).

    `_update_status` is an internal-only method on InMemoryRunRepository.
    The public protocol RunRepository must not declare it. The runs package
    __all__ must not export it. This is the convention-based protection
    described in v1.2.2 Correction 1.
    """
    # Not declared on the protocol
    assert "_update_status" not in RunRepository.__dict__
    # Not in package public exports
    import src.runs as runs_pkg

    assert "_update_status" not in runs_pkg.__all__
    # But it does exist on the in-memory implementation as an internal helper
    assert hasattr(InMemoryRunRepository, "_update_status")


@pytest.mark.asyncio
async def test_run_state_transition_full_sequence(repo, sample_run, ctx_tenant_a):
    """Happy path: repo update -> audit event -> cache invalidation."""
    audit_logger.clear()
    invalidated: list[str] = []
    set_dashboard_invalidator(lambda run_id: invalidated.append(run_id))
    try:
        await repo.create(sample_run)
        transitions = RunStateTransition(repo)

        # queued -> running
        record = await transitions.transition(ctx_tenant_a, "run_001", RunStatus.RUNNING)
        assert record.status == RunStatus.RUNNING
        assert record.started_at is not None

        # Audit event was emitted with the canonical action code
        events = audit_logger.query(action=AuditActions.RUN_STARTED)
        assert len(events) == 1
        assert events[0].resource_id == "run_001"

        # Cache invalidator was called
        assert invalidated == ["run_001"]

        # running -> completed
        record = await transitions.transition(ctx_tenant_a, "run_001", RunStatus.COMPLETED)
        assert record.status == RunStatus.COMPLETED
        assert record.completed_at is not None
        assert invalidated == ["run_001", "run_001"]
    finally:
        # Reset to no-op so other tests aren't polluted
        set_dashboard_invalidator(lambda run_id: None)
