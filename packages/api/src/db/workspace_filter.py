"""Canonical workspace-filter parameter helper (Turn 2 plan §5.7).

Used by every workspace-scoped repository to build the dual-filter
parameter dict passed into parameterized SQL:

    async def get(self, ctx: TenantContext, asset_id: str) -> AssetRecord:
        params = {**workspace_filter_params(ctx), "asset_id": asset_id}
        result = await session.execute(
            text(
                "SELECT ... FROM assets "
                "WHERE tenant_id = :tenant_id "
                "  AND workspace_id = :workspace_id "
                "  AND id = :asset_id"
            ),
            params,
        )

Scope — which Turn 2 repos use this helper:

  * Category A workspace-scoped (Turn 2.5):
      runs, comparisons, dashboard_artifacts
  * Category B workspace-scoped (Turn 2.5):
      assets, conversations
  * Category B tenant-scoped (Turn 2 — THIS STEP):
      tenants, workspaces, memberships
    These do NOT use this helper. Tenants/workspaces are scoped by
    tenant_id only (workspaces.id is the thing being looked up; using
    ctx.workspace_id would be self-referential). Memberships use an
    explicit (tenant, user, workspace_id-or-null) key rather than
    ctx.workspace_id.

Keeping this helper in one module (instead of inlining the dict literal
in every query site) means the dual-filter shape is audit-able by
grepping for a single symbol. Any future addition of a third filter
key (e.g., workspace_cohort_id) lands here once rather than in a
dozen repositories.
"""
from __future__ import annotations

from src.common.models import TenantContext

__all__ = ["workspace_filter_params"]


def workspace_filter_params(ctx: TenantContext) -> dict[str, str]:
    """Return the standard `{tenant_id, workspace_id}` bind-param dict.

    Callers spread this into their own params dict:

        params = {**workspace_filter_params(ctx), "id": some_id}

    The returned dict is fresh on every call so callers can mutate it
    without side effects.
    """
    return {
        "tenant_id": ctx.tenant_id,
        "workspace_id": ctx.workspace_id,
    }
