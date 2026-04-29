"""PostgresConversationSummaryRepository tests — Turn 2.6 Step 5 (6 tests).

Composition (Turn 2.6 plan v0.2.1 §4 Step 5):
  a. upsert + get round-trip
  b. list filters by run_id
  c. list filters by verdict
  d. get raises CrossTenantForbidden when row in different tenant
  e. get raises CrossWorkspaceForbidden when row in different workspace (AM-6)
  f. delete_for_run returns deleted summaries + idempotent re-delete
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import CrossTenantForbidden, CrossWorkspaceForbidden
from src.common.models import ActorRef, TenantContext, utcnow
from src.conversations.models import ConversationSummary
from src.conversations.repository import PostgresConversationSummaryRepository
from src.conversations.service import ConversationNotFound


def _ctx(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=ActorRef(actor_id="test-user", actor_type="human"),
    )


async def _seed_tenant_workspace(
    admin_session: AsyncSession, slug: str
) -> tuple[str, str]:
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
    admin_session: AsyncSession, tenant_id: str, workspace_id: str, run_id: str
) -> None:
    await admin_session.execute(
        text(
            "INSERT INTO runs (id, tenant_id, workspace_id, status, "
            "initiated_by_actor_id, engine_version, "
            "created_at, completed_at, started_at) "
            "VALUES (:id, :tid, :wid, 'completed', 'system', 'engine_v1', "
            "now(), now(), now())"
        ),
        {"id": run_id, "tid": tenant_id, "wid": workspace_id},
    )
    await admin_session.commit()


def _summary(
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    *,
    verdict: str = "pass",
    persona_id: str = "p1",
    conversation_id: str | None = None,
) -> ConversationSummary:
    return ConversationSummary(
        id=conversation_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        persona_id=persona_id,
        persona_name="Persona One",
        persona_type="standard",
        verdict=verdict,
        pass_rate=1.0 if verdict == "pass" else 0.0,
        turn_count=3,
        judge_scores={"grounding": 0.95},
        failure_reason=None,
        failure_category=None,
        transcript_ref=None,
        tags={},
        created_at=utcnow(),
        updated_at=utcnow(),
    )


# ---------------------------------------------------------------------------
# (a) upsert + get round-trip
# ---------------------------------------------------------------------------


async def test_postgres_conversation_summary_upsert_round_trips(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-conv-a")
    run_id = "11111111-1111-1111-1111-111111111111"
    await _seed_run(admin_session, tid, wid, run_id)
    repo = PostgresConversationSummaryRepository()

    summary = _summary(tid, wid, run_id, verdict="pass")
    await repo.upsert(summary)

    fetched = await repo.get(_ctx(tid, wid), summary.id)
    assert fetched.id == summary.id
    assert fetched.tenant_id == tid
    assert fetched.workspace_id == wid
    assert fetched.run_id == run_id
    assert fetched.verdict == "pass"
    assert fetched.pass_rate == 1.0
    assert fetched.judge_scores == {"grounding": 0.95}


# ---------------------------------------------------------------------------
# (b) list filters by run_id
# ---------------------------------------------------------------------------


async def test_postgres_conversation_summary_list_filters_by_run_id(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-conv-b")
    run_a = "22222222-2222-2222-2222-aaaaaaaaaaaa"
    run_b = "22222222-2222-2222-2222-bbbbbbbbbbbb"
    await _seed_run(admin_session, tid, wid, run_a)
    await _seed_run(admin_session, tid, wid, run_b)
    repo = PostgresConversationSummaryRepository()

    s_a1 = _summary(tid, wid, run_a)
    s_a2 = _summary(tid, wid, run_a)
    s_b1 = _summary(tid, wid, run_b)
    for s in (s_a1, s_a2, s_b1):
        await repo.upsert(s)

    in_a = await repo.list(_ctx(tid, wid), run_id=run_a)
    in_b = await repo.list(_ctx(tid, wid), run_id=run_b)

    assert {s.id for s in in_a} == {s_a1.id, s_a2.id}
    assert {s.id for s in in_b} == {s_b1.id}


# ---------------------------------------------------------------------------
# (c) list filters by verdict
# ---------------------------------------------------------------------------


async def test_postgres_conversation_summary_list_filters_by_verdict(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-conv-c")
    run_id = "33333333-3333-3333-3333-333333333333"
    await _seed_run(admin_session, tid, wid, run_id)
    repo = PostgresConversationSummaryRepository()

    s_pass = _summary(tid, wid, run_id, verdict="pass")
    s_fail = _summary(tid, wid, run_id, verdict="fail")
    await repo.upsert(s_pass)
    await repo.upsert(s_fail)

    pass_list = await repo.list(_ctx(tid, wid), verdict="pass")
    fail_list = await repo.list(_ctx(tid, wid), verdict="fail")

    assert {s.id for s in pass_list} == {s_pass.id}
    assert {s.id for s in fail_list} == {s_fail.id}


# ---------------------------------------------------------------------------
# (d) get raises CrossTenantForbidden when row in different tenant
# ---------------------------------------------------------------------------


async def test_postgres_conversation_summary_get_raises_cross_tenant_forbidden(
    admin_session: AsyncSession,
) -> None:
    tid_a, wid_a = await _seed_tenant_workspace(admin_session, "t26-conv-d-a")
    tid_b, wid_b = await _seed_tenant_workspace(admin_session, "t26-conv-d-b")
    run_b = "44444444-4444-4444-4444-bbbbbbbbbbbb"
    await _seed_run(admin_session, tid_b, wid_b, run_b)

    repo = PostgresConversationSummaryRepository()
    summary_in_b = _summary(tid_b, wid_b, run_b)
    await repo.upsert(summary_in_b)

    with pytest.raises(CrossTenantForbidden, match=summary_in_b.id):
        await repo.get(_ctx(tid_a, wid_a), summary_in_b.id)


# ---------------------------------------------------------------------------
# (e) get raises CrossWorkspaceForbidden when row in different workspace (AM-6)
# ---------------------------------------------------------------------------


async def test_postgres_conversation_summary_get_raises_cross_workspace_forbidden(
    admin_session: AsyncSession,
) -> None:
    tid, w1 = await _seed_tenant_workspace(admin_session, "t26-conv-e")
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": tid},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    run_in_w2 = "55555555-5555-5555-5555-555555555555"
    await _seed_run(admin_session, tid, w2, run_in_w2)

    repo = PostgresConversationSummaryRepository()
    summary_in_w2 = _summary(tid, w2, run_in_w2)
    await repo.upsert(summary_in_w2)

    with pytest.raises(CrossWorkspaceForbidden, match=summary_in_w2.id):
        await repo.get(_ctx(tid, w1), summary_in_w2.id)


# ---------------------------------------------------------------------------
# (f) delete_for_run returns deleted summaries + idempotent re-delete
# ---------------------------------------------------------------------------


async def test_postgres_conversation_summary_delete_for_run_returns_summaries(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-conv-f")
    run_id = "66666666-6666-6666-6666-666666666666"
    other_run = "66666666-6666-6666-6666-777777777777"
    await _seed_run(admin_session, tid, wid, run_id)
    await _seed_run(admin_session, tid, wid, other_run)

    repo = PostgresConversationSummaryRepository()
    s1 = _summary(tid, wid, run_id)
    s2 = _summary(tid, wid, run_id)
    s_other = _summary(tid, wid, other_run)
    for s in (s1, s2, s_other):
        await repo.upsert(s)

    # First delete returns the two run-scoped summaries
    deleted = await repo.delete_for_run(_ctx(tid, wid), run_id)
    assert {s.id for s in deleted} == {s1.id, s2.id}
    assert all(s.run_id == run_id for s in deleted)

    # Idempotent re-delete: returns empty list, does not raise
    re_deleted = await repo.delete_for_run(_ctx(tid, wid), run_id)
    assert re_deleted == []

    # The other run's summary is untouched
    fetched = await repo.get(_ctx(tid, wid), s_other.id)
    assert fetched.id == s_other.id

    # Confirm s1/s2 are gone — get raises ConversationNotFound (different
    # from the cross-* errors because it's a within-scope true miss).
    with pytest.raises(ConversationNotFound):
        await repo.get(_ctx(tid, wid), s1.id)
