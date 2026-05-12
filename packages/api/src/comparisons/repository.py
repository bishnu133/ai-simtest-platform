"""Comparison repository with locked sort order: created_at DESC, id DESC (v1.2.2 §S4)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import APIError, CrossTenantForbidden
from src.common.models import TenantContext
from src.common.write_context import WriteContext
from src.comparisons.models import ComparisonRecord
from src.db.models import Comparison as ComparisonORM
from src.db.session import raw_admin_session, tenant_scoped_session


def _mappers() -> tuple[object, object]:
    """Lazy import of comparison mapper functions to avoid circular imports."""
    from src.db.mappers.comparison import comparison_to_domain, comparison_to_orm
    return comparison_to_domain, comparison_to_orm


__all__ = [
    "ComparisonNotFound",
    "ComparisonRepository",
    "InMemoryComparisonRepository",
    "PostgresComparisonRepository",
]


class ComparisonNotFound(APIError):
    code = "comparison_not_found"
    http_status = 404


@runtime_checkable
class ComparisonRepository(Protocol):
    async def create(
        self,
        record: ComparisonRecord,
        *,
        write_ctx: WriteContext | None = None,
    ) -> ComparisonRecord: ...
    async def get(self, ctx: TenantContext, comparison_id: str) -> ComparisonRecord: ...
    async def list_for_tenant(self, ctx: TenantContext) -> list[ComparisonRecord]: ...


class InMemoryComparisonRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], ComparisonRecord] = {}

    def _key(self, tenant_id: str, workspace_id: str, comparison_id: str):
        return (tenant_id, workspace_id, comparison_id)

    async def create(
        self,
        record: ComparisonRecord,
        *,
        write_ctx: WriteContext | None = None,  # noqa: ARG002 — InMemory does not persist actors
    ) -> ComparisonRecord:
        # InMemory accepts write_ctx for Protocol parity but does not
        # persist the actor. Tests that need to verify actor persistence
        # must use PostgresComparisonRepository (which threads write_ctx
        # into the mapper via _upsert).
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


# ---------------------------------------------------------------------------
# Turn 2.6 Step 2 — PostgresComparisonRepository
# ---------------------------------------------------------------------------


class PostgresComparisonRepository:
    """SQLAlchemy 2.x async implementation of ``ComparisonRepository``.

    Mirrors ``PostgresRunRepository`` (Turn 2.5) discipline:
      * Read paths use ``tenant_scoped_session`` so RLS policies on the
        ``comparisons`` table apply automatically.
      * Workspace dual-filter (defense in depth on top of RLS) via explicit
        ``WHERE tenant_id=... AND workspace_id=...`` clauses.
      * §5.7 info-leak guards: cross-tenant probe → ``CrossTenantForbidden``.
        (Cross-workspace probe is intentionally NOT raised here — the
        v0.1 InMemoryComparisonRepository surface only distinguishes
        not-found from cross-tenant. Parity with that surface is required
        because ``ComparisonRepository`` Protocol is shared.)

    Session-kwarg contract (AM-3 + AM-4):
      Every public method accepts ``session: AsyncSession | None = None``.
        * ``session=None``  → repo opens its own ``tenant_scoped_session``.
                              The context manager owns the transaction.
        * ``session=...``   → caller has already scoped the session to
                              the appropriate tenant_id. Repo flushes only;
                              caller owns commit/rollback.

    create() UPSERT semantics (Turn 2.6 plan v0.2.1 §4 Step 2 + R-5):
      ``ComparisonService`` calls create() twice for a single comparison —
      once for PENDING state and once for the COMPLETED transition. We
      preserve the existing service contract by implementing create() as
      INSERT ... ON CONFLICT (id) DO UPDATE.

    T2.6-D1 RESOLVED in Turn 2.7 Drift 4 (plan v0.2.1 §2.4):
      Previously the mapper hardcoded ``initiated_by_actor_id="system"``
      because the Turn 2.6 ``ComparisonRepository.create()`` did not
      accept actor context. Turn 2.7 Drift 4 added ``write_ctx:
      WriteContext | None = None`` to the create() signature; the
      service builds ``WriteContext.from_tenant_context(ctx)`` and
      passes it through, so persisted rows now carry the real actor.
      ``write_ctx=None`` retains the system-actor fallback for back-compat.
    """

    async def create(
        self,
        record: ComparisonRecord,
        *,
        session: AsyncSession | None = None,
        write_ctx: WriteContext | None = None,
    ) -> ComparisonRecord:
        # RC-3 belt-and-braces consistency check
        if not record.tenant_id or not record.workspace_id:
            raise TypeError(
                "ComparisonRecord must carry non-empty tenant_id and "
                "workspace_id; ComparisonRepository.create() is not a "
                "self-context-providing surface."
            )
        if not record.id:
            raise TypeError("ComparisonRecord must carry non-empty id")

        if session is None:
            async with tenant_scoped_session(record.tenant_id) as s:
                await self._upsert(s, record, write_ctx=write_ctx)
                return record
        else:
            await self._upsert(session, record, write_ctx=write_ctx)
            return record

    async def _upsert(
        self,
        s: AsyncSession,
        record: ComparisonRecord,
        *,
        write_ctx: WriteContext | None = None,
    ) -> None:
        """UPSERT body. Service calls create() twice (PENDING then COMPLETED).

        Implementation: SQLAlchemy ``session.merge()`` produces the
        equivalent of INSERT ... ON CONFLICT (id) DO UPDATE for the full
        column set, with the row's primary key (id) as the conflict
        target. After flush, the row reflects the latest record state.

        Turn 2.7 Drift 4: ``write_ctx`` is forwarded to the mapper so
        ``initiated_by_actor_id`` reflects the real actor instead of
        the hardcoded ``"system"`` default. If ``write_ctx`` is None,
        the mapper falls back to ``WriteContext.system()`` — preserves
        back-compat for any caller not yet threaded.
        """
        _, comparison_to_orm = _mappers()
        orm_obj = comparison_to_orm(record, write_ctx=write_ctx)
        await s.merge(orm_obj)
        await s.flush()

    async def get(
        self,
        ctx: TenantContext,
        comparison_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> ComparisonRecord:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._get_inside_session(s, ctx, comparison_id)
        else:
            return await self._get_inside_session(session, ctx, comparison_id)

    async def _get_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        comparison_id: str,
    ) -> ComparisonRecord:
        """Body of ``get()`` factored out so session-or-caller branches don't
        duplicate the probe ladder. Mirrors PostgresRunRepository pattern."""
        primary = await s.execute(
            select(ComparisonORM).where(
                ComparisonORM.id == comparison_id,
                ComparisonORM.tenant_id == ctx.tenant_id,
                ComparisonORM.workspace_id == ctx.workspace_id,
            )
        )
        row = primary.scalar_one_or_none()
        if row is not None:
            comparison_to_domain, _ = _mappers()
            return comparison_to_domain(row)

        # Cross-tenant probe (§5.7). MUST use raw_admin_session so the
        # probe is not scoped to ctx.tenant_id and can see all tenants.
        async with raw_admin_session() as admin_s:
            tenant_probe = await admin_s.execute(
                select(ComparisonORM.id).where(ComparisonORM.id == comparison_id)
            )
            probe_row = tenant_probe.scalar_one_or_none()
            if probe_row is not None:
                # Row exists in some tenant. Distinguish:
                #   * Same tenant, different workspace → not raised here
                #     because the InMemory parity surface treats this as
                #     CrossTenant only. (See class docstring.)
                #   * Different tenant → CrossTenantForbidden.
                # Determine which case we hit:
                tenant_check = await admin_s.execute(
                    select(ComparisonORM.tenant_id).where(
                        ComparisonORM.id == comparison_id
                    )
                )
                row_tenant = tenant_check.scalar_one_or_none()
                if row_tenant is not None and str(row_tenant) != ctx.tenant_id:
                    raise CrossTenantForbidden(
                        f"Comparison {comparison_id} belongs to a different tenant"
                    )

        raise ComparisonNotFound(f"Comparison {comparison_id} not found")

    async def list_for_tenant(
        self,
        ctx: TenantContext,
        *,
        session: AsyncSession | None = None,
    ) -> list[ComparisonRecord]:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._list_inside_session(s, ctx)
        else:
            return await self._list_inside_session(session, ctx)

    async def _list_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
    ) -> list[ComparisonRecord]:
        # LOCKED sort order per v1.2.2 §S4: created_at DESC, id DESC
        result = await s.execute(
            select(ComparisonORM)
            .where(
                ComparisonORM.tenant_id == ctx.tenant_id,
                ComparisonORM.workspace_id == ctx.workspace_id,
            )
            .order_by(
                ComparisonORM.created_at.desc(),
                ComparisonORM.id.desc(),
            )
        )
        rows = result.scalars().all()
        comparison_to_domain, _ = _mappers()
        return [comparison_to_domain(r) for r in rows]

