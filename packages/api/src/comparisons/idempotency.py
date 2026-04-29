"""Idempotency store for POST /v1/comparisons (v1.2.2 §11.3).

Turn 2.6 Step 3 changes:
  * ``IdempotencyEntry`` (public dataclass) replaces the v0.1 private
    ``_Entry``. Uses a real ``datetime`` for ``expires_at`` (was
    ``time.monotonic()`` float) so the in-memory and Postgres impls
    agree on the time representation.
  * ``IdempotencyStore`` Protocol declared (``runtime_checkable``).
    ``ComparisonService`` now types against this Protocol per Turn 2.6
    plan v0.2.1 MF-1.
  * ``InMemoryIdempotencyStore.lookup``/``remember`` become ``async``
    and accept ``ctx: TenantContext`` instead of a bare ``workspace_id:
    str``. Storage key broadens to
    ``(ctx.tenant_id, ctx.workspace_id, idempotency_key)`` to align with
    the Postgres unique constraint
    ``(tenant_id, workspace_id, idempotency_key)``.
  * ``PostgresIdempotencyRepository`` is the Postgres-backed impl.
    Migrates the impedance between the slim domain shape (body_hash +
    comparison_id) and the richer ORM shape (request_hash +
    response_payload JSONB + status_code).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.models import TenantContext, utcnow
from src.db.models import IdempotencyKey as IdempotencyKeyORM
from src.db.session import raw_admin_session, tenant_scoped_session

IDEMPOTENCY_TTL_SECONDS = 86400  # 24h


def canonical_hash(workspace_id: str, left_run_id: str, right_run_id: str) -> str:
    """Stable hash of the request body fields. Per v1.2.2 §11.3."""
    payload = json.dumps(
        {
            "workspace_id": workspace_id,
            "left_run_id": left_run_id,
            "right_run_id": right_run_id,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class IdempotencyEntry:
    """Public, frozen view of a stored idempotency record.

    Replaces the v0.1 private ``_Entry`` dataclass. ``expires_at`` is a
    real ``datetime`` (was ``time.monotonic()`` float in v0.1) so both
    the in-memory and Postgres impls share a comparable time
    representation.
    """

    body_hash: str
    comparison_id: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Protocol (Turn 2.6 plan v0.2.1 §4 Step 3 / MF-1)
# ---------------------------------------------------------------------------


@runtime_checkable
class IdempotencyStore(Protocol):
    """Async idempotency store. Both InMemory and Postgres impls satisfy this.

    API contract:
      * ``lookup(ctx, key)`` returns the entry if present and unexpired,
        ``None`` otherwise. Expired entries may be deleted opportunistically.
      * ``remember(ctx, key, body_hash, comparison_id)`` upserts an
        entry with TTL ``IDEMPOTENCY_TTL_SECONDS`` from now.
      * Storage key for both impls is ``(ctx.tenant_id, ctx.workspace_id,
        idempotency_key)``. Two workspaces in the same tenant may use the
        same idempotency_key independently.
      * ``session: AsyncSession | None = None`` (AM-3 + AM-4 universal
        kwarg). When ``None``, the impl opens its own session;
        when supplied, the caller owns the transaction.
    """

    async def lookup(
        self,
        ctx: TenantContext,
        idempotency_key: str,
        *,
        session: AsyncSession | None = None,
    ) -> IdempotencyEntry | None: ...

    async def remember(
        self,
        ctx: TenantContext,
        idempotency_key: str,
        body_hash: str,
        comparison_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation (Turn 2.6: now async, ctx-keyed)
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore:
    """In-memory idempotency store. Async API per Turn 2.6 v0.2.1 Step 3.

    Storage key is ``(tenant_id, workspace_id, idempotency_key)``. The
    broader 3-tuple is additive: v0.1 was ``(workspace_id,
    idempotency_key)`` and the service has always supplied ctx-derived
    workspace_id, so existing behaviour is unchanged for any legitimate
    caller.
    """

    def __init__(self, ttl_seconds: int = IDEMPOTENCY_TTL_SECONDS):
        self._store: dict[tuple[str, str, str], IdempotencyEntry] = {}
        self._ttl = ttl_seconds

    def _key(self, tenant_id: str, workspace_id: str, idempotency_key: str):
        return (tenant_id, workspace_id, idempotency_key)

    async def lookup(
        self,
        ctx: TenantContext,
        idempotency_key: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 - accepted for Protocol parity
    ) -> IdempotencyEntry | None:
        k = self._key(ctx.tenant_id, ctx.workspace_id, idempotency_key)
        entry = self._store.get(k)
        if entry is None:
            return None
        if utcnow() >= entry.expires_at:
            del self._store[k]
            return None
        return entry

    async def remember(
        self,
        ctx: TenantContext,
        idempotency_key: str,
        body_hash: str,
        comparison_id: str,
        *,
        session: AsyncSession | None = None,  # noqa: ARG002 - accepted for Protocol parity
    ) -> None:
        k = self._key(ctx.tenant_id, ctx.workspace_id, idempotency_key)
        self._store[k] = IdempotencyEntry(
            body_hash=body_hash,
            comparison_id=comparison_id,
            expires_at=utcnow() + timedelta(seconds=self._ttl),
        )


# ---------------------------------------------------------------------------
# Postgres implementation (Turn 2.6 Step 3)
# ---------------------------------------------------------------------------


# Canonical status_code we stamp on idempotency rows. The
# ``idempotency_keys`` table was originally designed for a richer
# HTTP-cache use case (the schema carries response_payload JSONB and
# status_code). Turn 2.6 uses a slim subset: store the comparison_id
# inside response_payload and stamp 201 (the comparison-created HTTP
# status). Future use cases (full HTTP body cache) can layer on top
# without a migration.
_DEFAULT_STATUS_CODE = 201


class PostgresIdempotencyRepository:
    """Postgres-backed idempotency store.

    Schema mapping (impedance between domain and ORM):
      * domain ``body_hash``       -> ORM ``request_hash`` (column type
        ``String(128)``; SHA-256 hex is 64 chars).
      * domain ``comparison_id``   -> ``response_payload['comparison_id']``
        (JSONB; the ``response_payload`` column was provisioned for a
        richer HTTP cache use case but Turn 2.6 only stashes the
        comparison_id). ``status_code`` is hardcoded to 201.
      * domain ``expires_at``      -> ORM ``expires_at`` (TIMESTAMPTZ).
      * Composite key for lookup:  ``(tenant_id, workspace_id,
        idempotency_key)``. Migration 0003 added the workspace_id
        column and the new unique index.

    Session-kwarg contract (AM-3 + AM-4):
      Mirrors PostgresRunRepository / PostgresComparisonRepository.
    """

    def __init__(self, ttl_seconds: int = IDEMPOTENCY_TTL_SECONDS):
        self._ttl = ttl_seconds

    async def lookup(
        self,
        ctx: TenantContext,
        idempotency_key: str,
        *,
        session: AsyncSession | None = None,
    ) -> IdempotencyEntry | None:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                return await self._lookup_inside_session(s, ctx, idempotency_key)
        else:
            return await self._lookup_inside_session(session, ctx, idempotency_key)

    async def _lookup_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        idempotency_key: str,
    ) -> IdempotencyEntry | None:
        result = await s.execute(
            select(IdempotencyKeyORM).where(
                IdempotencyKeyORM.tenant_id == ctx.tenant_id,
                IdempotencyKeyORM.workspace_id == ctx.workspace_id,
                IdempotencyKeyORM.idempotency_key == idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        # Lazy-expiry check (do NOT raise on expired rows; treat as miss
        # and opportunistically delete in a separate admin session to
        # avoid contaminating the tenant-scoped read txn).
        if utcnow() >= row.expires_at:
            row_id = str(row.id)
            try:
                async with raw_admin_session() as cleanup:
                    await cleanup.execute(
                        delete(IdempotencyKeyORM).where(
                            IdempotencyKeyORM.id == row_id,
                            IdempotencyKeyORM.expires_at <= utcnow(),
                        )
                    )
                    await cleanup.commit()
            except Exception:
                # Cleanup is best-effort; never raise from a lookup miss.
                pass
            return None

        comparison_id = (row.response_payload or {}).get("comparison_id", "")
        return IdempotencyEntry(
            body_hash=row.request_hash,
            comparison_id=comparison_id,
            expires_at=row.expires_at,
        )

    async def remember(
        self,
        ctx: TenantContext,
        idempotency_key: str,
        body_hash: str,
        comparison_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        if session is None:
            async with tenant_scoped_session(ctx.tenant_id) as s:
                await self._remember_inside_session(
                    s, ctx, idempotency_key, body_hash, comparison_id
                )
        else:
            await self._remember_inside_session(
                session, ctx, idempotency_key, body_hash, comparison_id
            )

    async def _remember_inside_session(
        self,
        s: AsyncSession,
        ctx: TenantContext,
        idempotency_key: str,
        body_hash: str,
        comparison_id: str,
    ) -> None:
        # UPSERT semantic on (tenant_id, workspace_id, idempotency_key):
        # if an entry exists, refresh body_hash + comparison_id +
        # expires_at. Matches the in-memory store's last-write-wins
        # behaviour. SELECT-then-UPDATE-or-INSERT inside the
        # tenant-scoped session.
        result = await s.execute(
            select(IdempotencyKeyORM).where(
                IdempotencyKeyORM.tenant_id == ctx.tenant_id,
                IdempotencyKeyORM.workspace_id == ctx.workspace_id,
                IdempotencyKeyORM.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        new_expires_at = utcnow() + timedelta(seconds=self._ttl)
        if existing is not None:
            existing.request_hash = body_hash
            existing.response_payload = {"comparison_id": comparison_id}
            existing.status_code = _DEFAULT_STATUS_CODE
            existing.expires_at = new_expires_at
            await s.flush()
            return

        s.add(
            IdempotencyKeyORM(
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                idempotency_key=idempotency_key,
                request_hash=body_hash,
                response_payload={"comparison_id": comparison_id},
                status_code=_DEFAULT_STATUS_CODE,
                expires_at=new_expires_at,
            )
        )
        await s.flush()
