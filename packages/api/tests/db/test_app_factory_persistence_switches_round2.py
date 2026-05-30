"""Persistence feature-switch wiring tests — Turn 2.6 (Round 2).

This file complements the Turn 2.5 wiring tests (already in
``test_postgres_run_repository.py`` W1-W7 trio) and covers the new Round-2
switches: ``use_postgres_comparisons`` (Step 2),
``use_postgres_idempotency`` (Step 3), ``use_postgres_conversation_summaries``
(Step 5), plus the R-8 staging/production coupling guardrail (Step 3).

All tests live under ``tests/db/`` per Turn 2.6 plan v0.2.1 R-2 because each
booted ``create_app(...)`` call attaches to the engine the conftest
autouse-fixture configured against the subprocess Postgres.

Test naming convention mirrors Turn 2.5:
  W-{repo}-1 : default uses InMemory
  W-{repo}-2 : switch=True wires Postgres against settings DB
  W-{repo}-3 : switch=True without database_url raises FatalConfigurationError
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app_factory import FatalConfigurationError, create_app
from src.common.models import ActorRef, TenantContext
from src.comparisons.models import ComparisonRecord, ComparisonStatus
from src.comparisons.repository import (
    InMemoryComparisonRepository,
    PostgresComparisonRepository,
)
from src.config import AppSettings


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


# ---------------------------------------------------------------------------
# W-cmp-1: default uses InMemoryComparisonRepository
# ---------------------------------------------------------------------------


async def test_app_factory_default_uses_in_memory_comparison_repository() -> None:
    """W-cmp-1: ``create_app(default settings)`` →
    ``app.state.cmp_repo`` is ``InMemoryComparisonRepository``.

    Explicit database_url=None defeats the conftest autouse-fixture's
    DATABASE_URL env-var pollution so the test proves the literal
    "no DB, no Postgres" default behaviour.
    """
    app = create_app(settings=AppSettings(app_env="test", database_url=None))
    assert isinstance(app.state.cmp_repo, InMemoryComparisonRepository)


# ---------------------------------------------------------------------------
# W-cmp-2: use_postgres_comparisons=True wires PostgresComparisonRepository
# ---------------------------------------------------------------------------


async def test_app_factory_with_use_postgres_comparisons_uses_postgres_comparison_repository(
    pg_url: str,
    admin_session: AsyncSession,
) -> None:
    """W-cmp-2: AppSettings(use_postgres_comparisons=True, database_url=pg_url)
    → ``app.state.cmp_repo`` is ``PostgresComparisonRepository`` AND
    queries it executes hit the same Postgres instance pg_url points at.

    Engine source-of-truth proof: insert via admin_session and read back
    through the repo.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-w-cmp-2")
    cid = "babababa-1111-2222-3333-babababababa"
    left = "fafafafa-aaaa-bbbb-cccc-fafafafafafa"
    right = "cacacaca-bbbb-cccc-dddd-cacacacacaca"
    # Seed runs + comparison row directly in DB
    for rid in (left, right):
        await admin_session.execute(
            text(
                "INSERT INTO runs (id, tenant_id, workspace_id, status, "
                "initiated_by_actor_id, engine_version, "
                "created_at, completed_at, started_at) "
                "VALUES (:id, :tid, :wid, 'completed', 'system', 'engine_v1', "
                "now(), now(), now())"
            ),
            {"id": rid, "tid": tid, "wid": wid},
        )
    await admin_session.execute(
        text(
            "INSERT INTO comparisons (id, tenant_id, workspace_id, "
            "left_run_id, right_run_id, status, initiated_by_actor_id, "
            "engine_version, config, regression_signals, created_at) "
            "VALUES (:id, :tid, :wid, :left, :right, 'pending', 'system', "
            "'engine_v1', '{}', '[]', now())"
        ),
        {"id": cid, "tid": tid, "wid": wid, "left": left, "right": right},
    )
    await admin_session.commit()

    app = create_app(
        settings=AppSettings(
            app_env="test",
            database_url=pg_url,
            use_postgres_comparisons=True,
        )
    )
    # Type assertion (W-cmp-2 surface)
    assert isinstance(app.state.cmp_repo, PostgresComparisonRepository)

    # Engine source-of-truth proof: repo reads the row admin_session wrote
    fetched = await app.state.cmp_repo.get(_ctx(tid, wid), cid)
    assert fetched.id == cid
    assert fetched.status == ComparisonStatus.PENDING


# ---------------------------------------------------------------------------
# W-cmp-3: use_postgres_comparisons=True without database_url raises
# ---------------------------------------------------------------------------


async def test_use_postgres_comparisons_without_database_url_raises_fatal_configuration_error() -> None:
    """W-cmp-3: AppSettings(use_postgres_comparisons=True, database_url=None)
    → create_app raises FatalConfigurationError.

    Explicit database_url=None defeats the conftest autouse-fixture's
    DATABASE_URL env-var pollution so the test proves the literal guardrail
    behaviour, not just a misconfigured env.
    """
    with pytest.raises(FatalConfigurationError, match="database_url"):
        create_app(
            settings=AppSettings(
                app_env="test",
                database_url=None,
                use_postgres_comparisons=True,
            )
        )


# ---------------------------------------------------------------------------
# Step 3 — IdempotencyStore wiring (W-idem-* trio + R-8 coupling)
# ---------------------------------------------------------------------------

from src.comparisons.idempotency import (
    InMemoryIdempotencyStore,
    PostgresIdempotencyRepository,
)


# W-idem-1: default uses InMemoryIdempotencyStore
async def test_app_factory_default_uses_in_memory_idempotency_store() -> None:
    """W-idem-1: default settings → app.state.idempotency_store is
    InMemoryIdempotencyStore.
    """
    app = create_app(settings=AppSettings(app_env="test", database_url=None))
    assert isinstance(app.state.idempotency_store, InMemoryIdempotencyStore)


# W-idem-2: use_postgres_idempotency=True wires PostgresIdempotencyRepository
async def test_app_factory_with_use_postgres_idempotency_uses_postgres_idempotency_repository(
    pg_url: str,
    admin_session: AsyncSession,
) -> None:
    """W-idem-2: AppSettings(use_postgres_idempotency=True, database_url=pg_url)
    → app.state.idempotency_store is PostgresIdempotencyRepository AND
    queries it executes hit the same Postgres instance.

    Engine source-of-truth proof: write via the wired repo, read back via
    admin_session.
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-w-idem-2")

    app = create_app(
        settings=AppSettings(
            app_env="test",
            database_url=pg_url,
            use_postgres_idempotency=True,
        )
    )
    assert isinstance(app.state.idempotency_store, PostgresIdempotencyRepository)

    ctx = _ctx(tid, wid)
    await app.state.idempotency_store.remember(
        ctx, "wiring-key", "deadbeef" * 8, "11112222-3333-4444-5555-666677778888"
    )

    # Engine source-of-truth: row visible via admin_session
    row = await admin_session.execute(
        text(
            "SELECT idempotency_key FROM idempotency_keys "
            "WHERE tenant_id = :tid AND workspace_id = :wid"
        ),
        {"tid": tid, "wid": wid},
    )
    assert row.scalar_one_or_none() == "wiring-key"


# W-idem-3: use_postgres_idempotency=True without database_url raises
async def test_use_postgres_idempotency_without_database_url_raises_fatal_configuration_error() -> None:
    """W-idem-3: use_postgres_idempotency=True with database_url=None →
    create_app raises FatalConfigurationError.
    """
    with pytest.raises(FatalConfigurationError, match="database_url"):
        create_app(
            settings=AppSettings(
                app_env="test",
                database_url=None,
                use_postgres_idempotency=True,
            )
        )


# ---------------------------------------------------------------------------
# Step 3 — R-8 coupling guardrail (W-coup-1, parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_env,use_pg_cmp,use_pg_idem,should_raise",
    [
        # Mixed mode in staging/production: must raise
        ("production", True, False, True),
        ("staging", False, True, True),
        # Mixed mode in dev/test: must NOT raise
        ("test", True, False, False),
        ("development", False, True, False),
        # Coupled in production: must NOT raise (both False is the default;
        # with True, would also need PG runs/dashboards which we don't gate
        # in this test so we exercise both False, which is allowed).
    ],
)
async def test_staging_production_requires_comparison_idempotency_coupling(
    pg_url: str,
    app_env: str,
    use_pg_cmp: bool,
    use_pg_idem: bool,
    should_raise: bool,
) -> None:
    """W-coup-1 (R-8): in staging/production, use_postgres_comparisons and
    use_postgres_idempotency must match. In dev/test, mixed mode is allowed.

    Conversation summaries are intentionally NOT coupled (documented in the
    guardrail in app_factory).

    Note: production also requires non-dev auth provider; we avoid hitting
    that guardrail by using auth_provider='clerk' for production cases plus
    the minimum Clerk fields. For staging the dev provider is allowed.
    """
    settings_kwargs = dict(
        app_env=app_env,
        database_url=pg_url,
        use_postgres_comparisons=use_pg_cmp,
        use_postgres_idempotency=use_pg_idem,
        use_postgres_audit_events=True,
    )
    if app_env == "production":
        # Avoid the production-specific auth-provider + auth_enabled
        # guardrails so the only failure mode under test is the R-8
        # coupling guardrail.
        settings_kwargs.update(
            auth_enabled=True,
            auth_provider="clerk",
            clerk_jwks_url="https://example.test/jwks",
            clerk_issuer="https://example.test",
            clerk_audience="aud",
        )

    if should_raise:
        with pytest.raises(FatalConfigurationError, match="must match"):
            create_app(settings=AppSettings(**settings_kwargs))
    else:
        # No raise; app constructs successfully
        app = create_app(settings=AppSettings(**settings_kwargs))
        assert app is not None


# ---------------------------------------------------------------------------
# Step 4 — audit-persistence guardrail (FH-Tier-2-S1 B.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_env,use_pg_audit,should_raise",
    [
        ("production", False, True),
        ("staging", False, True),
        ("staging", True, False),
        ("test", False, False),
    ],
)
async def test_staging_production_requires_audit_persistence(
    pg_url: str,
    app_env: str,
    use_pg_audit: bool,
    should_raise: bool,
) -> None:
    """FH-Tier-2-S1 B.1: in staging/production, use_postgres_audit_events must
    be True (durable audit is mandatory in real deployments). In dev/test the
    guard does not apply. cmp/idem are left at default (both False) so R-8 is
    satisfied and the audit guard is the only failure mode under test.
    """
    settings_kwargs = dict(
        app_env=app_env,
        database_url=pg_url,
        use_postgres_audit_events=use_pg_audit,
    )
    if app_env == "production":
        settings_kwargs.update(
            auth_enabled=True,
            auth_provider="clerk",
            clerk_jwks_url="https://example.test/jwks",
            clerk_issuer="https://example.test",
            clerk_audience="aud",
        )

    if should_raise:
        with pytest.raises(FatalConfigurationError, match="use_postgres_audit_events"):
            create_app(settings=AppSettings(**settings_kwargs))
    else:
        app = create_app(settings=AppSettings(**settings_kwargs))
        assert app is not None


# ---------------------------------------------------------------------------
# Step 5 — ConversationSummaryRepository wiring (W-conv-* trio)
# ---------------------------------------------------------------------------

from src.conversations.repository import (
    InMemoryConversationSummaryRepository,
    PostgresConversationSummaryRepository,
)


# W-conv-1: default uses InMemoryConversationSummaryRepository
async def test_app_factory_default_uses_in_memory_conversation_summary_repository() -> None:
    """W-conv-1: default settings → app.state.conv_summary_repo is
    InMemoryConversationSummaryRepository.
    """
    app = create_app(settings=AppSettings(app_env="test", database_url=None))
    assert isinstance(
        app.state.conv_summary_repo, InMemoryConversationSummaryRepository
    )


# W-conv-2: use_postgres_conversation_summaries=True wires Postgres impl
async def test_app_factory_with_use_postgres_conversation_summaries_uses_postgres_repository(
    pg_url: str,
    admin_session: AsyncSession,
) -> None:
    """W-conv-2: AppSettings(use_postgres_conversation_summaries=True,
    database_url=pg_url) → conv_summary_repo is the Postgres impl AND
    queries it executes hit the same Postgres instance.
    """
    import uuid as _uuid

    from src.conversations.models import ConversationSummary

    tid, wid = await _seed_tenant_workspace(admin_session, "t26-w-conv-2")
    run_id = "77777777-7777-7777-7777-777777777777"
    await admin_session.execute(
        text(
            "INSERT INTO runs (id, tenant_id, workspace_id, status, "
            "initiated_by_actor_id, engine_version, created_at, "
            "completed_at, started_at) "
            "VALUES (:id, :tid, :wid, 'completed', 'system', 'engine_v1', "
            "now(), now(), now())"
        ),
        {"id": run_id, "tid": tid, "wid": wid},
    )
    await admin_session.commit()

    app = create_app(
        settings=AppSettings(
            app_env="test",
            database_url=pg_url,
            use_postgres_conversation_summaries=True,
        )
    )
    assert isinstance(
        app.state.conv_summary_repo, PostgresConversationSummaryRepository
    )

    cid = str(_uuid.uuid4())
    summary = ConversationSummary(
        id=cid,
        tenant_id=tid,
        workspace_id=wid,
        run_id=run_id,
        persona_id="p1",
        persona_name="Persona",
        persona_type="standard",
        verdict="pass",
        pass_rate=1.0,
        turn_count=2,
        judge_scores={},
        failure_reason=None,
        failure_category=None,
        transcript_ref=None,
        tags={},
    )
    await app.state.conv_summary_repo.upsert(summary)

    # Engine source-of-truth: row visible via admin_session
    row = await admin_session.execute(
        text(
            "SELECT id FROM conversation_summaries WHERE id = :cid"
        ),
        {"cid": cid},
    )
    fetched_id = row.scalar_one_or_none()
    # asyncpg returns UUID type for UUID columns; coerce to str for comparison
    assert fetched_id is not None
    assert str(fetched_id) == cid


# W-conv-3: use_postgres_conversation_summaries=True without database_url raises
async def test_use_postgres_conversation_summaries_without_database_url_raises_fatal_configuration_error() -> None:
    """W-conv-3: use_postgres_conversation_summaries=True with
    database_url=None → create_app raises FatalConfigurationError.
    """
    with pytest.raises(FatalConfigurationError, match="database_url"):
        create_app(
            settings=AppSettings(
                app_env="test",
                database_url=None,
                use_postgres_conversation_summaries=True,
            )
        )
