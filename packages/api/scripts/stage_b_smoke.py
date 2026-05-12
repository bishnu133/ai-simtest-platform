"""Stage B smoke test — boot app against Neon and exercise all 5 Cat A PG repos.

Run from packages/api/ with DATABASE_URL exported (asyncpg form).
Exits 0 on success, non-zero on any failure.

Exercises:
  Step 1: build app with all 5 use_postgres_* switches True (DI graph)
  Step 2: PostgresRunRepository.create + get  (x2 — both runs COMPLETED so
          step 4's ComparisonService eligibility check passes)
  Step 3: PostgresConversationSummaryRepository.upsert + get
  Step 4: ComparisonService.create_comparison + repo.get + direct ORM
          actor-column assertion  (Turn 2.7 Drift 2 + Drift 4 fix verification)
  Step 5: PostgresIdempotencyRepository.remember + lookup  (proves migration 0003)
  Step 6: PostgresDashboardArtifactRepository.upsert_artifacts + get_overview

Each round-trip happens inside a tenant_scoped_session, so RLS and the
SET LOCAL ROLE app_user step are exercised in earnest.

Turn 2.7 Drift 2 + 4 (plan v0.2.1):
  Step 4 was previously a direct ``cmp_repo.create()`` call that bypassed
  ComparisonService because the service minted ``cmp_<hex>`` ids that
  asyncpg rejected (T2.6-D2). Drift 2 fixed the service to mint UUID4
  ids; Drift 4 added WriteContext propagation so ``initiated_by_actor_id``
  reflects the real caller. Step 4 now exercises BOTH fixes end-to-end:
    * Service.create_comparison() round-trip (proves Drift 2)
    * Direct ORM read of initiated_by_actor_id matches ctx.actor.actor_id
      (proves Drift 4 — the actor flows from caller through to DB column)
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

# All Turn 2.6 imports go through src.* per the project convention.
from src.app_factory import create_app
from src.config import AppSettings
from src.db.models import Comparison as ComparisonORM
from src.db.session import raw_admin_session, tenant_scoped_session
from src.common.models import ActorRef, TenantContext
from src.runs.repository import PostgresRunRepository
from src.runs.models import RunRecord, RunStatus
from src.runs.service import RunService
from src.conversations.repository import PostgresConversationSummaryRepository
from src.conversations.models import ConversationSummary
from src.comparisons.repository import PostgresComparisonRepository
from src.comparisons.service import ComparisonService
from src.comparisons.idempotency import (
    PostgresIdempotencyRepository,
    canonical_hash,
)
from src.results.repository import PostgresDashboardArtifactRepository
from src.results.models import (
    CoverageMetric,
    FailurePattern,
    JudgeBreakdown,
    RunOverview,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"


def _ctx() -> TenantContext:
    """Build a TenantContext with the system actor for smoke-test purposes."""
    return TenantContext(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor=ActorRef.system(),
        correlation_id="stage-b-smoke",
    )


async def _run_smoke() -> dict[str, str]:
    """Run all 6 steps. Returns dict of generated ids on success, raises on failure."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    if not db_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("DATABASE_URL must use postgresql+asyncpg:// scheme")

    # ----- STEP 1: build app -------------------------------------------------
    print("\n[STEP 1/6] Building app with all 5 use_postgres_* switches True...")
    settings = AppSettings(
        app_env="staging",                    # exercises R-8 coupling guardrail
        auth_enabled=False,                   # DI builds without webhook plumbing
        database_url=db_url,
        use_postgres_runs=True,
        use_postgres_dashboard_artifacts=True,
        use_postgres_comparisons=True,
        use_postgres_idempotency=True,
        use_postgres_conversation_summaries=True,
    )
    app = create_app(settings=settings)
    routes = sorted({r.path for r in app.routes if hasattr(r, "path")})
    print(f"  OK: app built. {len(routes)} routes mounted.")
    for path in routes:
        print(f"    {path}")

    # ----- STEP 2: PostgresRunRepository round-trip --------------------------
    run_id = str(uuid.uuid4())
    other_run_id = str(uuid.uuid4())
    print(f"\n[STEP 2/6] PostgresRunRepository — create + get + create (x2)")
    print(f"  Run #1 id = {run_id}")

    async with tenant_scoped_session(TENANT_ID) as session:
        run_repo = PostgresRunRepository()
        record = RunRecord(
            run_id=run_id,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            # COMPLETED (not QUEUED) — Turn 2.7 Drift 4 step 4 calls
            # ComparisonService.create_comparison which requires both
            # runs to be COMPLETED for eligibility.
            status=RunStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            engine_version="engine_v1",
            metadata={"source": "stage_b_smoke"},
        )
        await run_repo.create(record, session=session)
        fetched = await run_repo.get(_ctx(), run_id, session=session)
        assert fetched.run_id == run_id, (
            f"RunRepository round-trip mismatch: got {fetched.run_id!r}"
        )
    print(f"  OK: Run #1 round-trip. status={fetched.status.value}")

    print(f"  Run #2 id = {other_run_id}")
    async with tenant_scoped_session(TENANT_ID) as session:
        run_repo = PostgresRunRepository()
        await run_repo.create(
            RunRecord(
                run_id=other_run_id,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                # COMPLETED — same reason as Run #1 above.
                status=RunStatus.COMPLETED,
                created_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
                engine_version="engine_v1",
            ),
            session=session,
        )
    print(f"  OK: Run #2 persisted")

    # ----- STEP 3: PostgresConversationSummaryRepository round-trip ----------
    convo_id = str(uuid.uuid4())
    print(f"\n[STEP 3/6] PostgresConversationSummaryRepository — upsert + get")
    print(f"  ConversationSummary id = {convo_id}")
    async with tenant_scoped_session(TENANT_ID) as session:
        convo_repo = PostgresConversationSummaryRepository()
        summary = ConversationSummary(
            id=convo_id,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            run_id=run_id,
            persona_id="stage-b-persona",
            persona_name="Stage B Smoke Persona",
            persona_type="standard",
            verdict="pending",
            pass_rate=0.0,
            turn_count=0,
            judge_scores={},
            failure_reason=None,
            failure_category=None,
            transcript_ref=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags={"source": "stage_b_smoke"},
        )
        await convo_repo.upsert(summary, session=session)
        fetched_convo = await convo_repo.get(_ctx(), convo_id, session=session)
        assert fetched_convo.id == convo_id, (
            f"ConversationSummary round-trip mismatch: got {fetched_convo.id!r}"
        )
    print(f"  OK: ConversationSummary round-trip. verdict={fetched_convo.verdict}")

    # ----- STEP 4: ComparisonService end-to-end (Drift 2 + Drift 4 verification)
    print(f"\n[STEP 4/6] ComparisonService.create_comparison — service flow")
    print(f"  This step verifies BOTH Turn 2.7 source-fixes:")
    print(f"    * Drift 2: service mints UUID4 ids (was f'cmp_{{uuid.hex[:12]}}')")
    print(f"    * Drift 4: ctx.actor flows through to comparisons.initiated_by_actor_id")

    class _NullProvider:
        """Smoke provider — returns no signals, no error. Service path
        runs PENDING -> RUNNING -> COMPLETED via this provider."""
        async def compute(self, record):  # noqa: ARG002
            return ([], None)

    # ComparisonService is what we're testing — wire it up with the
    # real Postgres comparison repo + a RunService over the real
    # Postgres run repo (so eligibility check reads from the runs we
    # just persisted in step 2).
    cmp_repo = PostgresComparisonRepository()
    run_repo = PostgresRunRepository()
    cmp_svc = ComparisonService(
        repo=cmp_repo,
        run_service=RunService(run_repo),
        provider=_NullProvider(),
    )

    # The TenantContext carries _ctx().actor = ActorRef.system() — see
    # _ctx() above. The service builds WriteContext.from_tenant_context(ctx)
    # and threads it through to mapper → ORM column. We verify the ORM
    # column directly below (Bishnu's final decision Q2 — Option B,
    # direct ORM query).
    record = await cmp_svc.create_comparison(_ctx(), run_id, other_run_id)
    comparison_id = record.id

    # Drift 2 verification — id is UUID4 string (NOT cmp_<hex>).
    parsed = uuid.UUID(comparison_id)
    assert parsed.version == 4, (
        f"Service minted id {comparison_id!r} which is not UUID4 "
        f"(version={parsed.version}). Drift 2 source-fix regressed."
    )
    assert not comparison_id.startswith("cmp_"), (
        f"Service minted id {comparison_id!r} carries the legacy 'cmp_' "
        f"prefix. Drift 2 source-fix regressed."
    )

    # Service round-trip via repo.get — same path Stage C will use.
    async with tenant_scoped_session(TENANT_ID) as session:
        fetched_cmp = await cmp_repo.get(_ctx(), comparison_id, session=session)
        assert fetched_cmp.id == comparison_id, (
            f"Comparison round-trip mismatch: got {fetched_cmp.id!r}"
        )
    print(f"  OK: ComparisonService round-trip. id={comparison_id} status={fetched_cmp.status.value}")

    # Drift 4 verification — direct ORM read of initiated_by_actor_id.
    # Per Bishnu's final decision Q2 (Option B), the smoke must prove
    # the actor propagation chain end-to-end:
    #   _ctx().actor → service builds WriteContext.from_tenant_context →
    #   repo.create(write_ctx=...) → mapper threads actor.actor_id →
    #   comparisons.initiated_by_actor_id column
    # If any link breaks, this assertion fails.
    expected_actor_id = _ctx().actor.actor_id  # = "system" for smoke
    async with raw_admin_session() as admin_s:
        # raw_admin_session is the right surface for this read — we
        # want the raw column value, not a domain-projection. RLS is
        # bypassed for the verification SELECT.
        result = await admin_s.execute(
            sa.select(ComparisonORM.initiated_by_actor_id).where(
                ComparisonORM.id == comparison_id
            )
        )
        persisted_actor_id = result.scalar_one_or_none()
    assert persisted_actor_id is not None, (
        f"Comparison row not found for id={comparison_id!r} after service flow."
    )
    assert persisted_actor_id == expected_actor_id, (
        f"Drift 4 actor propagation FAILED. Expected ctx.actor.actor_id="
        f"{expected_actor_id!r} to flow through to "
        f"comparisons.initiated_by_actor_id, got {persisted_actor_id!r}. "
        f"Somewhere in the chain (service builds WriteContext → "
        f"repo.create → mapper → ORM column), the actor id is being "
        f"lost or replaced."
    )
    print(f"  OK: Drift 4 actor propagation. initiated_by_actor_id={persisted_actor_id!r}")

    # ----- STEP 5: PostgresIdempotencyRepository round-trip ------------------
    # The central proof that migration 0003 worked: workspace_id column
    # accepts writes and the (tenant, workspace, key) composite index works.
    idem_key = "stage-b-key"
    body_hash = canonical_hash(WORKSPACE_ID, run_id, other_run_id)
    print(f"\n[STEP 5/6] PostgresIdempotencyRepository — remember + lookup  (THE migration 0003 test)")
    print(f"  idempotency_key = {idem_key!r}")
    print(f"  body_hash       = {body_hash}")
    async with tenant_scoped_session(TENANT_ID) as session:
        idem_repo = PostgresIdempotencyRepository()
        await idem_repo.remember(
            _ctx(),
            idem_key,
            body_hash,
            comparison_id,
            session=session,
        )
        entry = await idem_repo.lookup(_ctx(), idem_key, session=session)
        assert entry is not None, "Idempotency lookup returned None after remember"
        assert entry.comparison_id == comparison_id, (
            f"Idempotency lookup mismatch: got comparison_id="
            f"{entry.comparison_id!r}, expected {comparison_id!r}"
        )
        assert entry.body_hash == body_hash, (
            f"Idempotency lookup body_hash mismatch: got {entry.body_hash!r}, "
            f"expected {body_hash!r}"
        )
    print(f"  OK: Idempotency round-trip. expires_at={entry.expires_at.isoformat()}")

    # ----- STEP 6: PostgresDashboardArtifactRepository round-trip ------------
    # RC-5 requires the run to exist in the same (tenant, workspace);
    # run_id from STEP 2 satisfies that.
    print(f"\n[STEP 6/6] PostgresDashboardArtifactRepository — upsert_artifacts + get_overview")
    print(f"  run_id = {run_id}")
    async with tenant_scoped_session(TENANT_ID) as session:
        dash_repo = PostgresDashboardArtifactRepository()
        overview = RunOverview(
            run_id=run_id,
            # COMPLETED to match the run's actual status (bumped above
            # for Drift 4 ComparisonService eligibility).
            status=RunStatus.COMPLETED,
            pass_rate=0.0,
            total_conversations=1,
            started_at=None,
            completed_at=None,
        )
        judges = [JudgeBreakdown(judge_name="stage-b-judge",
                                 pass_count=1, fail_count=0, avg_score=1.0)]
        failures = [FailurePattern(cluster_id="stage-b-cluster",
                                   label="smoke-failure-pattern",
                                   count=0,
                                   sample_conversation_ids=[])]
        coverage = [CoverageMetric(dimension="topic", covered=1, total=1)]
        await dash_repo.upsert_artifacts(
            _ctx(),
            run_id,
            overview=overview,
            judges=judges,
            failures=failures,
            coverage=coverage,
            session=session,
        )
        fetched_overview = await dash_repo.get_overview(
            _ctx(), run_id, session=session
        )
        assert fetched_overview is not None, (
            "DashboardArtifact get_overview returned None after upsert_artifacts"
        )
        assert fetched_overview.run_id == run_id, (
            f"DashboardArtifact round-trip mismatch: got "
            f"run_id={fetched_overview.run_id!r}, expected {run_id!r}"
        )
        assert fetched_overview.total_conversations == 1, (
            f"DashboardArtifact pass-through mismatch: total_conversations="
            f"{fetched_overview.total_conversations}, expected 1"
        )
    print(f"  OK: DashboardArtifact round-trip. status={fetched_overview.status.value}, "
          f"total_conversations={fetched_overview.total_conversations}")

    return {
        "run_id":           run_id,
        "other_run_id":     other_run_id,
        "conversation_id":  convo_id,
        "comparison_id":    comparison_id,
        "idempotency_key":  idem_key,
        "dashboard_run_id": run_id,
    }


async def main() -> int:
    try:
        ids = await _run_smoke()
    except Exception:
        print("\n=== Stage B smoke test FAILED ===", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("\n=== Stage B smoke test PASSED ===")
    for label, value in ids.items():
        print(f"  {label:18s} = {value}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))