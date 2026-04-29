"""PostgresRunRepository tests — Turn 2.5 Step 2 (17 tests).

Composition (Turn 2.5 plan v0.2.1 §4.2):
  * v0.1 repo behavior: tests 1-10
  * RC-3 trust-boundary: test 11
  * RC-4 transaction-boundary: tests 12, 13
  * RC-1 wiring W1: test 14
  * RC-1 wiring W2 merged with RC-2 wiring W5: test 15
  * RC-6 guardrail W6: test 16
  * AM-6 cross-workspace status mutation: test 17
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
from src.runs import (
    InMemoryRunRepository,
    PostgresRunRepository,
    RunRecord,
    RunStatus,
    RunStatusMutating,
    RunService,
)

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
    """Insert a tenant + a default workspace; return (tenant_id, workspace_id).

    Inserts via raw SQL so tests don't depend on the workspace repo (which
    is itself under test in a sibling file). Returns UUIDs as strings.
    """
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
    """Insert a run row directly via admin_session for cross-tenant /
    cross-workspace probe tests (which need rows that bypass repo write
    paths)."""
    await admin_session.execute(
        text(
            "INSERT INTO runs (id, tenant_id, workspace_id, status, "
            "initiated_by_actor_id, engine_version) "
            "VALUES (:id, :tid, :wid, :st, 'system', 'engine_v1')"
        ),
        {"id": run_id, "tid": tenant_id, "wid": workspace_id, "st": status},
    )
    await admin_session.commit()


def _record(
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    status: RunStatus = RunStatus.QUEUED,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status=status,
    )


# ---------------------------------------------------------------------------
# Test 1 — create + get round-trip (v0.1)
# ---------------------------------------------------------------------------


async def test_create_persists_run_and_returns_record(
    admin_session: AsyncSession,
) -> None:
    """create(record) writes to runs; get(...) returns the same record."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-create")
    repo = PostgresRunRepository()

    rid = "11111111-1111-1111-1111-111111111111"
    await repo.create(_record(tid, wid, rid))

    fetched = await repo.get(_ctx(tid, wid), rid)
    assert fetched.run_id == rid
    assert fetched.tenant_id == tid
    assert fetched.workspace_id == wid
    assert fetched.status == RunStatus.QUEUED


# ---------------------------------------------------------------------------
# Test 2 — get within tenant (v0.1)
# ---------------------------------------------------------------------------


async def test_get_by_id_within_tenant_returns_record(
    admin_session: AsyncSession,
) -> None:
    """Happy-path read inside tenant + workspace context."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-get-within")
    rid = "22222222-2222-2222-2222-222222222222"
    await _seed_run(admin_session, tid, wid, rid)

    repo = PostgresRunRepository()
    fetched = await repo.get(_ctx(tid, wid), rid)
    assert fetched.run_id == rid


# ---------------------------------------------------------------------------
# Test 3 — get unknown raises RunNotFound (v0.1)
# ---------------------------------------------------------------------------


async def test_get_by_id_unknown_raises_run_not_found(
    admin_session: AsyncSession,
) -> None:
    """No row anywhere → RunNotFound."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-get-unknown")
    repo = PostgresRunRepository()

    with pytest.raises(RunNotFound):
        await repo.get(
            _ctx(tid, wid), "33333333-3333-3333-3333-333333333333"
        )


# ---------------------------------------------------------------------------
# Test 4 — cross-tenant (§5.7 info-leak guard) (v0.1)
# ---------------------------------------------------------------------------


async def test_get_cross_tenant_raises_cross_tenant_forbidden(
    admin_session: AsyncSession,
) -> None:
    """Row exists under T2, accessed via T1 ctx → CrossTenantForbidden."""
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-cross-tenant")
    t2, w2 = await _seed_tenant_workspace(admin_session, "t2-cross-tenant")
    rid = "44444444-4444-4444-4444-444444444444"
    await _seed_run(admin_session, t2, w2, rid)

    repo = PostgresRunRepository()
    with pytest.raises(CrossTenantForbidden):
        await repo.get(_ctx(t1, w1), rid)


# ---------------------------------------------------------------------------
# Test 5 — cross-workspace within tenant (D-Cwf) (v0.1)
# ---------------------------------------------------------------------------


async def test_get_cross_workspace_within_tenant_raises_cross_workspace_forbidden(
    admin_session: AsyncSession,
) -> None:
    """Row exists under (T1, W2), accessed via (T1, W1) → CrossWorkspaceForbidden."""
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-cross-ws")
    # Create a second workspace under the same tenant.
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": t1},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    rid = "55555555-5555-5555-5555-555555555555"
    await _seed_run(admin_session, t1, w2, rid)

    repo = PostgresRunRepository()
    with pytest.raises(CrossWorkspaceForbidden):
        await repo.get(_ctx(t1, w1), rid)


# ---------------------------------------------------------------------------
# Test 6 — list scoped to current workspace (v0.1)
# ---------------------------------------------------------------------------


async def test_list_for_tenant_returns_only_current_workspace_rows(
    admin_session: AsyncSession,
) -> None:
    """Insert (T1,W1) + (T1,W2) + (T2,W3) runs; list under (T1,W1) returns
    only the T1/W1 row."""
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-list-scoped-a")
    t2, w3 = await _seed_tenant_workspace(admin_session, "t2-list-scoped-b")
    # Second workspace under T1.
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": t1},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    await _seed_run(
        admin_session, t1, w1, "66666666-6666-6666-6666-666666666666"
    )
    await _seed_run(
        admin_session, t1, w2, "66666666-6666-6666-6666-666666666661"
    )
    await _seed_run(
        admin_session, t2, w3, "66666666-6666-6666-6666-666666666662"
    )

    repo = PostgresRunRepository()
    rows = await repo.list_for_tenant(_ctx(t1, w1))
    assert len(rows) == 1
    assert rows[0].run_id == "66666666-6666-6666-6666-666666666666"


# ---------------------------------------------------------------------------
# Test 7 — list empty (v0.1)
# ---------------------------------------------------------------------------


async def test_list_for_tenant_empty_returns_empty_list(
    admin_session: AsyncSession,
) -> None:
    """No rows → empty list (not None)."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-list-empty")
    repo = PostgresRunRepository()
    rows = await repo.list_for_tenant(_ctx(tid, wid))
    assert rows == []


# ---------------------------------------------------------------------------
# Test 8 — _update_status sets started_at on RUNNING (v0.1)
# ---------------------------------------------------------------------------


async def test_update_status_advances_record_and_sets_started_at_on_running(
    admin_session: AsyncSession,
) -> None:
    """_update_status(RUNNING) sets started_at if previously None."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-upd-running")
    rid = "77777777-7777-7777-7777-777777777777"
    await _seed_run(admin_session, tid, wid, rid, status="queued")

    repo = PostgresRunRepository()
    updated = await repo._update_status(_ctx(tid, wid), rid, RunStatus.RUNNING)
    assert updated.status == RunStatus.RUNNING
    assert updated.started_at is not None
    assert updated.completed_at is None


# ---------------------------------------------------------------------------
# Test 9 — _update_status sets completed_at on terminal states (v0.1)
# ---------------------------------------------------------------------------


async def test_update_status_sets_completed_at_on_terminal_state(
    admin_session: AsyncSession,
) -> None:
    """_update_status(COMPLETED|FAILED) sets completed_at."""
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-upd-terminal")
    repo = PostgresRunRepository()

    rid_completed = "88888888-8888-8888-8888-888888888881"
    rid_failed = "88888888-8888-8888-8888-888888888882"
    await _seed_run(admin_session, tid, wid, rid_completed, status="running")
    await _seed_run(admin_session, tid, wid, rid_failed, status="running")

    upd1 = await repo._update_status(
        _ctx(tid, wid), rid_completed, RunStatus.COMPLETED
    )
    assert upd1.status == RunStatus.COMPLETED
    assert upd1.completed_at is not None

    upd2 = await repo._update_status(
        _ctx(tid, wid), rid_failed, RunStatus.FAILED
    )
    assert upd2.status == RunStatus.FAILED
    assert upd2.completed_at is not None


# ---------------------------------------------------------------------------
# Test 10 — RunStatusMutating protocol satisfaction + RunService wiring (MF-2)
# ---------------------------------------------------------------------------


async def test_postgres_run_repository_satisfies_run_status_mutating_protocol(
    clean_db: str,
) -> None:
    """isinstance(repo, RunStatusMutating) is True at runtime;
    RunService(repo).transitions._repo is repo. Proves MF-2 wiring."""
    repo = PostgresRunRepository()
    assert isinstance(repo, RunStatusMutating)

    svc = RunService(repo)
    assert svc.transitions._repo is repo


# ---------------------------------------------------------------------------
# Test 11 — RC-3 trust boundary check
# ---------------------------------------------------------------------------


async def test_create_rejects_record_with_empty_tenant_or_workspace_or_run_id(
    clean_db: str,
) -> None:
    """RC-3 belt-and-braces: PostgresRunRepository.create with an empty
    tenant_id, workspace_id, or run_id raises TypeError before touching
    the DB."""
    repo = PostgresRunRepository()

    # Empty tenant_id
    with pytest.raises(TypeError, match="tenant_id and workspace_id"):
        await repo.create(
            RunRecord(run_id="r1", tenant_id="", workspace_id="w1")
        )

    # Empty workspace_id
    with pytest.raises(TypeError, match="tenant_id and workspace_id"):
        await repo.create(
            RunRecord(run_id="r1", tenant_id="t1", workspace_id="")
        )

    # Empty run_id
    with pytest.raises(TypeError, match="run_id"):
        await repo.create(
            RunRecord(run_id="", tenant_id="t1", workspace_id="w1")
        )


# ---------------------------------------------------------------------------
# Test 12 — RC-4 (a) external session does not commit until parent commits
# ---------------------------------------------------------------------------


async def test_external_session_create_does_not_commit_until_parent_commits(
    admin_session: AsyncSession,
) -> None:
    """RC-4 (a): when caller supplies a session, the row should not be
    visible from a separate admin session until the caller's outer
    transaction commits.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-ext-commit")
    rid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    repo = PostgresRunRepository()

    # Open a caller-owned session, scoped to the same tenant. Insert
    # without committing.
    from src.db.session import tenant_scoped_session

    # We need to drive the caller-owned session manually so we can
    # assert intermediate visibility.
    from src.db.session import get_sessionmaker

    sessionmaker = get_sessionmaker()
    caller_session = sessionmaker()
    try:
        await caller_session.begin()
        await caller_session.execute(text("SET LOCAL ROLE app_user"))
        await caller_session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tid},
        )

        # Caller-owned create — repo should flush only, not commit.
        await repo.create(_record(tid, wid, rid), session=caller_session)

        # Probe from a *separate* admin session — outer tx not committed yet.
        from src.db.session import raw_admin_session

        async with raw_admin_session() as probe:
            result = await probe.execute(
                text("SELECT id FROM runs WHERE id = :rid"),
                {"rid": rid},
            )
            assert result.scalar_one_or_none() is None, (
                "Row should NOT be visible before parent commits"
            )

        # Now parent commits.
        await caller_session.commit()
    except Exception:
        await caller_session.rollback()
        raise
    finally:
        await caller_session.close()

    # After parent commit, the row IS visible.
    from src.db.session import raw_admin_session

    async with raw_admin_session() as probe:
        result = await probe.execute(
            text("SELECT id FROM runs WHERE id = :rid"),
            {"rid": rid},
        )
        assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Test 13 — RC-4 (b) external session rolls back on parent error
# ---------------------------------------------------------------------------


async def test_external_session_create_rolls_back_on_parent_error(
    admin_session: AsyncSession,
) -> None:
    """RC-4 (b): when caller's outer transaction raises after a
    repo.create with caller-supplied session, the row must NOT be visible
    afterwards.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-ext-rollback")
    rid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    repo = PostgresRunRepository()

    from src.db.session import get_sessionmaker, raw_admin_session

    sessionmaker = get_sessionmaker()
    caller_session = sessionmaker()

    class _ParentError(Exception):
        pass

    try:
        try:
            await caller_session.begin()
            await caller_session.execute(text("SET LOCAL ROLE app_user"))
            await caller_session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": tid},
            )
            await repo.create(_record(tid, wid, rid), session=caller_session)
            # Simulate a parent-level error.
            raise _ParentError("simulated")
        except _ParentError:
            await caller_session.rollback()
    finally:
        await caller_session.close()

    # Row must NOT be visible — parent rolled back.
    async with raw_admin_session() as probe:
        result = await probe.execute(
            text("SELECT id FROM runs WHERE id = :rid"),
            {"rid": rid},
        )
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Test 14 — W1 default uses InMemoryRunRepository
# ---------------------------------------------------------------------------


async def test_app_factory_default_uses_in_memory_run_repository() -> None:
    """W1: create_app(default settings) → app.state.run_repo is
    InMemoryRunRepository.

    Explicit database_url=None defeats the conftest autouse-fixture's
    DATABASE_URL env-var pollution (Drift #7 carry-forward) so the test
    proves the literal "no DB, no Postgres" default behaviour.
    """
    app = create_app(
        settings=AppSettings(app_env="test", database_url=None)
    )
    assert isinstance(app.state.run_repo, InMemoryRunRepository)


# ---------------------------------------------------------------------------
# Test 15 — W2 + W5 merged: switch wires PostgresRunRepository against settings DB
# ---------------------------------------------------------------------------


async def test_app_factory_with_use_postgres_runs_uses_postgres_run_repository_against_settings_db(
    pg_url: str,
    admin_session: AsyncSession,
) -> None:
    """W2 + W5: AppSettings(use_postgres_runs=True, database_url=pg_url)
    → app.state.run_repo is PostgresRunRepository AND queries it executes
    hit the same Postgres instance pg_url points at.

    Proven by inserting via admin_session and reading back through the
    repo (W5 engine source-of-truth proof).
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t1-w2-w5-merged")
    rid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    await _seed_run(admin_session, tid, wid, rid, status="queued")

    app = create_app(
        settings=AppSettings(
            app_env="test",
            database_url=pg_url,
            use_postgres_runs=True,
        )
    )
    # W2: type assertion
    assert isinstance(app.state.run_repo, PostgresRunRepository)

    # W5: engine-source-of-truth proof — repo reads the row admin_session
    # wrote.
    fetched = await app.state.run_repo.get(_ctx(tid, wid), rid)
    assert fetched.run_id == rid


# ---------------------------------------------------------------------------
# Test 16 — W6 guardrail: use_postgres_runs without database_url raises
# ---------------------------------------------------------------------------


async def test_use_postgres_runs_without_database_url_raises_fatal_configuration_error() -> None:
    """W6: AppSettings(use_postgres_runs=True, database_url=None) →
    create_app raises FatalConfigurationError.

    Explicit database_url=None defeats the conftest autouse-fixture's
    DATABASE_URL env-var pollution (Drift #7 carry-forward) so the test
    proves the literal guardrail behaviour, not just a misconfigured env.
    """
    with pytest.raises(FatalConfigurationError, match="database_url"):
        create_app(
            settings=AppSettings(
                app_env="test",
                database_url=None,
                use_postgres_runs=True,
            )
        )


# ---------------------------------------------------------------------------
# Test 17 — AM-6 cross-workspace status mutation is forbidden
# ---------------------------------------------------------------------------


async def test_update_status_cross_workspace_raises_cross_workspace_forbidden(
    admin_session: AsyncSession,
) -> None:
    """AM-6: _update_status() must use the same probe ladder as get(),
    so cross-workspace and cross-tenant status mutations both raise the
    correct domain errors.

    Two assertions in one test body:
      1. Run R1 under (T1, W2), accessed via (T1, W1) → CrossWorkspaceForbidden.
      2. Run R2 under (T2, W?), accessed via (T1, ...) → CrossTenantForbidden.
    """
    t1, w1 = await _seed_tenant_workspace(admin_session, "t1-am6-xws")
    t2, w_t2 = await _seed_tenant_workspace(admin_session, "t2-am6-xtenant")
    # Second workspace under T1.
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": t1},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    r1 = "dddddddd-dddd-dddd-dddd-dddddddddd01"
    r2 = "dddddddd-dddd-dddd-dddd-dddddddddd02"
    await _seed_run(admin_session, t1, w2, r1, status="queued")
    await _seed_run(admin_session, t2, w_t2, r2, status="queued")

    repo = PostgresRunRepository()

    # Assertion 1 — cross-workspace within tenant.
    with pytest.raises(CrossWorkspaceForbidden):
        await repo._update_status(_ctx(t1, w1), r1, RunStatus.RUNNING)

    # Assertion 2 — cross-tenant.
    with pytest.raises(CrossTenantForbidden):
        await repo._update_status(_ctx(t1, w1), r2, RunStatus.RUNNING)
