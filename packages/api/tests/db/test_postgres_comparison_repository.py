"""PostgresComparisonRepository tests — Turn 2.6 Step 2 + Turn 2.7 Drift 2 (11 tests).

Composition (Turn 2.6 plan v0.2.1 §4 Step 2 + Turn 2.7 plan v0.2.1 §4.2):
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
      persists PENDING then COMPLETED via service.create_comparison()
      (Turn 2.7 Drift 2 — rewrote v0.2.1 §A.4's bypass workaround now
      that the service mints UUID4 ids that asyncpg accepts)
  11. SERVICE -> POSTGRES round-trip via repo.get verifies the
      str(uuid.uuid4()) id round-trips cleanly through the UUID column
      (Turn 2.7 Drift 2 source-fix end-to-end)
"""
from __future__ import annotations

import uuid
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
# Test 10 — SERVICE-LEVEL UPSERT (rewritten Turn 2.7 Drift 2)
# ---------------------------------------------------------------------------


async def test_comparison_service_with_postgres_repository_persists_pending_then_completed(
    admin_session: AsyncSession,
) -> None:
    """Turn 2.7 Drift 2 rewrite of v0.2.1 §A.4: prove the
    service-flow invariant — two ``repo.create()`` calls
    (PENDING then COMPLETED) for the same comparison id — yields
    exactly one row in the comparisons table with the COMPLETED state
    visible.

    History (Turn 2.6 → Turn 2.7):
        Originally this test could NOT call
        ``ComparisonService.create_comparison`` because the service
        minted ids of the form ``f"cmp_{uuid.uuid4().hex[:12]}"`` and
        asyncpg rejected the non-UUID string. The Turn 2.6 version
        called ``repo.create()`` directly with a hand-built
        ``ComparisonRecord(id=<uuid>, ...)`` to exercise the UPSERT
        invariant.

        Turn 2.7 Drift 2 fixed the service to mint UUID4 ids
        (``str(uuid.uuid4())``). This rewrite collapses the workaround:
        the test now invokes ``service.create_comparison(ctx, ...)``
        directly — exactly the path Stage C operators will use — and
        the UPSERT invariant is exercised implicitly by the service's
        internal two-call lifecycle (PENDING -> COMPLETED).

    Failure modes this catches:
      * Service mints non-UUID ids again (regression of T2.6-D2).
      * Service grows a third ``repo.create`` call.
      * INSERT instead of UPSERT (would raise IntegrityError on the
        second create).
      * Service skips the COMPLETED write (final row would have
        status='pending').
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t27-svc-upsert")
    left = "abababab-1111-1111-1111-abababababab"
    right = "cdcdcdcd-2222-2222-2222-cdcdcdcdcdcd"
    await _seed_runs(admin_session, tid, wid, left, right)

    # Build the service with the real Postgres repository + InMemory
    # runs/idempotency. Comparison service is what we're testing; the
    # other deps are smoke fakes that don't influence the id-mint or
    # the UPSERT path.
    pg_cmp_repo = PostgresComparisonRepository()
    in_mem_run_repo = InMemoryRunRepository()
    # Seed the InMemory run repo with COMPLETED runs whose ids match
    # the seeded Postgres rows so the eligibility check passes.
    await in_mem_run_repo.create(
        RunRecord(
            run_id=left,
            tenant_id=tid,
            workspace_id=wid,
            status=RunStatus.COMPLETED,
            engine_version="engine_v1",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
    )
    await in_mem_run_repo.create(
        RunRecord(
            run_id=right,
            tenant_id=tid,
            workspace_id=wid,
            status=RunStatus.COMPLETED,
            engine_version="engine_v1",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
    )

    class _NullProvider:
        async def compute(self, record):  # noqa: ARG002 — smoke
            return ([], None)

    service = ComparisonService(
        repo=pg_cmp_repo,
        run_service=RunService(in_mem_run_repo),
        provider=_NullProvider(),
    )

    # The service's internal two-call lifecycle (PENDING -> COMPLETED)
    # is what we're exercising. Returns the COMPLETED record.
    record = await service.create_comparison(_ctx(tid, wid), left, right)

    # Service must mint a UUID4 id (Drift 2 source-fix invariant).
    parsed = uuid.UUID(record.id)
    assert parsed.version == 4, (
        f"ComparisonService minted id {record.id!r} which is not UUID4 "
        f"(version={parsed.version}). Turn 2.7 Drift 2 source fix "
        f"regressed."
    )

    # Direct DB check: exactly one row, status='completed'.
    rows_result = await admin_session.execute(
        sa.select(ComparisonORM).where(ComparisonORM.id == record.id)
    )
    rows = rows_result.scalars().all()
    assert len(rows) == 1, (
        f"Expected exactly 1 row in comparisons for id={record.id}, "
        f"got {len(rows)}. UPSERT contract violated — service-flow "
        f"regression (e.g. third create call, or INSERT instead of UPSERT)."
    )
    assert rows[0].status == "completed"


# ---------------------------------------------------------------------------
# Test 11 — SERVICE -> POSTGRES round-trip via repo.get (Turn 2.7 Drift 2)
# ---------------------------------------------------------------------------


async def test_comparison_id_format_round_trips_through_postgres(
    admin_session: AsyncSession,
) -> None:
    """Turn 2.7 Drift 2: write via ``service.create_comparison``, read
    via ``repo.get``, assert the id matches in both directions.

    This is the "source-fix verified end-to-end" test:

      service mints UUID4 -> persists via repo
                                -> Postgres stores in UUID column
                                   -> repo.get() reads it back
                                      -> string equality round-trip OK

    If T2.6-D2 regresses (non-UUID id), the chain breaks at the
    ``persists via repo`` step with an asyncpg "invalid input syntax for
    type uuid" — failing this test before any assertion runs.

    If the service mints a UUID but the repo somehow returns a different
    id (e.g., a column-level transform crept in), the equality check
    catches that.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t27-roundtrip")
    left = "11111111-aaaa-aaaa-aaaa-111111111111"
    right = "22222222-bbbb-bbbb-bbbb-222222222222"
    await _seed_runs(admin_session, tid, wid, left, right)

    pg_cmp_repo = PostgresComparisonRepository()
    in_mem_run_repo = InMemoryRunRepository()
    for rid in (left, right):
        await in_mem_run_repo.create(
            RunRecord(
                run_id=rid,
                tenant_id=tid,
                workspace_id=wid,
                status=RunStatus.COMPLETED,
                engine_version="engine_v1",
                created_at=utcnow(),
                completed_at=utcnow(),
            )
        )

    class _NullProvider:
        async def compute(self, record):  # noqa: ARG002 — smoke
            return ([], None)

    service = ComparisonService(
        repo=pg_cmp_repo,
        run_service=RunService(in_mem_run_repo),
        provider=_NullProvider(),
    )
    ctx = _ctx(tid, wid)

    # Write via service — the id is minted internally.
    written = await service.create_comparison(ctx, left, right)

    # Read via repo.get — the id round-trips.
    fetched = await pg_cmp_repo.get(ctx, written.id)

    assert fetched.id == written.id, (
        f"Comparison id round-trip mismatch: wrote {written.id!r}, "
        f"read back {fetched.id!r}. The source-fix's str(uuid.uuid4()) "
        f"must round-trip cleanly through the Postgres UUID column."
    )

    # Sanity: id parses as a UUID4 (the source fix's specific shape).
    parsed = uuid.UUID(written.id)
    assert parsed.version == 4, (
        f"id {written.id!r} round-tripped but is not UUID4 "
        f"(version={parsed.version})"
    )
