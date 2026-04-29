"""Workspaces default-uniqueness migration tests — Turn 2.5 Step 1 (4 tests).

Verifies the ``uq_workspaces_one_default_per_tenant`` partial unique index
added by Alembic revision 0002 (Turn 2.5 plan v0.2.1 §3.1 D-Mig + MF-5).

Tests 1-3 use the raw `admin_session` fixture to exercise the DB index
directly (proving the partial unique-ness is per-tenant and only fires
on `is_default=true` rows). Test 4 exercises the public
``PostgresWorkspaceRepository.create`` API to prove that the
``IntegrityError`` is mapped to ``DuplicateWorkspace`` (the MF-5
end-to-end guarantee — DB partial unique index is the source of truth,
repository catches and translates).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import DuplicateWorkspace
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


async def _seed_tenant(admin_session: AsyncSession, slug: str) -> str:
    """Insert a tenant via raw SQL; return its id.

    Relies on server_default for `id`, `plan_id`, `settings`, `created_at`,
    and `updated_at` columns. Inserts only the fields that have no
    server_default: `name`, `slug`.
    """
    result = await admin_session.execute(
        text(
            "INSERT INTO tenants (name, slug) "
            "VALUES (:name, :slug) RETURNING id"
        ),
        {"name": f"Tenant {slug}", "slug": slug},
    )
    tenant_id = result.scalar_one()
    await admin_session.commit()
    return str(tenant_id)


async def _insert_workspace(
    admin_session: AsyncSession,
    tenant_id: str,
    name: str,
    is_default: bool,
) -> None:
    """Insert a workspace via raw SQL with an explicit is_default value."""
    await admin_session.execute(
        text(
            "INSERT INTO workspaces (id, tenant_id, name, is_default) "
            "VALUES (gen_random_uuid(), :tenant_id, :name, :is_default)"
        ),
        {"tenant_id": tenant_id, "name": name, "is_default": is_default},
    )
    await admin_session.commit()


async def test_partial_unique_index_blocks_second_default_for_same_tenant(
    admin_session: AsyncSession,
) -> None:
    """Insert tenant T1 + workspace W1(default=true). A second insert with
    tenant T1 + W2(default=true) must raise IntegrityError from the
    ``uq_workspaces_one_default_per_tenant`` partial unique index."""
    t1 = await _seed_tenant(admin_session, "t1-blocks-second-default")

    # First default inserts cleanly.
    await _insert_workspace(admin_session, t1, "W1", is_default=True)

    # Second default for same tenant violates the partial unique index.
    with pytest.raises(IntegrityError) as exc_info:
        await _insert_workspace(admin_session, t1, "W2", is_default=True)

    # Constraint name is the migration index name.
    assert "uq_workspaces_one_default_per_tenant" in str(exc_info.value)


async def test_non_default_rows_in_same_tenant_freely_coexist(
    admin_session: AsyncSession,
) -> None:
    """Insert tenant T1 + W1(default=true) + W2(default=false) +
    W3(default=false). All three succeed; the partial index does not
    fire on `is_default=false` rows because the WHERE clause excludes
    them entirely."""
    t1 = await _seed_tenant(admin_session, "t1-non-default-coexist")

    await _insert_workspace(admin_session, t1, "W1", is_default=True)
    await _insert_workspace(admin_session, t1, "W2", is_default=False)
    await _insert_workspace(admin_session, t1, "W3", is_default=False)

    # Verify all three rows landed.
    result = await admin_session.execute(
        text("SELECT COUNT(*) FROM workspaces WHERE tenant_id = :tid"),
        {"tid": t1},
    )
    assert result.scalar_one() == 3


async def test_two_tenants_each_have_their_own_default(
    admin_session: AsyncSession,
) -> None:
    """Insert T1+W1(default=true) and T2+W2(default=true). Both succeed
    because the partial unique index is scoped to tenant_id."""
    t1 = await _seed_tenant(admin_session, "t1-two-tenants-own-default")
    t2 = await _seed_tenant(admin_session, "t2-two-tenants-own-default")

    await _insert_workspace(admin_session, t1, "W1", is_default=True)
    await _insert_workspace(admin_session, t2, "W2", is_default=True)

    # Both default rows are present.
    result = await admin_session.execute(
        text(
            "SELECT COUNT(*) FROM workspaces "
            "WHERE is_default = true AND tenant_id IN (:t1, :t2)"
        ),
        {"t1": t1, "t2": t2},
    )
    assert result.scalar_one() == 2


async def test_postgres_workspace_repository_create_maps_integrity_error_to_duplicate_workspace(
    clean_db: str,
) -> None:
    """End-to-end MF-5 guarantee: via the public
    ``PostgresWorkspaceRepository.create`` API, the second create with
    is_default=True for the same tenant raises ``DuplicateWorkspace``
    (NOT ``IntegrityError``). The repository now drops the app-level
    check-then-insert and catches IntegrityError from the DB index,
    translating it to the public-surface domain error.
    """
    tenant_repo = PostgresTenantRepository()
    ws_repo = PostgresWorkspaceRepository()

    tenant = await tenant_repo.create(name="T", slug="t-mf5-end-to-end")

    # First default workspace: succeeds.
    first = await ws_repo.create(
        tenant_id=tenant.id, name="default", is_default=True
    )
    assert first.is_default is True

    # Second default workspace for the same tenant: must raise the
    # public domain error, not the raw SQLAlchemy IntegrityError.
    with pytest.raises(DuplicateWorkspace) as exc_info:
        await ws_repo.create(
            tenant_id=tenant.id, name="another-default", is_default=True
        )

    # The error carries useful diagnostics.
    err = exc_info.value
    assert err.details["tenant_id"] == tenant.id
    assert err.details["conflict"] == "default_workspace"
    # Underlying cause is the IntegrityError from the partial unique index.
    assert isinstance(err.__cause__, IntegrityError)
