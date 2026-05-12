"""ComparisonRepository actor-persistence tests (Turn 2.7 Drift 4, plan v0.2.1 §4.4).

Three tests, split into independent functions per Bishnu's final
decision Q1 (no parametrize — sharp 392/1 target):

  1. ``test_comparison_to_orm_threads_actor_id_when_write_ctx_provided``
     Pure mapper test. Given a WriteContext with a real human actor,
     the resulting Comparison ORM row carries that actor_id.

  2. ``test_comparison_to_orm_falls_back_to_system_when_write_ctx_is_none``
     Pure mapper test. Given write_ctx=None, the mapper must fall back
     to the canonical system actor (back-compat for non-threaded
     callers like InMemory smoke fixtures).

  3. ``test_postgres_comparison_persists_real_actor_id_via_service_call``
     End-to-end. Service builds WriteContext from a TenantContext with
     a real human actor, the row's initiated_by_actor_id matches that
     actor (NOT 'system'). This is the regression guard for T2.6-D1
     resolution.

Tests 1+2 are pure (no DB). Test 3 uses the existing admin_session
fixture and seeds a tenant + workspace + 2 COMPLETED runs the same way
test_postgres_comparison_repository.py does.

Plan reference: turn_2_7_plan_v0_2_1.md §4.4
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.models import ActorRef, TenantContext, utcnow
from src.common.write_context import WriteContext
from src.comparisons.models import (
    ComparisonRecord,
    ComparisonStatus,
)
from src.comparisons.repository import PostgresComparisonRepository
from src.comparisons.service import ComparisonService
from src.db.mappers.comparison import comparison_to_orm
from src.db.models import Comparison as ComparisonORM
from src.runs import InMemoryRunRepository, RunRecord, RunService, RunStatus


# ---------------------------------------------------------------------------
# Helpers (mirrored from tests/db/test_postgres_comparison_repository.py
# so this test file is self-contained — pytest discovers fixtures from
# conftest.py automatically, but plain helper functions don't transfer).
# ---------------------------------------------------------------------------


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
) -> ComparisonRecord:
    """Build a minimal ComparisonRecord for mapper-only tests."""
    return ComparisonRecord(
        id=cid,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        left_run_id=str(uuid.uuid4()),
        right_run_id=str(uuid.uuid4()),
        status=ComparisonStatus.PENDING,
        created_at=utcnow(),
        engine_version="engine_v1",
    )


# ---------------------------------------------------------------------------
# Test 1 — mapper threads write_ctx.actor.actor_id (the threaded path)
# ---------------------------------------------------------------------------


def test_comparison_to_orm_threads_actor_id_when_write_ctx_provided() -> None:
    """When ``write_ctx`` carries a non-system ActorRef, the resulting
    Comparison ORM row's ``initiated_by_actor_id`` must equal that
    actor's actor_id (NOT the hardcoded 'system' default).

    This is the direct verification of Drift 4's mapper change in
    ``src/db/mappers/comparison.py:comparison_to_orm``. Failure modes:

      * Mapper ignores write_ctx and still hardcodes 'system'.
      * Mapper looks at the wrong field on WriteContext.
      * WriteContext's actor field is not properly carried through
        the dataclass.
    """
    record = _record(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        str(uuid.uuid4()),
    )
    write_ctx = WriteContext(
        actor=ActorRef(
            actor_id="user_42",
            actor_type="human",
            display_name="Test Human",
        ),
    )

    orm = comparison_to_orm(record, write_ctx=write_ctx)

    assert orm.initiated_by_actor_id == "user_42", (
        f"Mapper did not thread write_ctx.actor.actor_id. Expected "
        f"'user_42' (the actor_id we passed in), got "
        f"{orm.initiated_by_actor_id!r}. This is the regression mode "
        f"for T2.6-D1: a hardcoded 'system' would silently bypass the "
        f"caller's actor."
    )


# ---------------------------------------------------------------------------
# Test 2 — mapper falls back to system when write_ctx is None
# ---------------------------------------------------------------------------


def test_comparison_to_orm_falls_back_to_system_when_write_ctx_is_none() -> None:
    """When ``write_ctx`` is None (not threaded), the mapper must fall
    back to the canonical system actor.

    This is the back-compat invariant. Callers that haven't been
    threaded through (smoke scripts, fixtures, in-memory uses) still
    see 'system' as actor_id — same as Turn 2.6 behavior.

    Failure modes this catches:

      * Mapper raises on write_ctx=None (would break every non-threaded
        caller).
      * Mapper falls back to a different actor (e.g., 'anonymous' or
        the empty string) — would silently corrupt persisted history.
    """
    record = _record(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        str(uuid.uuid4()),
    )

    # Explicitly pass None to exercise the fallback path. (Calling
    # comparison_to_orm(record) with no write_ctx kwarg uses the
    # default value, which is also None — that path is exercised by
    # InMemoryComparisonRepository's tests indirectly.)
    orm = comparison_to_orm(record, write_ctx=None)

    assert orm.initiated_by_actor_id == "system", (
        f"Mapper fallback path failed. With write_ctx=None, expected "
        f"'system' (back-compat for non-threaded callers), got "
        f"{orm.initiated_by_actor_id!r}."
    )


# ---------------------------------------------------------------------------
# Test 3 — end-to-end: service builds WriteContext from ctx, real actor persists
# ---------------------------------------------------------------------------


async def test_postgres_comparison_persists_real_actor_id_via_service_call(
    admin_session: AsyncSession,
) -> None:
    """End-to-end Drift 4 verification: when ``ComparisonService.create_comparison``
    is called with a TenantContext carrying a real human actor, the
    persisted row's ``initiated_by_actor_id`` reflects that actor and
    NOT the hardcoded 'system'.

    Procedure:
      1. Seed a tenant + workspace + 2 COMPLETED runs in Postgres.
      2. Build a TenantContext with a non-system actor (a real human).
      3. Invoke service.create_comparison(ctx, left, right) — the
         service builds a WriteContext from ctx and threads it through
         to repo.create() → mapper → ORM row.
      4. Read the row back via raw SQL (admin session bypasses RLS for
         the verification SELECT) and assert
         initiated_by_actor_id == ctx.actor.actor_id.

    Why this test matters more than tests 1+2:
        Tests 1+2 are pure mapper tests — they pass even if the SERVICE
        forgets to build a WriteContext. This test is the regression
        guard for the *full chain*: TenantContext → service →
        WriteContext.from_tenant_context → repo.create → mapper → DB.
        If any link breaks, this test fails.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t27-d4-actor")
    left_run_id = str(uuid.uuid4())
    right_run_id = str(uuid.uuid4())
    await _seed_runs(admin_session, tid, wid, left_run_id, right_run_id)

    # Build the service with the real Postgres comparison repo.
    pg_cmp_repo = PostgresComparisonRepository()
    in_mem_run_repo = InMemoryRunRepository()
    for rid in (left_run_id, right_run_id):
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

    # The TenantContext carries a real human actor. The service must
    # propagate this through to the persisted row.
    expected_actor_id = "user_drift_4_test"
    ctx = TenantContext(
        tenant_id=tid,
        workspace_id=wid,
        actor=ActorRef(
            actor_id=expected_actor_id,
            actor_type="human",
            display_name="Drift 4 Test User",
        ),
    )

    record = await service.create_comparison(ctx, left_run_id, right_run_id)

    # Verify the row directly via raw SQL — admin_session bypasses RLS,
    # which is the right layer to read for verification (we want the
    # raw column value, not the domain projection).
    row_result = await admin_session.execute(
        sa.select(ComparisonORM.initiated_by_actor_id).where(
            ComparisonORM.id == record.id
        )
    )
    persisted_actor_id = row_result.scalar_one_or_none()

    assert persisted_actor_id is not None, (
        f"Comparison row not found in DB after service.create_comparison. "
        f"Expected id={record.id!r}."
    )
    assert persisted_actor_id == expected_actor_id, (
        f"Persisted initiated_by_actor_id did not match the caller's "
        f"actor. Expected {expected_actor_id!r} (from ctx.actor), got "
        f"{persisted_actor_id!r}. T2.6-D1 regression — somewhere in "
        f"the chain (service builds WriteContext → repo.create → mapper "
        f"→ ORM column), the actor id is being lost or replaced."
    )

    # Belt-and-braces: it specifically should NOT be 'system' (the
    # Turn 2.6 hardcoded default). If it is, the mapper is still
    # ignoring write_ctx.
    assert persisted_actor_id != "system", (
        f"Persisted initiated_by_actor_id is 'system' — the Turn 2.6 "
        f"T2.6-D1 hardcoded default. Drift 4's mapper change did not "
        f"take effect for the service-flow path."
    )
