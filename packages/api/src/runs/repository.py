"""Run repository: protocol + in-memory implementation (v1.2.2 §M1).

`_update_status` is **internal-only by convention** (single-underscore
prefix). It is not part of the public repository contract and must only
be called by `RunStateTransition` in `runs/service.py`.

Per v1.2.2 Correction 1, true Python name mangling (`__update_status`) is
intentionally NOT used because:
  - the protocol surface is in-process only (not a security boundary), and
  - name mangling would make subclassing the protocol awkward.

A static check in `test_runs_repository.py` asserts that `_update_status`
is absent from `RunRepository.__all__` exports, so accidental promotion to
the public surface fails the test suite.
"""
from __future__ import annotations

from typing import Protocol

from src.api.errors import CrossTenantForbidden, RunNotFound
from src.common.models import TenantContext, utcnow
from src.runs.models import RunRecord, RunStatus

__all__ = ["RunRepository", "InMemoryRunRepository"]


class RunRepository(Protocol):
    """Public surface of the run repository.

    Note that `_update_status` is intentionally absent. Status mutations
    must go through `RunStateTransition` in `runs/service.py`.
    """

    async def create(self, record: RunRecord) -> RunRecord: ...

    async def get(self, ctx: TenantContext, run_id: str) -> RunRecord: ...

    async def list_for_tenant(self, ctx: TenantContext) -> list[RunRecord]: ...


class InMemoryRunRepository:
    """In-memory implementation. Replaced by Postgres in a later S2 week."""

    def __init__(self) -> None:
        # (tenant_id, workspace_id, run_id) -> RunRecord
        self._records: dict[tuple[str, str, str], RunRecord] = {}

    def _key(self, tenant_id: str, workspace_id: str, run_id: str) -> tuple[str, str, str]:
        return (tenant_id, workspace_id, run_id)

    async def create(self, record: RunRecord) -> RunRecord:
        key = self._key(record.tenant_id, record.workspace_id, record.run_id)
        self._records[key] = record
        return record

    async def get(self, ctx: TenantContext, run_id: str) -> RunRecord:
        # Look up by tenant+workspace first; if a record exists under a
        # different tenant for this run_id, raise CrossTenantForbidden so
        # the caller can return 403 instead of 404 (information leak guard).
        key = self._key(ctx.tenant_id, ctx.workspace_id, run_id)
        record = self._records.get(key)
        if record is not None:
            return record
        # Check if any other tenant owns this run_id
        for (t, _w, r), rec in self._records.items():
            if r == run_id and t != ctx.tenant_id:
                raise CrossTenantForbidden(
                    f"Run {run_id} belongs to a different tenant"
                )
        raise RunNotFound(f"Run {run_id} not found")

    async def list_for_tenant(self, ctx: TenantContext) -> list[RunRecord]:
        return [
            r
            for (t, w, _r), r in self._records.items()
            if t == ctx.tenant_id and w == ctx.workspace_id
        ]

    # ---- Internal-only — not part of public protocol ----------------------

    async def _update_status(
        self,
        ctx: TenantContext,
        run_id: str,
        new_status: RunStatus,
    ) -> RunRecord:
        """Mutate run status. ONLY callable by RunStateTransition.

        See module docstring and v1.2.2 §M1 for the rationale.
        """
        record = await self.get(ctx, run_id)
        record.status = new_status
        now = utcnow()
        if new_status == RunStatus.RUNNING and record.started_at is None:
            record.started_at = now
        if new_status.is_terminal and record.completed_at is None:
            record.completed_at = now
        return record
