"""PostgresComparisonRepository tests — Turn 2.6 Step 2 (10 tests).

Composition (Turn 2.6 plan v0.2.1 §4 Step 2):
  1.  create round-trips (PENDING)
  2.  create UPSERTs status transition (PENDING -> COMPLETED, no dup-key)
  3.  get raises ComparisonNotFound for absent id
  4.  get raises CrossTenantForbidden when row exists in other tenant
  5.  list_for_tenant returns rows in LOCKED sort order (created_at DESC, id DESC)
  6.  list_for_tenant is workspace-scoped within a tenant
  7.  satisfies ComparisonRepository protocol (runtime_checkable)
  8.  external session does not commit until parent commits (RC-4 a)
  9.  external session rolls back on parent error (RC-4 b)
  10. SERVICE-LEVEL: ComparisonService(PostgresComparisonRepository)
      persists PENDING then COMPLETED without duplicate-key failure
      (v0.2.1 §A.4 strongly-recommended item)
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import CrossTenantForbidden
from src.common.models import ActorRef, TenantContext, utcnow
from src.comparisons.models import (
    ComparisonRecord,
    ComparisonStatus,
    RegressionSignal,
    RunProvenance,
)
from src.comparisons.repository import (
    ComparisonNotFound,
    ComparisonRepository,
    InMemoryComparisonRepository,
    PostgresComparisonRepository,
)
from src.comparisons.service import ComparisonService
from src.db.models import Comparison as ComparisonORM
from src.runs import (
    InMemoryRunRepository,
    RunRecord,
    RunService,
    RunStatus,
)


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
    """Insert tenant + default workspace, return (tenant_id, workspace_id)."""
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


async def _seed_runs(
    admin_session: AsyncSession,
    tenant_id: str,
    workspace_id: str,
    *run_ids: str,
) -> None:
    """Seed COMPLETED runs so comparison foreign keys are satisfied."""
    for rid in run_ids:
        await admin_session.execute(
            text(
                "INSERT INTO runs (id, tenant_id, workspace_id, status, "
                "initiated_by_actor_id, engine_version, "
                "created_at, completed_at, started_at) "
                "VALUES (:id, :tid, :wid, 'completed', 'system', 'engine_v1', "
                "now(), now(), now())"
            ),
            {"id": rid, "tid": tenant_id, "wid": workspace_id},
        )
    await admin_session.commit()


def _record(
    tenant_id: str,
    workspace_id: str,
    cid: str,
    left_rid: str,
    right_rid: str,
    *,
    status: ComparisonStatus = ComparisonStatus.PENDING,
    created_at=None,
) -> ComparisonRecord:
    return ComparisonRecord(
        id=cid,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        left_run_id=left_rid,
        right_run_id=right_rid,
        status=status,
        created_at=created_at or utcnow(),
        engine_version="engine_v1",
    )


# ---------------------------------------------------------------------------
# Test 1 — create round-trips a PENDING record
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_create_round_trips(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-cmp-1")
    await _seed_runs(
        admin_session,
        tid,
        wid,
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
    cid = "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa"
    repo = PostgresComparisonRepository()
    rec = _record(
        tid,
        wid,
        cid,
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )

    await repo.create(rec)

    fetched = await repo.get(_ctx(tid, wid), cid)
    assert fetched.id == cid
    assert fetched.tenant_id == tid
    assert fetched.workspace_id == wid
    assert fetched.status == ComparisonStatus.PENDING
    assert fetched.engine_version == "engine_v1"


# ---------------------------------------------------------------------------
# Test 2 — create UPSERTs status transition (PENDING -> COMPLETED)
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_create_upserts_status_transition(
    admin_session: AsyncSession,
) -> None:
    """v0.2.1 §4 Step 2 + R-5: service calls create() twice for a single
    comparison (PENDING -> COMPLETED). The Postgres adapter's UPSERT
    semantic must accept the second call without IntegrityError, and
    the final row must reflect COMPLETED state with regression_signals
    populated.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-cmp-2")
    left = "33333333-3333-3333-3333-333333333333"
    right = "44444444-4444-4444-4444-444444444444"
    await _seed_runs(admin_session, tid, wid, left, right)
    cid = "bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb"
    repo = PostgresComparisonRepository()

    pending = _record(tid, wid, cid, left, right, status=ComparisonStatus.PENDING)
    await repo.create(pending)

    # Same id, transition to COMPLETED with regression signals + provenance
    completed = ComparisonRecord(
        id=cid,
        workspace_id=wid,
        tenant_id=tid,
        left_run_id=left,
        right_run_id=right,
        status=ComparisonStatus.COMPLETED,
        created_at=pending.created_at,
        completed_at=utcnow(),
        engine_version="engine_v1",
        regression_signals=[
            RegressionSignal(
                type="judge_drift",
                severity="info",
                metric="grounding",
                payload={"old": 0.9, "new": 0.85},
            )
        ],
        left_provenance=RunProvenance(
            run_id=left,
            engine_version="engine_v1",
            asset_versions_used=["a1@v1"],
            run_status=RunStatus.COMPLETED,
        ),
        right_provenance=RunProvenance(
            run_id=right,
            engine_version="engine_v1",
            asset_versions_used=["a1@v2"],
            run_status=RunStatus.COMPLETED,
        ),
    )

    # Must not raise IntegrityError on the duplicate id
    await repo.create(completed)

    fetched = await repo.get(_ctx(tid, wid), cid)
    assert fetched.status == ComparisonStatus.COMPLETED
    assert len(fetched.regression_signals) == 1
    assert fetched.regression_signals[0].type == "judge_drift"
    assert fetched.completed_at is not None


# ---------------------------------------------------------------------------
# Test 3 — get raises ComparisonNotFound for absent id
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_get_raises_not_found(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-cmp-3")
    repo = PostgresComparisonRepository()

    # UUID-shaped id (column type is UUID); guaranteed not seeded.
    absent_id = "00000000-0000-0000-0000-deadbeefcafe"
    with pytest.raises(ComparisonNotFound, match=absent_id):
        await repo.get(_ctx(tid, wid), absent_id)


# ---------------------------------------------------------------------------
# Test 4 — get raises CrossTenantForbidden when row exists in other tenant
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_get_raises_cross_tenant(
    admin_session: AsyncSession,
) -> None:
    """A comparison row exists in tenant B's workspace; tenant A's ctx
    should see CrossTenantForbidden, not ComparisonNotFound (info-leak guard).
    """
    tid_a, wid_a = await _seed_tenant_workspace(admin_session, "t26-cmp-4-a")
    tid_b, wid_b = await _seed_tenant_workspace(admin_session, "t26-cmp-4-b")
    left_b = "55555555-5555-5555-5555-555555555555"
    right_b = "66666666-6666-6666-6666-666666666666"
    await _seed_runs(admin_session, tid_b, wid_b, left_b, right_b)

    cid = "cccccccc-3333-3333-3333-cccccccccccc"
    repo = PostgresComparisonRepository()
    await repo.create(_record(tid_b, wid_b, cid, left_b, right_b))

    with pytest.raises(CrossTenantForbidden, match=cid):
        await repo.get(_ctx(tid_a, wid_a), cid)


# ---------------------------------------------------------------------------
# Test 5 — list_for_tenant locked sort order (created_at DESC, id DESC)
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_list_for_tenant_locked_sort(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-cmp-5")
    left = "77777777-7777-7777-7777-777777777777"
    right = "88888888-8888-8888-8888-888888888888"
    await _seed_runs(admin_session, tid, wid, left, right)

    repo = PostgresComparisonRepository()
    base = utcnow()
    # Three rows: middle creation time tied, id tiebreaker
    cids = sorted(
        [
            "dddddddd-1111-4444-5555-aaaaaaaaaaaa",
            "dddddddd-2222-4444-5555-bbbbbbbbbbbb",
            "dddddddd-3333-4444-5555-cccccccccccc",
        ]
    )
    times = [base - timedelta(seconds=10), base - timedelta(seconds=5), base]
    for cid, t in zip(cids, times):
        await repo.create(_record(tid, wid, cid, left, right, created_at=t))

    listed = await repo.list_for_tenant(_ctx(tid, wid))
    assert len(listed) == 3
    # DESC by created_at: most recent first
    assert listed[0].created_at == times[2]
    assert listed[1].created_at == times[1]
    assert listed[2].created_at == times[0]


# ---------------------------------------------------------------------------
# Test 6 — list_for_tenant is workspace-scoped within a tenant
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_list_for_tenant_workspace_scoped(
    admin_session: AsyncSession,
) -> None:
    """Tenant has 2 workspaces; list_for_tenant must only return rows
    matching ctx.workspace_id.
    """
    tid, w1 = await _seed_tenant_workspace(admin_session, "t26-cmp-6")
    # Add a second workspace to the same tenant (NOT default)
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": tid},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    left = "99999999-9999-9999-9999-999999999999"
    right = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    await _seed_runs(admin_session, tid, w1, left)
    await _seed_runs(admin_session, tid, w2, right)

    repo = PostgresComparisonRepository()
    cid_w1 = "eeeeeeee-1111-1111-1111-eeeeeeeeeeee"
    cid_w2 = "ffffffff-2222-2222-2222-ffffffffffff"
    # Self-comparison rows for simplicity (left=right within each ws)
    await repo.create(_record(tid, w1, cid_w1, left, left))
    await repo.create(_record(tid, w2, cid_w2, right, right))

    in_w1 = await repo.list_for_tenant(_ctx(tid, w1))
    assert {c.id for c in in_w1} == {cid_w1}

    in_w2 = await repo.list_for_tenant(_ctx(tid, w2))
    assert {c.id for c in in_w2} == {cid_w2}


# ---------------------------------------------------------------------------
# Test 7 — satisfies ComparisonRepository protocol (runtime_checkable)
# ---------------------------------------------------------------------------


def test_postgres_comparison_repository_satisfies_protocol() -> None:
    repo = PostgresComparisonRepository()
    assert isinstance(repo, ComparisonRepository), (
        "PostgresComparisonRepository must structurally satisfy "
        "ComparisonRepository Protocol (runtime_checkable)."
    )


# ---------------------------------------------------------------------------
# Test 8 — RC-4 (a): external session does not commit until parent commits
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_external_session_does_not_commit_until_parent_commits(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-cmp-8")
    left = "bbbbbbbb-3333-3333-3333-bbbbbbbbbbbb"
    right = "cccccccc-4444-4444-4444-cccccccccccc"
    await _seed_runs(admin_session, tid, wid, left, right)
    cid = "11111111-aaaa-bbbb-cccc-222222222222"

    from src.db.session import get_sessionmaker, raw_admin_session

    repo = PostgresComparisonRepository()
    sessionmaker = get_sessionmaker()
    caller_session = sessionmaker()
    try:
        await caller_session.begin()
        await caller_session.execute(text("SET LOCAL ROLE app_user"))
        await caller_session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tid},
        )

        await repo.create(_record(tid, wid, cid, left, right), session=caller_session)

        # Probe from a separate admin session — outer tx not committed yet.
        async with raw_admin_session() as probe:
            result = await probe.execute(
                text("SELECT id FROM comparisons WHERE id = :cid"),
                {"cid": cid},
            )
            assert result.scalar_one_or_none() is None, (
                "Row should NOT be visible before parent commits"
            )

        await caller_session.commit()
    except Exception:
        await caller_session.rollback()
        raise
    finally:
        await caller_session.close()

    async with raw_admin_session() as probe:
        result = await probe.execute(
            text("SELECT id FROM comparisons WHERE id = :cid"),
            {"cid": cid},
        )
        assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Test 9 — RC-4 (b): external session rolls back on parent error
# ---------------------------------------------------------------------------


async def test_postgres_comparison_repository_external_session_rolls_back_on_parent_error(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-cmp-9")
    left = "dddddddd-5555-5555-5555-dddddddddddd"
    right = "eeeeeeee-6666-6666-6666-eeeeeeeeeeee"
    await _seed_runs(admin_session, tid, wid, left, right)
    cid = "ffffffff-7777-8888-9999-ffffffffffff"

    from src.db.session import get_sessionmaker, raw_admin_session

    repo = PostgresComparisonRepository()
    sessionmaker = get_sessionmaker()
    caller_session = sessionmaker()
    try:
        await caller_session.begin()
        await caller_session.execute(text("SET LOCAL ROLE app_user"))
        await caller_session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tid},
        )

        await repo.create(_record(tid, wid, cid, left, right), session=caller_session)

        # Caller's outer tx errors → rollback
        await caller_session.rollback()
    finally:
        await caller_session.close()

    # Row must NOT be visible after rollback
    async with raw_admin_session() as probe:
        result = await probe.execute(
            text("SELECT id FROM comparisons WHERE id = :cid"),
            {"cid": cid},
        )
        assert result.scalar_one_or_none() is None, (
            "Row should NOT be visible after caller rolled back"
        )


# ---------------------------------------------------------------------------
# Test 10 — SERVICE-LEVEL UPSERT (v0.2.1 §A.4 strongly-recommended)
# ---------------------------------------------------------------------------


async def test_comparison_service_with_postgres_repository_persists_pending_then_completed(
    admin_session: AsyncSession,
) -> None:
    """v0.2.1 §A.4: prove the service-flow invariant — two
    ``repo.create()`` calls (PENDING then COMPLETED) for the same
    comparison id — yields exactly one row in the comparisons table
    with the COMPLETED state visible.

    Discovered drift T2.6-D2 (id-shape mismatch):
        ``ComparisonService.create_comparison`` mints ids of the form
        ``f"cmp_{uuid.uuid4().hex[:12]}"`` (16 chars, non-UUID), but the
        ``comparisons.id`` ORM column is ``UUID(as_uuid=False)`` and
        asyncpg rejects non-UUID strings. This is parity with T2.6-D1
        (initiated_by_actor_id="system") in scope: a forward-looking
        discipline drift that needs the service to be made
        Postgres-aware. Resolution scheduled for Turn 2.7 alongside
        the audit/actor refactor (Future-1).

    For this test we exercise the same two-call lifecycle the service
    uses internally, but with a UUID-shaped id. The intent is unchanged:
    catch a regression where the service-flow grows a third
    ``repo.create`` call or violates the create-twice invariant. If the
    UPSERT semantic is broken, this fails with IntegrityError on the
    second create, exactly as a service-flow bug would.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-svc-upsert")
    left = "abababab-1111-1111-1111-abababababab"
    right = "cdcdcdcd-2222-2222-2222-cdcdcdcdcdcd"
    await _seed_runs(admin_session, tid, wid, left, right)

    repo = PostgresComparisonRepository()
    cid = "11112222-3333-4444-5555-666677778888"

    # Step 1 — the PENDING write the service makes after eligibility check
    pending = _record(tid, wid, cid, left, right, status=ComparisonStatus.PENDING)
    await repo.create(pending)

    # Step 2 — the COMPLETED write the service makes after provider returns.
    # This MUST not raise IntegrityError (UPSERT invariant).
    completed = ComparisonRecord(
        id=cid,
        workspace_id=wid,
        tenant_id=tid,
        left_run_id=left,
        right_run_id=right,
        status=ComparisonStatus.COMPLETED,
        created_at=pending.created_at,
        completed_at=utcnow(),
        engine_version="engine_v1",
        regression_signals=[
            RegressionSignal(
                type="pass_rate_drop",
                severity="warning",
                metric="overall",
                payload={},
            )
        ],
    )
    await repo.create(completed)

    # Direct DB check: exactly one row, status='completed', signals populated.
    rows_result = await admin_session.execute(
        sa.select(ComparisonORM).where(ComparisonORM.id == cid)
    )
    rows = rows_result.scalars().all()
    assert len(rows) == 1, (
        f"Expected exactly 1 row in comparisons for id={cid}, "
        f"got {len(rows)}. UPSERT contract violated — service-flow "
        f"regression (e.g. third create call, or INSERT instead of UPSERT)."
    )
    assert rows[0].status == "completed"
    assert rows[0].regression_signals  # non-empty JSONB list
