"""Run repository: protocol + in-memory + Postgres implementations.

`_update_status` is **internal-only by convention** (single-underscore
prefix). It is not part of the public ``RunRepository`` protocol and must
only be called by ``RunStateTransition`` in ``runs/service.py``. To let
``RunStateTransition`` type-check both implementations without a
``cast()``, this module exposes a separate ``RunStatusMutating`` Protocol
(Turn 2.5 plan v0.2.1 §3.2 + MF-2).

Per v1.2.2 Correction 1, true Python name mangling (``__update_status``) is
intentionally NOT used because:
  - the protocol surface is in-process only (not a security boundary), and
  - name mangling would make subclassing the protocol awkward.

A static check in ``test_runs_repository.py`` asserts that ``_update_status``
is absent from ``RunRepository.__all__`` exports, so accidental promotion
to the public surface fails the test suite. ``RunStatusMutating`` is a
separate Protocol and does not violate that guard.

Per Turn 2.5 plan v0.2.1 §3.2 + AM-3 + AM-4, every public/internal method
on this module's repositories accepts an optional ``session: AsyncSession |
None = None`` kwarg, mirroring the universal-kwarg pattern shipped Cat-B
repos (tenants/workspaces/memberships) already use. When ``session=None``,
the impl opens its own session; when supplied, the caller owns the
transaction.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import (
    CrossTenantForbidden,
    CrossWorkspaceForbidden,
    RunNotFound,
)
from src.common.models import TenantContext, utcnow
from src.db.models import Run as RunORM
from src.db.session import raw_admin_session, tenant_scoped_session
from src.runs.models import RunRecord, RunStatus

# Note on `run_to_domain`/`run_to_orm`: these mapper functions are imported
# at function call time (lazy import) rather than at module load time. The
# eager form would create a circular import: this module is imported by
# `runs.service`, which is imported by `comparisons.service`, which is
# loaded transitively when `db.mappers.__init__` runs. Lazy imports break
# the cycle without changing the runtime behaviour: the mappers are pure
# functions (`db.mappers.__init__` docstring: "no DB access, no repository
# dependencies, no logging side effects") and Python caches the import
# after first call.


def _mappers() -> tuple[object, object]:
    """Lazy import of run mapper functions; see module docstring above."""
    from src.db.mappers.run import run_to_domain, run_to_orm
    return run_to_domain, run_to_orm

__all__ = [
    "RunRepository",
    "RunStatusMutating",
    "RunExistenceReading",
    "InMemoryRunRepository",
    "PostgresRunRepository",
]


class RunRepository(Protocol):
    """Public surface of the run repository.

    Note that ``_update_status`` is intentionally absent. Status mutations
    must go through ``RunStateTransition`` in ``runs/service.py``, which
    depends on the separate ``RunStatusMutating`` Protocol.

    Per AM-4 (universal-kwarg pattern), every method accepts an optional
    ``session: AsyncSession | None = None`` kwarg.
    """

    async def create(
        self,
        record: RunRecord,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord: ...

    async def get(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord: ...

    async def list_for_tenant(
        self,
        ctx: TenantContext,
        *,
        session: AsyncSession | None = None,
    ) -> list[RunRecord]: ...


@runtime_checkable
class RunStatusMutating(Protocol):
    """INTERNAL — only ``RunStateTransition`` may depend on this surface.

    The public ``RunRepository`` protocol intentionally omits
    ``_update_status`` (see this module's top docstring). This protocol
    exists so ``RunStateTransition``'s type annotation can refer to "any
    repo that supports the internal status-mutation contract" without
    depending on the concrete ``InMemoryRunRepository`` class.

    Both ``InMemoryRunRepository`` (in this module) and
    ``PostgresRunRepository`` (below) structurally satisfy this protocol.

    Per AM-4 (universal-kwarg pattern): ``_update_status`` takes the same
    optional ``session: AsyncSession | None = None`` kwarg as the public
    ``RunRepository`` methods.
    """

    async def _update_status(
        self,
        ctx: TenantContext,
        run_id: str,
        new_status: RunStatus,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord: ...


@runtime_checkable
class RunExistenceReading(Protocol):
    """Minimal sub-Protocol for AM-5 Path A (Turn 2.6 plan v0.2.1 §4 Step 6).

    Used by ``InMemoryDashboardArtifactRepository`` to validate that a run
    exists before upserting artifacts for it. Both ``InMemoryRunRepository``
    and ``PostgresRunRepository`` structurally satisfy this Protocol via
    their existing ``get()`` method, so no new methods are added to the
    repos themselves; this Protocol is declared at the consumer's edge
    (the dashboard repo) per the same discipline Turn 2.5 used for
    ``RunStatusMutating``.
    """

    async def get(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord: ...


class InMemoryRunRepository:
    """In-memory implementation. Replaced by ``PostgresRunRepository`` when
    ``AppSettings.use_postgres_runs=True`` (Turn 2.5 plan v0.2.1 §3.4).

    Per AM-4, accepts the ``session=`` kwarg on every method for API
    parity with the Postgres impl. The kwarg is unused here (``# noqa:
    ARG002``).
    """

    def __init__(self) -> None:
        # (tenant_id, workspace_id, run_id) -> RunRecord
        self._records: dict[tuple[str, str, str], RunRecord] = {}

    def _key(self, tenant_id: str, workspace_id: str, run_id: str) -> tuple[str, str, str]:
        return (tenant_id, workspace_id, run_id)

    async def create(
        self,
        record: RunRecord,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> RunRecord:
        key = self._key(record.tenant_id, record.workspace_id, record.run_id)
        self._records[key] = record
        return record

    async def get(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> RunRecord:
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

    async def list_for_tenant(
        self,
        ctx: TenantContext,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> list[RunRecord]:
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
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 — API parity
    ) -> RunRecord:
        """Mutate run status. ONLY callable by ``RunStateTransition``.

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


# ---------------------------------------------------------------------------
# Postgres implementation (Turn 2.5 plan v0.2.1 §3.2 D-RunRepo)
# ---------------------------------------------------------------------------


class PostgresRunRepository:
    """SQLAlchemy 2.x async implementation of ``RunRepository``.

    Read paths use ``tenant_scoped_session`` so RLS policies on the ``runs``
    table apply automatically. Write paths use the same so the row is
    inserted with the ``current_setting('app.current_tenant_id')`` matching
    the row's ``tenant_id`` (RLS WITH CHECK clause).

    Workspace dual-filter (defense in depth on top of RLS) via explicit
    ``WHERE tenant_id=... AND workspace_id=...`` clauses.

    §5.7 info-leak guards:
      * Cross-tenant probe → ``CrossTenantForbidden``
      * Cross-workspace-within-tenant probe → ``CrossWorkspaceForbidden``
        (D-Cwf in Turn 2.5 plan v0.2.1 §3.5)

    Session-kwarg contract (AM-3 + AM-4):
      Every public/internal method accepts ``session: AsyncSession | None = None``.
        * ``session=None``  → repo opens its own ``tenant_scoped_session``.
                              The context manager owns the transaction.
        * ``session=...``   → caller has already scoped the session to
                              the appropriate tenant_id (same one that
                              appears in ``record.tenant_id`` /
                              ``ctx.tenant_id``). Repo flushes only;
                              caller owns commit/rollback. Mismatched
                              scoping is a programming bug — RLS will
                              reject the row at WITH CHECK time.
    """

    async def create(
        self,
        record: RunRecord,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord:
        # RC-3 belt-and-braces consistency check
        if not record.tenant_id or not record.workspace_id:
            raise TypeError(
                "RunRecord must carry non-empty tenant_id and workspace_id; "
                "RunRepository.create() is not a self-context-providing surface."
            )
        if not record.run_id:
            raise TypeError("RunRecord must carry non-empty run_id")

        if session is None:
            async with tenant_scoped_session(record.tenant_id) as s:
                _, run_to_orm = _mappers()
                s.add(run_to_orm(record))
                await s.flush()
                return record
        else:
            _, run_to_orm = _mappers()
            session.add(run_to_orm(record))
            await session.flush()
            return record

    async def get(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._get_inside_session(s, ctx, run_id)
        else:
            return await self._get_inside_session(session, ctx, run_id)

    async def _get_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
    ) -> RunRecord:
        """Body of ``get()`` factored out so the session-or-caller branches
        don't duplicate the probe ladder. Reused by ``_update_status``."""
        primary = await s.execute(
            select(RunORM).where(
                RunORM.id == run_id,
                RunORM.tenant_id == ctx.tenant_id,
                RunORM.workspace_id == ctx.workspace_id,
            )
        )
        row = primary.scalar_one_or_none()
        if row is not None:
            run_to_domain, _ = _mappers()
            return run_to_domain(row)

        # Cross-workspace probe (D-Cwf). Same tenant context, safe to
        # reuse `s` whether owned or caller-supplied.
        ws_probe = await s.execute(
            select(RunORM.id).where(
                RunORM.id == run_id,
                RunORM.tenant_id == ctx.tenant_id,
            )
        )
        if ws_probe.scalar_one_or_none() is not None:
            raise CrossWorkspaceForbidden(
                f"Run {run_id} belongs to a different workspace"
            )

        # Cross-tenant probe (§5.7). MUST use raw_admin_session so the
        # probe is not scoped to ctx.tenant_id and can see all tenants.
        async with raw_admin_session() as admin_s:
            tenant_probe = await admin_s.execute(
                select(RunORM.id).where(RunORM.id == run_id)
            )
            if tenant_probe.scalar_one_or_none() is not None:
                raise CrossTenantForbidden(
                    f"Run {run_id} belongs to a different tenant"
                )

        raise RunNotFound(f"Run {run_id} not found")

    async def list_for_tenant(
        self,
        ctx: TenantContext,
        *,
        session: AsyncSession | None = None,
    ) -> list[RunRecord]:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._list_inside_session(s, ctx)
        else:
            return await self._list_inside_session(session, ctx)

    async def _list_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
    ) -> list[RunRecord]:
        result = await s.execute(
            select(RunORM)
            .where(
                RunORM.tenant_id == ctx.tenant_id,
                RunORM.workspace_id == ctx.workspace_id,
            )
            .order_by(RunORM.created_at.desc(), RunORM.id.desc())
        )
        run_to_domain, _ = _mappers()
        return [run_to_domain(r) for r in result.scalars().all()]

    # ---- Internal-only — not part of public protocol ----------------------

    async def _update_status(
        self,
        ctx: TenantContext,
        run_id: str,
        new_status: RunStatus,
        *,
        session: AsyncSession | None = None,
    ) -> RunRecord:
        """Mutate run status. Internal-only — same contract as
        ``InMemoryRunRepository._update_status``. Sets ``started_at`` on
        first entry to RUNNING; sets ``completed_at`` on entry to any
        terminal state.

        Reuses ``_get_inside_session()`` for the cross-tenant/workspace
        probe semantics so AM-6 cross-workspace coverage exercises the
        same probe ladder as ``get()``.
        """
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._update_status_inside_session(
                    s, ctx, run_id, new_status
                )
        else:
            return await self._update_status_inside_session(
                session, ctx, run_id, new_status
            )

    async def _update_status_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
        new_status: RunStatus,
    ) -> RunRecord:
        primary = await s.execute(
            select(RunORM).where(
                RunORM.id == run_id,
                RunORM.tenant_id == ctx.tenant_id,
                RunORM.workspace_id == ctx.workspace_id,
            )
        )
        row = primary.scalar_one_or_none()
        if row is None:
            # Re-use the probe ladder for consistent error mapping. This
            # raises CrossWorkspaceForbidden / CrossTenantForbidden /
            # RunNotFound with the same semantics get() produces (AM-6
            # coverage).
            await self._get_inside_session(s, ctx, run_id)
            # Defensive fallback (should never reach here)
            raise RunNotFound(f"Run {run_id} not found")

        row.status = new_status.value
        now = utcnow()
        if new_status == RunStatus.RUNNING and row.started_at is None:
            row.started_at = now
        if new_status.is_terminal and row.completed_at is None:
            row.completed_at = now
        await s.flush()
        run_to_domain, _ = _mappers()
        return run_to_domain(row)
