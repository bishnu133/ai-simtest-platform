"""Future-4 regression — create_app must NOT swap the global engine
when settings.database_url already matches.

Background
----------
Plan v0.4 narrowed a suite-only failure of
test_factory_built_app_serves_one_authenticated_request_end_to_end_postgres
to ``_build_engine()`` in ``src/app_factory.py`` calling
``configure_engine_from_url()`` unconditionally. That nulls the
module-level ``_engine`` / ``_sessionmaker`` without awaiting an async
dispose. Mid-test, this broke the visibility of just-committed rows
to a subsequently-rebuilt engine — bootstrap re-ran the create path
on the same claims and produced a duplicate tenant_id, which the
endpoint's cross-tenant guard rejected as 403.

The fix made ``_build_engine`` idempotent against same-URL re-calls.
These three tests pin that contract.
"""
from __future__ import annotations

import sqlalchemy as sa
import pytest

from src.app_factory import create_app
from src.auth.bootstrap import bootstrap
from src.auth.provider import VerifiedClaims
from src.config import AppSettings
from src.db.session import get_engine, get_sessionmaker, raw_admin_session


pytestmark = pytest.mark.asyncio


async def test_create_app_preserves_global_engine_when_url_matches(
    clean_db: str,
) -> None:
    """A same-URL create_app must reuse the already-configured engine.

    The engine identity must be preserved across the call; otherwise
    any in-flight session whose connections were bound to the prior
    engine becomes operationally orphaned.
    """
    engine_before = get_engine()
    settings = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=True,
        database_url=clean_db,
        allow_self_serve_provisioning=True,
    )
    create_app(settings=settings)
    engine_after = get_engine()

    assert engine_before is engine_after, (
        "create_app rebuilt the global engine on a same-URL call. "
        "This breaks visibility of writes committed before create_app "
        "ran. See Future-4 root cause."
    )


async def test_bootstrap_commit_visible_after_create_app(clean_db: str) -> None:
    """Visibility contract: a row committed before create_app must
    still be visible to a fresh raw_admin_session opened after.

    This is the precise invariant
    ``test_factory_built_app_serves_one_authenticated_request_end_to_end_postgres``
    relies on — Phase 1 commits, Phase 2 builds the app, Phase 4
    reads. The read must see the write.
    """
    sm = get_sessionmaker()
    claims = VerifiedClaims(
        user_id="user_future4_visibility",
        org_id="org_future4_visibility",
        provider_org_role="admin",
        display_name="Future-4 Visibility",
    )
    async with sm() as session:
        first = await bootstrap(
            session, claims, allow_self_serve_provisioning=True
        )
    assert first.is_first_user is True

    settings = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=True,
        database_url=clean_db,
        allow_self_serve_provisioning=True,
    )
    create_app(settings=settings)

    async with raw_admin_session() as probe:
        rows = (
            await probe.execute(
                sa.text(
                    "SELECT id::text FROM tenants WHERE clerk_org_id = :o"
                ),
                {"o": claims.org_id},
            )
        ).all()
    assert len(rows) == 1, (
        f"Expected 1 tenant row for {claims.org_id}, got {rows}. "
        "create_app's engine handling broke read visibility of an "
        "earlier commit."
    )
    assert rows[0][0] == first.tenant_id


async def test_two_bootstraps_around_create_app_are_idempotent(
    clean_db: str,
) -> None:
    """A bootstrap → create_app(same URL) → bootstrap(same claims)
    sequence must take the existing-row path on the second call and
    return the SAME tenant_id.

    This is the failure mode of Future-4: the second bootstrap
    silently re-ran the create path and produced a different
    tenant_id, leaving the endpoint cross-tenant check at 403.
    """
    sm = get_sessionmaker()
    claims = VerifiedClaims(
        user_id="user_future4_idempotency",
        org_id="org_future4_idempotency",
        provider_org_role="admin",
        display_name="Future-4 Idempotency",
    )
    async with sm() as s1:
        a = await bootstrap(
            s1, claims, allow_self_serve_provisioning=True
        )
    assert a.is_first_user is True

    settings = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=True,
        database_url=clean_db,
        allow_self_serve_provisioning=True,
    )
    create_app(settings=settings)

    sm2 = get_sessionmaker()
    async with sm2() as s2:
        b = await bootstrap(
            s2, claims, allow_self_serve_provisioning=True
        )

    assert b.is_first_user is False, (
        f"Second bootstrap took the create path "
        f"(is_first_user=True) instead of finding the row from the "
        f"first bootstrap. a.tenant_id={a.tenant_id}, "
        f"b.tenant_id={b.tenant_id}. This is the exact symptom "
        f"Plan v0.4 was scoped to fix."
    )
    assert a.tenant_id == b.tenant_id
    assert a.workspace_id == b.workspace_id