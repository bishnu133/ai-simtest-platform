"""PostgresWorkspaceRepository tests — Turn 2 Step 2 (4 tests).

Verifies the three contract methods (`get_by_id`, `get_default_for_tenant`,
`create`) against a real Postgres 16 instance.

Notable coverage:
  * §5.7 info-leak guard — cross-tenant workspace probe raises
    CrossTenantForbidden, not WorkspaceNotFound, so existence leaks
    cannot be derived from 404-vs-403 response shapes.
  * "At most one default workspace per tenant" — enforced at the
    application layer in Turn 2 (see PostgresWorkspaceRepository.create
    docstring); a proper partial unique index migration is queued for
    Foundation Hardening #9.
"""
from __future__ import annotations

import pytest

from src.api.errors import (
    CrossTenantForbidden,
    DuplicateWorkspace,
    WorkspaceNotFound,
)
from src.common.models import ActorRef, TenantContext
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: str, workspace_id: str = "00000000-0000-0000-0000-000000000000") -> TenantContext:
    """Build a minimal TenantContext for tests. The workspace_id here
    is just a placeholder for context-construction — the workspace repo
    does NOT filter by ctx.workspace_id (see module docstring)."""
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=ActorRef(actor_type="human", actor_id="test-user"),
    )


async def test_create_then_get_by_id_roundtrip(clean_db: str) -> None:
    """Happy path: create a workspace, fetch it by id under the same
    tenant's context. Fields round-trip."""
    tenant_repo = PostgresTenantRepository()
    ws_repo = PostgresWorkspaceRepository()

    tenant = await tenant_repo.create(name="T", slug="t-ws-roundtrip")
    created = await ws_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )

    assert created.tenant_id == tenant.id
    assert created.name == "Default"
    assert created.is_default is True

    fetched = await ws_repo.get_by_id(_ctx(tenant.id), created.id)
    assert fetched.id == created.id
    assert fetched.tenant_id == tenant.id
    assert fetched.name == "Default"
    assert fetched.is_default is True


async def test_get_by_id_across_tenants_raises_cross_tenant(
    clean_db: str,
) -> None:
    """§5.7 info-leak guard: looking up a workspace_id that exists in a
    different tenant raises CrossTenantForbidden (403), NOT
    WorkspaceNotFound (404). This prevents existence probing across
    tenant boundaries."""
    tenant_repo = PostgresTenantRepository()
    ws_repo = PostgresWorkspaceRepository()

    tenant_a = await tenant_repo.create(name="A", slug="ws-cross-a")
    tenant_b = await tenant_repo.create(name="B", slug="ws-cross-b")

    # Create a workspace under tenant_a
    ws_a = await ws_repo.create(
        tenant_id=tenant_a.id,
        name="A's Workspace",
        is_default=True,
    )

    # Attempt to look it up from tenant_b's context → 403, not 404
    with pytest.raises(CrossTenantForbidden) as exc_info:
        await ws_repo.get_by_id(_ctx(tenant_b.id), ws_a.id)

    assert exc_info.value.http_status == 403

    # Sanity: a genuinely nonexistent id from tenant_b's context → 404
    with pytest.raises(WorkspaceNotFound):
        await ws_repo.get_by_id(
            _ctx(tenant_b.id),
            "00000000-0000-0000-0000-000000000000",
        )


async def test_get_default_for_tenant_returns_default_workspace(
    clean_db: str,
) -> None:
    """Bootstrap lookup: after creating a default workspace,
    get_default_for_tenant returns it. Returns None if the tenant has
    no default workspace yet (initial-state case)."""
    tenant_repo = PostgresTenantRepository()
    ws_repo = PostgresWorkspaceRepository()

    tenant = await tenant_repo.create(name="Default Test", slug="ws-default")

    # Before any workspaces: None
    assert await ws_repo.get_default_for_tenant(tenant.id) is None

    # Create a non-default workspace: still None
    await ws_repo.create(
        tenant_id=tenant.id,
        name="Non-Default",
        is_default=False,
    )
    assert await ws_repo.get_default_for_tenant(tenant.id) is None

    # Create the default workspace: now returns it
    default_ws = await ws_repo.create(
        tenant_id=tenant.id,
        name="The Default",
        is_default=True,
    )
    fetched = await ws_repo.get_default_for_tenant(tenant.id)
    assert fetched is not None
    assert fetched.id == default_ws.id
    assert fetched.is_default is True


async def test_only_one_default_workspace_per_tenant(
    clean_db: str,
) -> None:
    """Application-layer enforcement of 'at most one default workspace
    per tenant' (Turn 2 check-then-insert, FH #9 promotes to DB-level
    partial unique index). A second is_default=True insert for the same
    tenant raises DuplicateWorkspace (409).

    Non-default workspaces for the same tenant remain unrestricted."""
    tenant_repo = PostgresTenantRepository()
    ws_repo = PostgresWorkspaceRepository()

    tenant = await tenant_repo.create(name="Uniq", slug="ws-uniq-default")

    # First default workspace — ok
    await ws_repo.create(
        tenant_id=tenant.id,
        name="First Default",
        is_default=True,
    )

    # Second default workspace — blocked
    with pytest.raises(DuplicateWorkspace) as exc_info:
        await ws_repo.create(
            tenant_id=tenant.id,
            name="Second Default",
            is_default=True,
        )
    assert exc_info.value.http_status == 409
    assert exc_info.value.details.get("conflict") == "default_workspace"

    # Non-default workspaces — still allowed, no limit
    ws_a = await ws_repo.create(
        tenant_id=tenant.id, name="Non-Default A", is_default=False
    )
    ws_b = await ws_repo.create(
        tenant_id=tenant.id, name="Non-Default B", is_default=False
    )
    assert ws_a.id != ws_b.id
