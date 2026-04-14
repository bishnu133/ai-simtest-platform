"""Comparison repository with locked sort order: created_at DESC, id DESC (v1.2.2 §S4)."""
from __future__ import annotations

from typing import Protocol

from src.api.errors import APIError, CrossTenantForbidden
from src.common.models import TenantContext
from src.comparisons.models import ComparisonRecord


class ComparisonNotFound(APIError):
    code = "comparison_not_found"
    http_status = 404


class ComparisonRepository(Protocol):
    async def create(self, record: ComparisonRecord) -> ComparisonRecord: ...
    async def get(self, ctx: TenantContext, comparison_id: str) -> ComparisonRecord: ...
    async def list_for_tenant(self, ctx: TenantContext) -> list[ComparisonRecord]: ...


class InMemoryComparisonRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], ComparisonRecord] = {}

    def _key(self, tenant_id: str, workspace_id: str, comparison_id: str):
        return (tenant_id, workspace_id, comparison_id)

    async def create(self, record: ComparisonRecord) -> ComparisonRecord:
        k = self._key(record.tenant_id, record.workspace_id, record.id)
        self._records[k] = record
        return record

    async def get(self, ctx: TenantContext, comparison_id: str) -> ComparisonRecord:
        k = self._key(ctx.tenant_id, ctx.workspace_id, comparison_id)
        record = self._records.get(k)
        if record is not None:
            return record
        # Cross-tenant info-leak guard
        for (t, _w, cid), rec in self._records.items():
            if cid == comparison_id and t != ctx.tenant_id:
                raise CrossTenantForbidden(
                    f"Comparison {comparison_id} belongs to a different tenant"
                )
        raise ComparisonNotFound(f"Comparison {comparison_id} not found")

    async def list_for_tenant(self, ctx: TenantContext) -> list[ComparisonRecord]:
        items = [
            r
            for (t, w, _c), r in self._records.items()
            if t == ctx.tenant_id and w == ctx.workspace_id
        ]
        # LOCKED sort order per v1.2.2 §S4: created_at DESC, id DESC
        items.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return items
