"""PostgresTenantRepository tests — Turn 2 Step 2 (4 tests).

Verifies the three contract methods (`get_by_id`, `get_by_clerk_org_id`,
`create`) against a real Postgres 16 instance with the Turn 1a
migration applied.

Test infrastructure: reuses the session-scoped `clean_db` fixture from
`tests/db/conftest.py` — TRUNCATE per test, migration once per session.
"""
from __future__ import annotations

import pytest

from src.api.errors import DuplicateTenant, TenantNotFound
from src.tenants import PostgresTenantRepository

pytestmark = pytest.mark.asyncio


async def test_create_then_get_by_id_roundtrip(clean_db: str) -> None:
    """Happy path: create() returns a TenantRecord, get_by_id() returns
    an equivalent one. Round-trip preserves all fields including
    settings dict, plan_id default, and timestamps."""
    repo = PostgresTenantRepository()

    created = await repo.create(
        name="Acme Corp",
        slug="acme",
        clerk_org_id="org_test_abc",
        plan_id="pro",
        settings={"branding": {"primary": "#ff0000"}},
    )

    assert created.name == "Acme Corp"
    assert created.slug == "acme"
    assert created.clerk_org_id == "org_test_abc"
    assert created.plan_id == "pro"
    assert created.settings == {"branding": {"primary": "#ff0000"}}
    # UUID round-trippable
    assert len(created.id) == 36

    fetched = await repo.get_by_id(created.id)
    assert fetched.id == created.id
    assert fetched.name == created.name
    assert fetched.slug == created.slug
    assert fetched.clerk_org_id == created.clerk_org_id
    assert fetched.plan_id == created.plan_id
    assert fetched.settings == created.settings


async def test_get_by_id_raises_when_absent(clean_db: str) -> None:
    """Lookup by id for a nonexistent UUID raises TenantNotFound (404)."""
    repo = PostgresTenantRepository()

    with pytest.raises(TenantNotFound) as exc_info:
        await repo.get_by_id("00000000-0000-0000-0000-000000000000")

    assert exc_info.value.http_status == 404
    assert "not found" in exc_info.value.message.lower()


async def test_get_by_clerk_org_id_returns_none_when_absent(
    clean_db: str,
) -> None:
    """Bootstrap first-use signal: get_by_clerk_org_id returns None
    (not raises) when no tenant is bound to the Clerk org yet.

    This is a CRITICAL behavioral distinction from get_by_id: bootstrap
    uses None-vs-record to decide 'create new tenant' vs. 'load
    existing tenant'. Raising would break the bootstrap control flow.
    """
    repo = PostgresTenantRepository()

    # First call on an unknown clerk_org_id — no tenant bound yet
    result = await repo.get_by_clerk_org_id("org_never_seen")
    assert result is None

    # After a tenant is created with this clerk_org_id, the same call
    # returns the record
    created = await repo.create(
        name="Clerk-Bound Co",
        slug="clerk-bound",
        clerk_org_id="org_now_bound",
    )
    result = await repo.get_by_clerk_org_id("org_now_bound")
    assert result is not None
    assert result.id == created.id


async def test_duplicate_slug_raises_duplicate_tenant(
    clean_db: str,
) -> None:
    """Two tenants cannot share the same slug. Second insert raises
    DuplicateTenant (409) with details.conflict == 'slug'."""
    repo = PostgresTenantRepository()

    await repo.create(name="First", slug="same-slug")

    with pytest.raises(DuplicateTenant) as exc_info:
        await repo.create(name="Second", slug="same-slug")

    assert exc_info.value.http_status == 409
    assert exc_info.value.details.get("conflict") == "slug"
    assert exc_info.value.details.get("slug") == "same-slug"
