"""PostgresMembershipRepository tests — Turn 2 Step 2 (6 tests).

Verifies the three contract methods (`get_role`, `create`,
`list_for_user_in_tenant`) against a real Postgres 16 instance, with
particular attention to the plan §6.5 dual-membership shape and the
§7.1 partial unique indexes.

Distinct from `tests/db/test_memberships_uniqueness.py` (which tests the
raw DB-layer partial unique indexes via raw INSERT): this file tests
the repository-layer behavior — IntegrityError translation, domain
exceptions, ordering guarantees, and the get_role None-vs-role contract
that Turn 3 `get_actor_role()` depends on.
"""
from __future__ import annotations

import pytest

from src.api.errors import DuplicateMembership
from src.memberships import PostgresMembershipRepository
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


async def _seed_tenant_with_workspace(
    name_hint: str,
) -> tuple[str, str]:
    """Helper: create a tenant + default workspace, return their ids."""
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.create(name=name_hint, slug=f"m-{name_hint}")
    ws = await w_repo.create(
        tenant_id=tenant.id,
        name=f"{name_hint} default",
        is_default=True,
    )
    return tenant.id, ws.id


async def test_create_tenant_level_membership(clean_db: str) -> None:
    """Create a membership row with workspace_id=None (tenant-level).

    Verifies the plan §6.5 tenant-level shape:
      * Record round-trips through domain ↔ ORM
      * workspace_id is preserved as None (not a UUID, not "")
      * is_tenant_level / is_workspace_level properties read correctly
      * get_role returns the role for the same (tenant_id, user_id, None) key
    """
    tenant_id, _ws_id = await _seed_tenant_with_workspace("tl")
    repo = PostgresMembershipRepository()

    created = await repo.create(
        tenant_id=tenant_id,
        user_id="user_123",
        workspace_id=None,
        role="admin",
    )
    assert created.tenant_id == tenant_id
    assert created.user_id == "user_123"
    assert created.workspace_id is None
    assert created.role == "admin"
    assert created.is_tenant_level is True
    assert created.is_workspace_level is False

    role = await repo.get_role(
        tenant_id=tenant_id,
        user_id="user_123",
        workspace_id=None,
    )
    assert role == "admin"


async def test_create_workspace_level_membership(clean_db: str) -> None:
    """Create a membership row with workspace_id set (workspace-level).

    Verifies the plan §6.5 workspace-level shape. Also confirms the
    tenant-level and workspace-level lookups are distinct: a
    workspace-level row for (T, U, W) does NOT answer a
    tenant-level lookup (T, U, None).
    """
    tenant_id, ws_id = await _seed_tenant_with_workspace("wl")
    repo = PostgresMembershipRepository()

    created = await repo.create(
        tenant_id=tenant_id,
        user_id="user_ws",
        workspace_id=ws_id,
        role="member",
    )
    assert created.tenant_id == tenant_id
    assert created.user_id == "user_ws"
    assert created.workspace_id == ws_id
    assert created.role == "member"
    assert created.is_tenant_level is False
    assert created.is_workspace_level is True

    # Workspace-level lookup finds it
    role_ws = await repo.get_role(
        tenant_id=tenant_id,
        user_id="user_ws",
        workspace_id=ws_id,
    )
    assert role_ws == "member"

    # Tenant-level lookup DOES NOT find it — the partial-index semantics
    # are repository-respected: workspace-level rows don't satisfy
    # tenant-level queries.
    role_tl = await repo.get_role(
        tenant_id=tenant_id,
        user_id="user_ws",
        workspace_id=None,
    )
    assert role_tl is None


async def test_get_role_returns_none_when_no_membership(
    clean_db: str,
) -> None:
    """Negative lookup: user has no membership in the tenant → None.

    This is the exact shape Turn 3 `get_actor_role()` relies on. Raising
    here would break the fallback ladder (try workspace, then tenant,
    then 403)."""
    tenant_id, ws_id = await _seed_tenant_with_workspace("none")
    repo = PostgresMembershipRepository()

    # No rows inserted — both lookups return None cleanly
    assert (
        await repo.get_role(
            tenant_id=tenant_id,
            user_id="ghost_user",
            workspace_id=None,
        )
        is None
    )
    assert (
        await repo.get_role(
            tenant_id=tenant_id,
            user_id="ghost_user",
            workspace_id=ws_id,
        )
        is None
    )


async def test_duplicate_tenant_level_raises_duplicate_membership(
    clean_db: str,
) -> None:
    """Inserting a second tenant-level row for the same (tenant, user)
    fires uq_memberships_tenant_level → DuplicateMembership (409) with
    scope='tenant_level' in details.

    This is the plan v0.5.1 MF-1 invariant at the repository layer."""
    tenant_id, _ws_id = await _seed_tenant_with_workspace("dup-tl")
    repo = PostgresMembershipRepository()

    await repo.create(
        tenant_id=tenant_id,
        user_id="dup_user",
        workspace_id=None,
        role="admin",
    )
    with pytest.raises(DuplicateMembership) as exc_info:
        await repo.create(
            tenant_id=tenant_id,
            user_id="dup_user",
            workspace_id=None,
            role="viewer",  # different role — still rejected
        )
    assert exc_info.value.http_status == 409
    assert exc_info.value.details.get("scope") == "tenant_level"


async def test_duplicate_workspace_level_raises_duplicate_membership(
    clean_db: str,
) -> None:
    """Inserting a second workspace-level row for the same
    (tenant, user, workspace) fires uq_memberships_workspace_level →
    DuplicateMembership (409) with scope='workspace_level' in details.

    Also confirms tenant-level and workspace-level rows coexist for the
    same (tenant, user) — the dual-membership shape bootstrap creates."""
    tenant_id, ws_id = await _seed_tenant_with_workspace("dup-wl")
    repo = PostgresMembershipRepository()

    # Dual membership — both rows accepted
    await repo.create(
        tenant_id=tenant_id,
        user_id="dup_user",
        workspace_id=None,
        role="admin",
    )
    await repo.create(
        tenant_id=tenant_id,
        user_id="dup_user",
        workspace_id=ws_id,
        role="admin",
    )

    # Second workspace-level row for same (T, U, W) — rejected
    with pytest.raises(DuplicateMembership) as exc_info:
        await repo.create(
            tenant_id=tenant_id,
            user_id="dup_user",
            workspace_id=ws_id,
            role="member",
        )
    assert exc_info.value.http_status == 409
    assert exc_info.value.details.get("scope") == "workspace_level"


async def test_list_for_user_returns_both_tenant_and_workspace_rows(
    clean_db: str,
) -> None:
    """The dual-read path used by Turn 3 authz precedence resolver.

    After bootstrap creates the dual-membership (tenant-level +
    workspace-level for the same user), list_for_user_in_tenant returns
    BOTH rows ordered deterministically: tenant-level first, then
    workspace-level by workspace_id."""
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    m_repo = PostgresMembershipRepository()

    tenant = await t_repo.create(name="Dual", slug="m-dual")
    ws_a = await w_repo.create(
        tenant_id=tenant.id, name="Default", is_default=True
    )
    ws_b = await w_repo.create(
        tenant_id=tenant.id, name="Secondary", is_default=False
    )

    await m_repo.create(
        tenant_id=tenant.id,
        user_id="multi_user",
        workspace_id=None,
        role="admin",
    )
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="multi_user",
        workspace_id=ws_a.id,
        role="member",
    )
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="multi_user",
        workspace_id=ws_b.id,
        role="viewer",
    )
    # Add noise — a membership for a different user in the same tenant
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="other_user",
        workspace_id=None,
        role="viewer",
    )

    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=tenant.id,
        user_id="multi_user",
    )

    # All three rows for multi_user, and only for multi_user
    assert len(rows) == 3
    assert all(r.user_id == "multi_user" for r in rows)
    assert all(r.tenant_id == tenant.id for r in rows)

    # Ordering: tenant-level first (workspace_id IS NULL, NULLS FIRST),
    # then workspace-level rows. Exact ordering of workspace-level rows
    # is not part of the contract — only that tenant-level comes first.
    assert rows[0].workspace_id is None
    assert rows[0].role == "admin"
    assert rows[1].workspace_id is not None
    assert rows[2].workspace_id is not None
    assert {rows[1].workspace_id, rows[2].workspace_id} == {ws_a.id, ws_b.id}
