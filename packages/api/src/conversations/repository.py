"""Conversation summary repository (Turn 2.6 Step 4 + Step 5).

Extracts the `_summaries` dict ownership out of ``ConversationService`` into
a dedicated repository so a Postgres-backed implementation can land in
Step 5 without changing the service's surface.

The Protocol intentionally does NOT include ``_reset()`` — that is an
InMemory-only test helper. ``ConversationService._reset()`` uses a
``getattr`` capability check (Turn 2.6 plan v0.2.1 §6.3 / R-7) so the
Protocol stays clean and the service fails loudly if something tries to
reset a Postgres-backed repo.

Storage adapter (R2 / Local) for transcripts is unchanged. This module
only repo-fies the SUMMARY store. The service still owns the lifecycle
of the transcript bytes and continues to call ``self._storage.put / get
/ delete`` directly.
"""
from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import CrossTenantForbidden, CrossWorkspaceForbidden
from src.common.models import TenantContext
from src.conversations.models import ConversationSummary, ConversationVerdict
from src.db.models import ConversationSummary as ConversationSummaryORM
from src.db.session import raw_admin_session, tenant_scoped_session


__all__ = [
    "ConversationSummaryRepository",
    "InMemoryConversationSummaryRepository",
    "PostgresConversationSummaryRepository",
]


def _mappers() -> tuple[object, object]:
    """Lazy import of conversation_summary mapper functions."""
    from src.db.mappers.conversation_summary import (
        conversation_summary_to_domain,
        conversation_summary_to_orm,
    )
    return conversation_summary_to_domain, conversation_summary_to_orm
    """Lazy import of conversation_summary mapper functions."""
    from src.db.mappers.conversation_summary import (
        conversation_summary_to_domain,
        conversation_summary_to_orm,
    )
    return conversation_summary_to_domain, conversation_summary_to_orm


@runtime_checkable
class ConversationSummaryRepository(Protocol):
    """Async store for conversation summaries.

    The repo carries metadata about each conversation (verdict, judge
    scores, transcript_ref pointer); it does NOT carry the transcript
    body. Transcripts live in object storage (R2/Local) under keys the
    service constructs from ctx + run_id + conversation_id.

    Session-kwarg contract (AM-3 + AM-4):
      Every method accepts ``session: AsyncSession | None = None``.
        * ``session=None``  → impl opens its own session (Postgres) or
                              ignores the parameter (InMemory).
        * ``session=...``   → caller owns the transaction. Repo flushes
                              only.
    """

    async def upsert(
        self,
        summary: ConversationSummary,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Insert or replace a summary keyed by (tenant_id, workspace_id, id)."""
        ...

    async def get(
        self,
        ctx: TenantContext,
        conversation_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> ConversationSummary:
        """Return the summary or raise ``ConversationNotFound``.

        Postgres impl additionally raises ``CrossTenantForbidden`` /
        ``CrossWorkspaceForbidden`` when a row exists in a different
        scope (mirrors PostgresRunRepository pattern). InMemory impl
        treats any miss as a not-found.
        """
        ...

    async def list(
        self,
        ctx: TenantContext,
        *,
        run_id: str | None = None,
        verdict: ConversationVerdict | None = None,
        limit: int = 50,
        session: AsyncSession | None = None,
    ) -> list[ConversationSummary]:
        """List summaries scoped to ``(ctx.tenant_id, ctx.workspace_id)``,
        with optional ``run_id`` and ``verdict`` filters. Sort: created_at DESC.
        """
        ...

    async def delete_for_run(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[ConversationSummary]:
        """Delete every summary scoped to ``(ctx, run_id)`` and return the
        deleted records. The caller (service) iterates the returned list to
        fan out object-storage deletes.
        """
        ...


# ---------------------------------------------------------------------------
# InMemory implementation (extracted from ConversationService)
# ---------------------------------------------------------------------------


class InMemoryConversationSummaryRepository:
    """In-memory summary store. Owns the same dict + lock that lived inside
    ``ConversationService`` pre-Turn-2.6.
    """

    def __init__(self) -> None:
        # Key: (tenant_id, workspace_id, conversation_id)
        self._summaries: dict[tuple[str, str, str], ConversationSummary] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _summary_key(
        tenant_id: str, workspace_id: str, conversation_id: str
    ) -> tuple[str, str, str]:
        return (tenant_id, workspace_id, conversation_id)

    async def upsert(
        self,
        summary: ConversationSummary,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 - Protocol parity
    ) -> None:
        k = self._summary_key(
            summary.tenant_id, summary.workspace_id, summary.id
        )
        async with self._lock:
            self._summaries[k] = summary

    async def get(
        self,
        ctx: TenantContext,
        conversation_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 - Protocol parity
    ) -> ConversationSummary:
        # Lazy import to avoid circular dependency with conversations.service
        from src.conversations.service import ConversationNotFound

        k = self._summary_key(ctx.tenant_id, ctx.workspace_id, conversation_id)
        summary = self._summaries.get(k)
        if summary is None:
            raise ConversationNotFound(
                f"Conversation {conversation_id} not found in workspace"
            )
        return summary

    async def list(
        self,
        ctx: TenantContext,
        *,
        run_id: str | None = None,
        verdict: ConversationVerdict | None = None,
        limit: int = 50,
        session: AsyncSession | None = None,  # noqa: ARG002 - Protocol parity
    ) -> list[ConversationSummary]:
        results: list[ConversationSummary] = []
        for (t, w, _cid), summary in self._summaries.items():
            if t != ctx.tenant_id or w != ctx.workspace_id:
                continue
            if run_id and summary.run_id != run_id:
                continue
            if verdict and summary.verdict != verdict:
                continue
            results.append(summary)
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results[:limit]

    async def delete_for_run(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 - Protocol parity
    ) -> list[ConversationSummary]:
        to_delete: list[tuple[tuple[str, str, str], ConversationSummary]] = []
        for k, summary in self._summaries.items():
            t, w, _ = k
            if (
                t == ctx.tenant_id
                and w == ctx.workspace_id
                and summary.run_id == run_id
            ):
                to_delete.append((k, summary))

        async with self._lock:
            deleted_summaries: list[ConversationSummary] = []
            for k, summary in to_delete:
                self._summaries.pop(k, None)
                deleted_summaries.append(summary)
        return deleted_summaries

    # ------------------------------------------------------------------
    # Test-only helper (NOT on the Protocol; getattr-checked by service).
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """Wipe the summary store. Test-only — InMemory impl only.

        Postgres impl will NOT define this method; the service uses a
        getattr capability check (Turn 2.6 plan v0.2.1 §6.3 / R-7) to
        fail loudly if reset is attempted against a production repo.
        """
        self._summaries.clear()


# ---------------------------------------------------------------------------
# Postgres implementation (Turn 2.6 Step 5)
# ---------------------------------------------------------------------------


class PostgresConversationSummaryRepository:
    """SQLAlchemy 2.x async implementation of ``ConversationSummaryRepository``.

    Mirrors PostgresRunRepository / PostgresComparisonRepository discipline:
      * Read paths use ``tenant_scoped_session`` for RLS.
      * Workspace dual-filter on every read/write (defense in depth).
      * §5.7 info-leak guards: cross-tenant -> CrossTenantForbidden;
        cross-workspace-within-tenant -> CrossWorkspaceForbidden
        (D-Cwf pattern from Turn 2.5).

    Session-kwarg contract (AM-3 + AM-4): every method accepts
    ``session: AsyncSession | None = None``.

    Transcript storage NOT touched here. The repo only persists the
    SUMMARY metadata; transcripts live in object storage (R2/Local) and
    are managed by ConversationService.
    """

    async def upsert(
        self,
        summary: ConversationSummary,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        # RC-3 belt-and-braces consistency check
        if not summary.tenant_id or not summary.workspace_id:
            raise TypeError(
                "ConversationSummary must carry non-empty tenant_id and "
                "workspace_id; ConversationSummaryRepository.upsert() is "
                "not a self-context-providing surface."
            )
        if not summary.id:
            raise TypeError("ConversationSummary must carry non-empty id")

        if session is None:
            async with tenant_scoped_session(summary.tenant_id) as s:
                await self._upsert_inside_session(s, summary)
        else:
            await self._upsert_inside_session(session, summary)

    async def _upsert_inside_session(
        self, s: AsyncSession, summary: ConversationSummary
    ) -> None:
        """UPSERT body. SQLAlchemy ``session.merge()`` produces the
        equivalent of INSERT ... ON CONFLICT (id) DO UPDATE for the full
        column set, with the row's primary key (id) as the conflict
        target."""
        _, conversation_summary_to_orm = _mappers()
        orm_obj = conversation_summary_to_orm(summary)
        await s.merge(orm_obj)
        await s.flush()

    async def get(
        self,
        ctx: TenantContext,
        conversation_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> ConversationSummary:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._get_inside_session(s, ctx, conversation_id)
        else:
            return await self._get_inside_session(session, ctx, conversation_id)

    async def _get_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        conversation_id: str,
    ) -> ConversationSummary:
        """Body of ``get()`` factored out so session-or-caller branches
        don't duplicate the probe ladder. Mirrors PostgresRunRepository."""
        # Lazy import for the not-found error class
        from src.conversations.service import ConversationNotFound

        primary = await s.execute(
            select(ConversationSummaryORM).where(
                ConversationSummaryORM.id == conversation_id,
                ConversationSummaryORM.tenant_id == ctx.tenant_id,
                ConversationSummaryORM.workspace_id == ctx.workspace_id,
            )
        )
        row = primary.scalar_one_or_none()
        if row is not None:
            conversation_summary_to_domain, _ = _mappers()
            return conversation_summary_to_domain(row)

        # Cross-workspace probe (D-Cwf). Same tenant context, safe to
        # reuse `s` whether owned or caller-supplied.
        ws_probe = await s.execute(
            select(ConversationSummaryORM.id).where(
                ConversationSummaryORM.id == conversation_id,
                ConversationSummaryORM.tenant_id == ctx.tenant_id,
            )
        )
        if ws_probe.scalar_one_or_none() is not None:
            raise CrossWorkspaceForbidden(
                f"Conversation {conversation_id} belongs to a different workspace"
            )

        # Cross-tenant probe (§5.7). Use raw_admin_session.
        async with raw_admin_session() as admin_s:
            tenant_probe = await admin_s.execute(
                select(ConversationSummaryORM.id).where(
                    ConversationSummaryORM.id == conversation_id
                )
            )
            if tenant_probe.scalar_one_or_none() is not None:
                raise CrossTenantForbidden(
                    f"Conversation {conversation_id} belongs to a different tenant"
                )

        raise ConversationNotFound(
            f"Conversation {conversation_id} not found in workspace"
        )

    async def list(
        self,
        ctx: TenantContext,
        *,
        run_id: str | None = None,
        verdict: ConversationVerdict | None = None,
        limit: int = 50,
        session: AsyncSession | None = None,
    ) -> list[ConversationSummary]:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._list_inside_session(
                    s, ctx, run_id=run_id, verdict=verdict, limit=limit
                )
        else:
            return await self._list_inside_session(
                session, ctx, run_id=run_id, verdict=verdict, limit=limit
            )

    async def _list_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        *,
        run_id: str | None,
        verdict: ConversationVerdict | None,
        limit: int,
    ) -> list[ConversationSummary]:
        stmt = select(ConversationSummaryORM).where(
            ConversationSummaryORM.tenant_id == ctx.tenant_id,
            ConversationSummaryORM.workspace_id == ctx.workspace_id,
        )
        if run_id is not None:
            stmt = stmt.where(ConversationSummaryORM.run_id == run_id)
        if verdict is not None:
            stmt = stmt.where(ConversationSummaryORM.verdict == verdict)
        stmt = stmt.order_by(ConversationSummaryORM.created_at.desc()).limit(limit)

        result = await s.execute(stmt)
        rows = result.scalars().all()
        conversation_summary_to_domain, _ = _mappers()
        return [conversation_summary_to_domain(r) for r in rows]

    async def delete_for_run(
        self,
        ctx: TenantContext,
        run_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[ConversationSummary]:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._delete_inside_session(s, ctx, run_id)
        else:
            return await self._delete_inside_session(session, ctx, run_id)

    async def _delete_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        run_id: str,
    ) -> list[ConversationSummary]:
        # First fetch the summaries we're about to delete (so the caller
        # can fan out object-storage cleanup), then delete them in the
        # same session so visibility is transactional.
        result = await s.execute(
            select(ConversationSummaryORM).where(
                ConversationSummaryORM.tenant_id == ctx.tenant_id,
                ConversationSummaryORM.workspace_id == ctx.workspace_id,
                ConversationSummaryORM.run_id == run_id,
            )
        )
        rows = result.scalars().all()
        conversation_summary_to_domain, _ = _mappers()
        deleted_summaries = [conversation_summary_to_domain(r) for r in rows]

        if not deleted_summaries:
            return []

        await s.execute(
            delete(ConversationSummaryORM).where(
                ConversationSummaryORM.tenant_id == ctx.tenant_id,
                ConversationSummaryORM.workspace_id == ctx.workspace_id,
                ConversationSummaryORM.run_id == run_id,
            )
        )
        await s.flush()
        return deleted_summaries
