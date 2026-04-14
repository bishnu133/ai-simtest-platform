"""Mapper round-trip tests (plan §8 Turn 1b — 26 tests).

Strategy:
  - Pure mappers (no DB needed): constructed ORM object → domain → ORM,
    assert field equality. These verify the mapper logic in isolation.
  - DB-backed mappers: INSERT via to_orm, SELECT, read via to_domain,
    assert round-trip equality. These verify the mapper produces ORM
    objects Postgres will actually accept (column types, NOT NULL,
    CHECK constraints, etc.).

The 11 RunStatus cases are parametrized over every value in the v2 §6.1
11-state persisted enum, split cleanly between "in-code (5, round-trips)"
and "future (6, raises RunStatusNotYetSupported)".

Test count breakdown (26 total):
  - test_tenant_roundtrip_via_db              (1)
  - test_workspace_roundtrip_via_db           (1)
  - test_membership_tenant_level_roundtrip    (1)
  - test_membership_workspace_level_roundtrip (1)
  - test_asset_inline_roundtrip_via_db        (1)
  - test_asset_object_tier_roundtrip_via_db   (1)
  - test_asset_with_approved_by_roundtrip     (1)
  - test_run_roundtrip_via_db                 (1)
  - test_run_metadata_carries_through         (1)
  - test_conversation_summary_roundtrip       (1)
  - test_conversation_summary_with_transcript (1)
  - test_comparison_roundtrip_via_db          (1)
  - test_comparison_with_typed_signals        (1)
  - test_dashboard_artifact_roundtrip         (1)
  - test_idempotency_key_roundtrip            (1)
  - test_audit_event_roundtrip                (1)
  - test_run_status_forward_identity          (5 parametrized — one per in-code state)
  - test_run_status_reverse_future_raises     (6 parametrized — one per future state)

  Total: 5 + 6 + 15 = 26 ✅
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src.assets.models import AssetRecord
from src.common.models import ActorRef, AssetStatus, AssetType
from src.comparisons.models import (
    ComparisonRecord,
    ComparisonStatus,
    MetricDelta,
    RegressionSignal,
    RunProvenance,
)
from src.conversations.models import ConversationSummary
from src.db.domain import (
    AuditEventRecord,
    MembershipRecord,
    TenantRecord,
    WorkspaceRecord,
)
from src.db.mappers import (
    RunStatusNotYetSupported,
    asset_to_domain,
    asset_to_orm,
    audit_event_to_domain,
    audit_event_to_orm,
    comparison_to_domain,
    comparison_to_orm,
    conversation_summary_to_domain,
    conversation_summary_to_orm,
    dashboard_artifact_to_domain,
    dashboard_artifact_to_orm,
    idempotency_key_to_domain,
    idempotency_key_to_orm,
    membership_to_domain,
    membership_to_orm,
    run_status_from_persisted,
    run_status_to_persisted,
    run_to_domain,
    run_to_orm,
    tenant_to_domain,
    tenant_to_orm,
    workspace_to_domain,
    workspace_to_orm,
)
from src.db.mappers.dashboard_artifact import DashboardArtifactRecord
from src.db.mappers.idempotency_key import IdempotencyKeyRecord
from src.db.session import raw_admin_session
from src.runs.models import RunRecord, RunStatus


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


async def _seed_tenant_and_workspace() -> tuple[str, str]:
    """Create a tenant + workspace via raw admin session. Returns (tid, wid)."""
    tid = str(uuid.uuid4())
    wid = str(uuid.uuid4())
    async with raw_admin_session() as session:
        await session.execute(
            sa.text(
                "INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"
            ),
            {"id": tid, "n": f"T-{tid[:8]}", "s": f"t-{tid[:8]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO workspaces (id, tenant_id, name, is_default) "
                "VALUES (:id, :tid, 'default', true)"
            ),
            {"id": wid, "tid": tid},
        )
    return tid, wid


async def _seed_run(tid: str, wid: str, status: str = "queued") -> str:
    """Create a run row via raw SQL. Returns the run id."""
    rid = str(uuid.uuid4())
    async with raw_admin_session() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runs "
                "(id, tenant_id, workspace_id, status, initiated_by_actor_id, "
                "engine_version) "
                "VALUES (:id, :tid, :wid, :status, 'system', 'engine_v1')"
            ),
            {"id": rid, "tid": tid, "wid": wid, "status": status},
        )
    return rid


# ---------------------------------------------------------------------------
# tenants
# ---------------------------------------------------------------------------


async def test_tenant_roundtrip_via_db(clean_db: str) -> None:
    tid = str(uuid.uuid4())
    now = _now()
    original = TenantRecord(
        id=tid,
        name="Acme Corp",
        slug="acme",
        clerk_org_id="org_abc123",
        plan_id="pro",
        settings={"theme": "dark", "beta_flags": ["quality_loop"]},
        created_at=now,
        updated_at=now,
    )
    orm = tenant_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(sa.select(orm.__class__).where(orm.__class__.id == tid))
        ).scalar_one()
        roundtripped = tenant_to_domain(fetched)

    assert roundtripped.id == original.id
    assert roundtripped.name == original.name
    assert roundtripped.slug == original.slug
    assert roundtripped.clerk_org_id == original.clerk_org_id
    assert roundtripped.plan_id == original.plan_id
    assert roundtripped.settings == original.settings


# ---------------------------------------------------------------------------
# workspaces
# ---------------------------------------------------------------------------


async def test_workspace_roundtrip_via_db(clean_db: str) -> None:
    tid, _wid = await _seed_tenant_and_workspace()
    wid = str(uuid.uuid4())
    now = _now()
    original = WorkspaceRecord(
        id=wid,
        tenant_id=tid,
        name="Growth Workspace",
        is_default=False,
        created_at=now,
        updated_at=now,
    )
    orm = workspace_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(sa.select(orm.__class__).where(orm.__class__.id == wid))
        ).scalar_one()
        roundtripped = workspace_to_domain(fetched)

    assert roundtripped.id == wid
    assert roundtripped.tenant_id == tid
    assert roundtripped.name == "Growth Workspace"
    assert roundtripped.is_default is False


# ---------------------------------------------------------------------------
# memberships — both tenant-level and workspace-level shapes
# ---------------------------------------------------------------------------


async def test_membership_tenant_level_roundtrip(clean_db: str) -> None:
    tid, _wid = await _seed_tenant_and_workspace()
    now = _now()
    original = MembershipRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        user_id="user_alice",
        workspace_id=None,  # tenant-level
        role="owner",
        created_at=now,
        updated_at=now,
    )
    orm = membership_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = membership_to_domain(fetched)

    assert roundtripped.workspace_id is None
    assert roundtripped.is_tenant_level is True
    assert roundtripped.is_workspace_level is False
    assert roundtripped.role == "owner"
    assert roundtripped.user_id == "user_alice"


async def test_membership_workspace_level_roundtrip(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    original = MembershipRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        user_id="user_bob",
        workspace_id=wid,
        role="admin",
        created_at=now,
        updated_at=now,
    )
    orm = membership_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = membership_to_domain(fetched)

    assert roundtripped.workspace_id == wid
    assert roundtripped.is_workspace_level is True
    assert roundtripped.is_tenant_level is False
    assert roundtripped.role == "admin"


# ---------------------------------------------------------------------------
# assets — inline content, object-tier, approved_by optional
# ---------------------------------------------------------------------------


async def test_asset_inline_roundtrip_via_db(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    alice = ActorRef(
        actor_id="user_alice",
        actor_type="human",
        display_name="Alice",
        email="alice@example.com",
    )
    original = AssetRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        asset_type=AssetType.JUDGE_PACK,
        name="Greeting Judge",
        slug="greeting-judge",
        description="Checks for friendly tone",
        version=1,
        status=AssetStatus.DRAFT,
        content_hash="abc123",
        inline_content={"rules": ["rule_a", "rule_b"]},
        payload_ref=None,
        created_by=alice,
        approved_by=None,
        approved_at=None,
        changelog="Initial draft",
        cloned_from=None,
        parent_version=None,
        tags={"owner": "qa-team"},
        created_at=now,
        updated_at=now,
    )
    orm = asset_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = asset_to_domain(fetched)

    assert roundtripped.name == "Greeting Judge"
    assert roundtripped.slug == "greeting-judge"
    assert roundtripped.asset_type == AssetType.JUDGE_PACK
    assert roundtripped.status == AssetStatus.DRAFT
    assert roundtripped.inline_content == {"rules": ["rule_a", "rule_b"]}
    assert roundtripped.payload_ref is None
    assert roundtripped.created_by.actor_id == "user_alice"
    assert roundtripped.created_by.actor_type == "human"
    assert roundtripped.approved_by is None
    assert roundtripped.tags == {"owner": "qa-team"}


async def test_asset_object_tier_roundtrip_via_db(clean_db: str) -> None:
    """Object-tier asset: payload_ref populated, inline_content None."""
    from src.storage.models import ObjectRef, StorageTier

    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    alice = ActorRef(actor_id="user_alice", actor_type="human", display_name="Alice")
    payload = ObjectRef(
        backend="r2",
        bucket="ai-simtest-dev",
        key="t/abc/datasets/big.json",
        size_bytes=12345678,
        content_hash="sha256:deadbeef",
    )
    original = AssetRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        asset_type=AssetType.DATASET,
        name="Big Dataset",
        slug="big-dataset",
        description="",
        version=1,
        status=AssetStatus.DRAFT,
        content_hash="sha256:deadbeef",
        storage_tier=StorageTier.OBJECT,
        inline_content=None,
        payload_ref=payload,
        created_by=alice,
        created_at=now,
        updated_at=now,
    )
    orm = asset_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = asset_to_domain(fetched)

    assert roundtripped.storage_tier == StorageTier.OBJECT
    assert roundtripped.inline_content is None
    assert roundtripped.payload_ref is not None
    assert roundtripped.payload_ref.backend == "r2"
    assert roundtripped.payload_ref.bucket == "ai-simtest-dev"
    assert roundtripped.payload_ref.key == "t/abc/datasets/big.json"
    assert roundtripped.payload_ref.size_bytes == 12345678


async def test_asset_with_approved_by_roundtrip(clean_db: str) -> None:
    """Approval lifecycle: approved_by and approved_at round-trip correctly."""
    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    alice = ActorRef(actor_id="user_alice", actor_type="human", display_name="Alice")
    bob = ActorRef(actor_id="user_bob", actor_type="human", display_name="Bob")
    original = AssetRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        asset_type=AssetType.POLICY_PACK,
        name="GDPR Policy",
        slug="gdpr-policy",
        version=2,
        status=AssetStatus.APPROVED,
        content_hash="sha256:policy",
        inline_content={"rules": []},
        created_by=alice,
        approved_by=bob,
        approved_at=now,
        changelog="Approved for production",
        parent_version=1,
        created_at=now,
        updated_at=now,
    )
    orm = asset_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = asset_to_domain(fetched)

    assert roundtripped.status == AssetStatus.APPROVED
    assert roundtripped.approved_by is not None
    assert roundtripped.approved_by.actor_id == "user_bob"
    assert roundtripped.approved_at == now
    assert roundtripped.parent_version == 1
    assert roundtripped.changelog == "Approved for production"


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


async def test_run_roundtrip_via_db(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    original = RunRecord(
        run_id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        status=RunStatus.RUNNING,
        created_at=now,
        started_at=now,
        engine_version="engine_v1",
        metadata={"trigger": "cli", "note": "regression sweep"},
    )
    orm = run_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.run_id)
            )
        ).scalar_one()
        roundtripped = run_to_domain(fetched)

    # Domain uses run_id, ORM uses id — mapper bridges both directions.
    assert roundtripped.run_id == original.run_id
    assert roundtripped.tenant_id == original.tenant_id
    assert roundtripped.workspace_id == original.workspace_id
    assert roundtripped.status == RunStatus.RUNNING
    assert roundtripped.engine_version == "engine_v1"
    # metadata dict preserved
    assert roundtripped.metadata.get("trigger") == "cli"
    assert roundtripped.metadata.get("note") == "regression sweep"


async def test_run_metadata_carries_through(clean_db: str) -> None:
    """Nested dicts and lists in metadata survive JSONB round-trip."""
    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    complex_meta = {
        "bot_config": {"url": "https://api.example.com", "timeout": 30},
        "tags": ["prod", "eu-west-1"],
        "attempt": 3,
    }
    original = RunRecord(
        run_id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        status=RunStatus.COMPLETED,
        created_at=now,
        started_at=now,
        completed_at=now,
        metadata=complex_meta,
    )
    orm = run_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.run_id)
            )
        ).scalar_one()
        roundtripped = run_to_domain(fetched)

    assert roundtripped.metadata["bot_config"]["url"] == "https://api.example.com"
    assert roundtripped.metadata["bot_config"]["timeout"] == 30
    assert roundtripped.metadata["tags"] == ["prod", "eu-west-1"]
    assert roundtripped.metadata["attempt"] == 3


# ---------------------------------------------------------------------------
# conversation_summaries
# ---------------------------------------------------------------------------


async def test_conversation_summary_roundtrip(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    rid = await _seed_run(tid, wid, status="completed")
    now = _now()
    original = ConversationSummary(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        run_id=rid,
        persona_id="persona_angry_customer",
        persona_name="Angry Customer",
        persona_type="adversarial",
        verdict="fail",
        pass_rate=0.35,
        turn_count=12,
        judge_scores={"tone": 0.2, "accuracy": 0.5},
        failure_reason="Hallucinated discount policy",
        failure_category="hallucination",
        transcript_ref=None,
        created_at=now,
        updated_at=now,
        tags={"category": "regression"},
    )
    orm = conversation_summary_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = conversation_summary_to_domain(fetched)

    assert roundtripped.persona_id == "persona_angry_customer"
    assert roundtripped.verdict == "fail"
    assert roundtripped.pass_rate == 0.35
    assert roundtripped.turn_count == 12
    assert roundtripped.judge_scores == {"tone": 0.2, "accuracy": 0.5}
    assert roundtripped.failure_category == "hallucination"
    assert roundtripped.tags == {"category": "regression"}


async def test_conversation_summary_with_transcript(clean_db: str) -> None:
    """ObjectRef transcript reference round-trips through bucket/key columns."""
    from src.storage.models import ObjectRef

    tid, wid = await _seed_tenant_and_workspace()
    rid = await _seed_run(tid, wid, status="completed")
    now = _now()
    transcript = ObjectRef(
        backend="r2",
        bucket="ai-simtest-dev",
        key=f"t/{tid[:8]}/transcripts/conv-1.json",
        size_bytes=0,
        content_hash="",
    )
    original = ConversationSummary(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        run_id=rid,
        persona_id="p1",
        persona_name="P1",
        verdict="pass",
        pass_rate=1.0,
        turn_count=5,
        transcript_ref=transcript,
        created_at=now,
        updated_at=now,
    )
    orm = conversation_summary_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = conversation_summary_to_domain(fetched)

    assert roundtripped.transcript_ref is not None
    assert roundtripped.transcript_ref.backend == "r2"
    assert roundtripped.transcript_ref.bucket == "ai-simtest-dev"
    assert roundtripped.transcript_ref.key == f"t/{tid[:8]}/transcripts/conv-1.json"


# ---------------------------------------------------------------------------
# comparisons
# ---------------------------------------------------------------------------


async def test_comparison_roundtrip_via_db(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    left_run = await _seed_run(tid, wid, status="completed")
    right_run = await _seed_run(tid, wid, status="completed")
    now = _now()
    left_prov = RunProvenance(
        run_id=left_run,
        engine_version="engine_v1",
        asset_versions_used=["judge_pack:greet@v1", "policy_pack:gdpr@v2"],
        run_status=RunStatus.COMPLETED,
    )
    right_prov = RunProvenance(
        run_id=right_run,
        engine_version="engine_v1",
        asset_versions_used=["judge_pack:greet@v1", "policy_pack:gdpr@v3"],
        run_status=RunStatus.COMPLETED,
    )
    original = ComparisonRecord(
        id=str(uuid.uuid4()),
        workspace_id=wid,
        tenant_id=tid,
        left_run_id=left_run,
        right_run_id=right_run,
        status=ComparisonStatus.COMPLETED,
        created_at=now,
        started_at=now,
        completed_at=now,
        engine_version="engine_v1",
        regression_signals=[],
        left_provenance=left_prov,
        right_provenance=right_prov,
    )
    orm = comparison_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = comparison_to_domain(fetched)

    assert roundtripped.status == ComparisonStatus.COMPLETED
    assert roundtripped.left_run_id == left_run
    assert roundtripped.right_run_id == right_run
    assert roundtripped.left_provenance is not None
    assert roundtripped.left_provenance.engine_version == "engine_v1"
    assert roundtripped.left_provenance.asset_versions_used == [
        "judge_pack:greet@v1",
        "policy_pack:gdpr@v2",
    ]
    assert roundtripped.right_provenance is not None
    assert roundtripped.right_provenance.run_status == RunStatus.COMPLETED


async def test_comparison_with_typed_signals(clean_db: str) -> None:
    """Typed RegressionSignal list round-trips through JSONB with no type loss."""
    tid, wid = await _seed_tenant_and_workspace()
    left_run = await _seed_run(tid, wid, status="completed")
    right_run = await _seed_run(tid, wid, status="completed")
    now = _now()
    signals = [
        RegressionSignal(
            type="pass_rate_drop",
            severity="critical",
            metric="overall_pass_rate",
            payload={"from": 0.92, "to": 0.78, "delta": -0.14},
        ),
        RegressionSignal(
            type="new_failure_cluster",
            severity="warning",
            metric="cluster_count",
            payload={"cluster_id": "c_hallucination_3", "count": 4},
        ),
    ]
    deltas = [
        MetricDelta(metric="pass_rate", left=0.92, right=0.78, delta=-0.14),
        MetricDelta(metric="avg_turns", left=5.2, right=6.8, delta=1.6),
    ]
    original = ComparisonRecord(
        id=str(uuid.uuid4()),
        workspace_id=wid,
        tenant_id=tid,
        left_run_id=left_run,
        right_run_id=right_run,
        status=ComparisonStatus.COMPLETED,
        created_at=now,
        completed_at=now,
        regression_signals=signals,
        metric_deltas=deltas,
        verdict="regression",
        evidence_count=2,
    )
    orm = comparison_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = comparison_to_domain(fetched)

    assert len(roundtripped.regression_signals) == 2
    assert roundtripped.regression_signals[0].type == "pass_rate_drop"
    assert roundtripped.regression_signals[0].severity == "critical"
    assert roundtripped.regression_signals[0].payload["delta"] == -0.14
    assert roundtripped.regression_signals[1].type == "new_failure_cluster"
    assert roundtripped.metric_deltas is not None
    assert len(roundtripped.metric_deltas) == 2
    assert roundtripped.metric_deltas[0].metric == "pass_rate"
    assert roundtripped.metric_deltas[0].delta == -0.14
    assert roundtripped.verdict == "regression"
    assert roundtripped.evidence_count == 2


# ---------------------------------------------------------------------------
# dashboard_artifacts — thin opaque cache
# ---------------------------------------------------------------------------


async def test_dashboard_artifact_roundtrip(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    rid = await _seed_run(tid, wid, status="completed")
    now = _now()
    original = DashboardArtifactRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        workspace_id=wid,
        run_id=rid,
        artifact_type="dashboard_summary",
        payload={
            "overview": {"pass_rate": 0.85, "total": 100},
            "top_failures": [{"cluster": "c1", "count": 10}],
            "engine_version": "engine_v1",
        },
        created_at=now,
    )
    orm = dashboard_artifact_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = dashboard_artifact_to_domain(fetched)

    assert roundtripped.artifact_type == "dashboard_summary"
    assert roundtripped.payload["overview"]["pass_rate"] == 0.85
    assert roundtripped.payload["top_failures"][0]["cluster"] == "c1"


# ---------------------------------------------------------------------------
# idempotency_keys
# ---------------------------------------------------------------------------


async def test_idempotency_key_roundtrip(clean_db: str) -> None:
    tid, _wid = await _seed_tenant_and_workspace()
    now = _now()
    original = IdempotencyKeyRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        idempotency_key="client-key-abc",
        request_hash="sha256:requesthash",
        response_payload={"comparison_id": "cmp_123", "status": "pending"},
        status_code=201,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    orm = idempotency_key_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = idempotency_key_to_domain(fetched)

    assert roundtripped.idempotency_key == "client-key-abc"
    assert roundtripped.request_hash == "sha256:requesthash"
    assert roundtripped.response_payload["comparison_id"] == "cmp_123"
    assert roundtripped.status_code == 201


# ---------------------------------------------------------------------------
# audit_events
# ---------------------------------------------------------------------------


async def test_audit_event_roundtrip(clean_db: str) -> None:
    tid, wid = await _seed_tenant_and_workspace()
    now = _now()
    original = AuditEventRecord(
        id=str(uuid.uuid4()),
        tenant_id=tid,
        action="asset.created",
        resource_type="asset",
        resource_id="asset_abc",
        actor_id="user_alice",
        actor_type="human",
        actor_display="Alice",
        workspace_id=wid,
        details={"asset_type": "judge_pack", "version": 1},
        asset_versions={"judge_pack:greet": 1},
        ip_address="192.168.1.1",
        user_agent="curl/8.0",
        correlation_id="corr-abc-123",
        created_at=now,
    )
    orm = audit_event_to_orm(original)
    async with raw_admin_session() as session:
        session.add(orm)
        await session.flush()
        fetched = (
            await session.execute(
                sa.select(orm.__class__).where(orm.__class__.id == original.id)
            )
        ).scalar_one()
        roundtripped = audit_event_to_domain(fetched)

    assert roundtripped.action == "asset.created"
    assert roundtripped.actor_id == "user_alice"
    assert roundtripped.actor_type == "human"
    assert roundtripped.details == {"asset_type": "judge_pack", "version": 1}
    assert roundtripped.asset_versions == {"judge_pack:greet": 1}
    assert roundtripped.correlation_id == "corr-abc-123"


# ---------------------------------------------------------------------------
# RunStatus 5→11 forward/reverse — parametrized, 5 + 6 = 11 tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "in_code_state",
    [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ],
)
async def test_run_status_forward_identity(in_code_state: RunStatus) -> None:
    """Every in-code RunStatus round-trips through to_persisted/from_persisted."""
    persisted = run_status_to_persisted(in_code_state)
    assert persisted == in_code_state.value
    back = run_status_from_persisted(persisted)
    assert back == in_code_state


@pytest.mark.parametrize(
    "future_state",
    [
        "provisioning",
        "finalizing",
        "waiting_input",
        "paused",
        "expired",
        "unknown",
    ],
)
async def test_run_status_reverse_future_raises(future_state: str) -> None:
    """Each of the 6 future 11-state values raises RunStatusNotYetSupported."""
    with pytest.raises(RunStatusNotYetSupported) as excinfo:
        run_status_from_persisted(future_state)
    # The error must name the offending state so ops can act on it.
    assert future_state in str(excinfo.value)
    assert excinfo.value.persisted_status == future_state
