"""PostgresIdempotencyRepository tests — Turn 2.6 Step 3 (4 tests).

Composition (Turn 2.6 plan v0.2.1 §4 Step 3):
  1. lookup returns None for absent key
  2. remember + lookup round-trip
  3. lookup returns None for expired entry (lazy expiry)
  4. workspace-scoped lookup (same key in two workspaces returns distinct entries)
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.models import ActorRef, TenantContext, utcnow
from src.comparisons.idempotency import (
    IdempotencyEntry,
    PostgresIdempotencyRepository,
)


def _ctx(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=ActorRef(actor_id="test-user", actor_type="human"),
    )


async def _seed_tenant_workspace(
    admin_session: AsyncSession, slug: str
) -> tuple[str, str]:
    t_result = await admin_session.execute(
        text(
            "INSERT INTO tenants (name, slug) "
            "VALUES (:name, :slug) RETURNING id"
        ),
        {"name": f"Tenant {slug}", "slug": slug},
    )
    tenant_id = str(t_result.scalar_one())
    w_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'default', true) RETURNING id"
        ),
        {"tid": tenant_id},
    )
    workspace_id = str(w_result.scalar_one())
    await admin_session.commit()
    return tenant_id, workspace_id


# ---------------------------------------------------------------------------
# Test 1 — lookup returns None for absent key
# ---------------------------------------------------------------------------


async def test_postgres_idempotency_lookup_returns_none_for_absent_key(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-idem-1")
    repo = PostgresIdempotencyRepository()

    result = await repo.lookup(_ctx(tid, wid), "absent-key-12345")
    assert result is None


# ---------------------------------------------------------------------------
# Test 2 — remember + lookup round-trip
# ---------------------------------------------------------------------------


async def test_postgres_idempotency_remember_then_lookup_round_trips(
    admin_session: AsyncSession,
) -> None:
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-idem-2")
    repo = PostgresIdempotencyRepository()
    ctx = _ctx(tid, wid)

    body_hash = "deadbeef" * 8  # 64-char SHA-256-shaped hex
    comparison_id = "11112222-3333-4444-5555-666677778888"

    await repo.remember(ctx, "round-trip-key", body_hash, comparison_id)

    fetched = await repo.lookup(ctx, "round-trip-key")
    assert fetched is not None
    assert isinstance(fetched, IdempotencyEntry)
    assert fetched.body_hash == body_hash
    assert fetched.comparison_id == comparison_id
    # expires_at is approximately now + TTL (24h)
    assert fetched.expires_at > utcnow()


# ---------------------------------------------------------------------------
# Test 3 — lookup returns None for expired entry (lazy expiry)
# ---------------------------------------------------------------------------


async def test_postgres_idempotency_lookup_returns_none_for_expired_entry(
    admin_session: AsyncSession,
) -> None:
    """Force expires_at into the past via direct SQL, then prove lookup
    treats the row as a miss (lazy expiry).
    """
    tid, wid = await _seed_tenant_workspace(admin_session, "t26-idem-3")
    repo = PostgresIdempotencyRepository()
    ctx = _ctx(tid, wid)

    body_hash = "cafebabe" * 8
    comparison_id = "aaaa1111-2222-3333-4444-555566667777"
    await repo.remember(ctx, "expiring-key", body_hash, comparison_id)

    # Rewrite expires_at to 1 hour ago via admin session (bypasses RLS)
    await admin_session.execute(
        text(
            "UPDATE idempotency_keys "
            "SET expires_at = now() - interval '1 hour' "
            "WHERE tenant_id = :tid AND workspace_id = :wid "
            "AND idempotency_key = :key"
        ),
        {"tid": tid, "wid": wid, "key": "expiring-key"},
    )
    await admin_session.commit()

    result = await repo.lookup(ctx, "expiring-key")
    assert result is None, (
        "Expired entry must be treated as a miss (lazy expiry); the "
        "lookup must not raise and must not return the stale entry."
    )


# ---------------------------------------------------------------------------
# Test 4 — workspace-scoped lookup (same key in two workspaces of same tenant)
# ---------------------------------------------------------------------------


async def test_postgres_idempotency_workspace_scoped_lookup(
    admin_session: AsyncSession,
) -> None:
    """Same idempotency_key reused in two workspaces of the same tenant
    must yield two distinct entries — proves the migration 0003 unique
    constraint is workspace-aware.
    """
    tid, w1 = await _seed_tenant_workspace(admin_session, "t26-idem-4")
    # Add a second workspace
    w2_result = await admin_session.execute(
        text(
            "INSERT INTO workspaces (tenant_id, name, is_default) "
            "VALUES (:tid, 'second', false) RETURNING id"
        ),
        {"tid": tid},
    )
    w2 = str(w2_result.scalar_one())
    await admin_session.commit()

    repo = PostgresIdempotencyRepository()
    key = "shared-idempotency-key"
    cid_w1 = "12345678-1111-1111-1111-aaaaaaaaaaaa"
    cid_w2 = "87654321-2222-2222-2222-bbbbbbbbbbbb"

    await repo.remember(_ctx(tid, w1), key, "hash-w1" + "0" * 56, cid_w1)
    await repo.remember(_ctx(tid, w2), key, "hash-w2" + "0" * 56, cid_w2)

    e1 = await repo.lookup(_ctx(tid, w1), key)
    e2 = await repo.lookup(_ctx(tid, w2), key)

    assert e1 is not None and e2 is not None
    assert e1.comparison_id == cid_w1
    assert e2.comparison_id == cid_w2
    assert e1.comparison_id != e2.comparison_id, (
        "Two workspaces of the same tenant must store independent "
        "idempotency entries under the same key (Migration 0003 unique "
        "constraint includes workspace_id)."
    )
