"""Run service layer with mandatory state transition helper (v1.2.2 §M1, Correction 2).

`RunStateTransition.transition()` is the **only sanctioned path** for run
status mutations. It performs three actions as an **ordered best-effort
sequence with fail-fast semantics**, NOT transactional atomicity:

  1. Repository update via `_update_status`. Raises -> abort, no audit, no
     cache invalidation. Original status preserved.

  2. Audit event emission. Raises after step 1 succeeded -> log a
     CONSISTENCY_WARNING, still call dashboard_cache.invalidate (so cache
     doesn't serve stale from_status), then re-raise.

  3. Cache invalidation. Raises -> log CACHE_WARNING and swallow (TTL
     fallback). Repository and audit are already consistent at this point.

When real Postgres lands, steps 1+2 will be wrapped in a single DB
transaction and the consistency-warning path becomes dead code.
"""
from __future__ import annotations

import logging
from typing import Callable

from src.audit.logger import AuditActions, audit_logger
from src.common.models import TenantContext
from src.runs.models import RunRecord, RunStatus
from src.runs.repository import RunRepository, RunStatusMutating

logger = logging.getLogger(__name__)


# Map RunStatus -> AuditActions canonical action code
_STATUS_TO_AUDIT_ACTION: dict[RunStatus, str] = {
    RunStatus.QUEUED: AuditActions.RUN_QUEUED,
    RunStatus.RUNNING: AuditActions.RUN_STARTED,
    RunStatus.COMPLETED: AuditActions.RUN_COMPLETED,
    RunStatus.FAILED: AuditActions.RUN_FAILED,
    RunStatus.CANCELLED: AuditActions.RUN_CANCELLED,
}


# Type alias for the cache invalidation hook. Default is a no-op so the
# runs/ package has no hard dependency on results/cache. The results
# package wires its real cache.invalidate via set_dashboard_invalidator()
# at app startup (in main.py).
InvalidatorFn = Callable[[str], None]


def _noop_invalidator(run_id: str) -> None:
    return None


_dashboard_invalidator: InvalidatorFn = _noop_invalidator


def set_dashboard_invalidator(fn: InvalidatorFn) -> None:
    """Wire the dashboard cache invalidator. Called from main.py at startup."""
    global _dashboard_invalidator
    _dashboard_invalidator = fn


class RunStateTransition:
    """The only sanctioned path for mutating run status."""

    def __init__(self, repo: RunStatusMutating):
        self._repo = repo

    async def transition(
        self,
        ctx: TenantContext,
        run_id: str,
        new_status: RunStatus,
    ) -> RunRecord:
        # Step 1: repository update (fail-fast)
        record = await self._repo._update_status(ctx, run_id, new_status)

        # Step 2: audit event emission
        try:
            audit_logger.write(
                ctx,
                action=_STATUS_TO_AUDIT_ACTION[new_status],
                resource_type="run",
                resource_id=run_id,
                metadata={"new_status": new_status.value},
            )
        except Exception as audit_exc:
            logger.error(
                "CONSISTENCY_WARNING: run state advanced but audit failed: "
                "run_id=%s new_status=%s error=%s",
                run_id,
                new_status.value,
                audit_exc,
            )
            # Still invalidate cache so we don't serve stale from_status
            try:
                _dashboard_invalidator(run_id)
            except Exception:
                pass
            raise

        # Step 3: cache invalidation (best-effort)
        try:
            _dashboard_invalidator(run_id)
        except Exception as cache_exc:
            logger.warning(
                "CACHE_WARNING: dashboard cache invalidation failed for run %s: %s",
                run_id,
                cache_exc,
            )

        return record


class RunService:
    """Read-side run service for routers."""

    def __init__(self, repo: RunRepository):
        # repo must satisfy both RunRepository (public) and
        # RunStatusMutating (internal). Both shipped impls satisfy both
        # protocols. We type the parameter as the public Protocol so
        # callers don't have to know about the internal mutation surface;
        # the runtime check below catches a mis-wired repo at startup
        # rather than at the first state transition.
        if not isinstance(repo, RunStatusMutating):
            raise TypeError(
                f"RunService requires a repo satisfying RunStatusMutating; "
                f"got {type(repo).__name__}"
            )
        self._repo = repo
        self.transitions = RunStateTransition(repo)

    async def get_run(self, ctx: TenantContext, run_id: str) -> RunRecord:
        return await self._repo.get(ctx, run_id)

    async def list_runs(self, ctx: TenantContext) -> list[RunRecord]:
        return await self._repo.list_for_tenant(ctx)

    async def create_run(self, record: RunRecord) -> RunRecord:
        return await self._repo.create(record)
