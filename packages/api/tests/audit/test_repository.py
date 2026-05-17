"""Unit tests for :mod:`src.audit.repository` — Protocol + InMemory.

Slice 4 of FH-Tier-1. No database required.

Covers:
  * :class:`AuditEventRepository` Protocol shape (``runtime_checkable``)
  * :class:`InMemoryAuditEventRepository` contract for use in dev
    composition and test fixtures
  * Independence of pretenant vs tenant event storage

Postgres integration tests live in
``tests/db/test_postgres_audit_event_repository.py``.

Test function names are sacred from Slice 4 close forward (FH-Tier-1
v0.3.4 §8); renames or removals require a versioned plan amendment.
"""
from __future__ import annotations

import uuid

from src.audit.context import (
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.audit.repository import (
    AuditEventRepository,
    InMemoryAuditEventRepository,
    PostgresAuditEventRepository,
)


# ---------------------------------------------------------------------------
# Test fixtures (lightweight constructors)
# ---------------------------------------------------------------------------


_VALID_TENANT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_VALID_WORKSPACE_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _sample_audit_context() -> AuditContext:
    """Return a valid AuditContext suitable for TenantAuditEvent construction."""
    return AuditContext(
        tenant_id=_VALID_TENANT_ID,
        actor_id="user_test_123",
        actor_type="human",
        workspace_id=_VALID_WORKSPACE_ID,
    )


def _sample_tenant_event() -> TenantAuditEvent:
    """Return a valid TenantAuditEvent for in-memory repository tests."""
    return TenantAuditEvent(
        action="run.created",
        context=_sample_audit_context(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_audit_event_repository_protocol_is_runtime_checkable():
    """``AuditEventRepository`` is decorated ``@runtime_checkable``.

    isinstance() must succeed against both implementations. The
    ``PostgresAuditEventRepository`` instantiation does NOT touch the
    database — it's a stateless class, so this test runs without
    Postgres being available.
    """
    in_memory = InMemoryAuditEventRepository()
    postgres = PostgresAuditEventRepository()

    assert isinstance(in_memory, AuditEventRepository)
    assert isinstance(postgres, AuditEventRepository)


def test_inmemory_audit_event_repository_implements_protocol():
    """``InMemoryAuditEventRepository`` declares both Protocol methods.

    Belt-and-braces over the runtime_checkable test: explicitly verifies
    the public method surface using ``hasattr``. Catches accidental
    method-rename regressions even if runtime_checkable's structural
    matching is loose about signatures.
    """
    repo = InMemoryAuditEventRepository()
    assert isinstance(repo, AuditEventRepository)
    assert hasattr(repo, "append_pretenant_event")
    assert hasattr(repo, "append_tenant_event")


async def test_inmemory_append_pretenant_event_returns_uuid():
    """``InMemory.append_pretenant_event`` returns a freshly-allocated UUID.

    Uses a default-constructed ``PretenantAuditEvent`` (the dataclass
    provides all defaults for an ``auth.rejected`` event). The repository
    must return a UUID; the type check is the sacred assertion.
    """
    repo = InMemoryAuditEventRepository()
    event = PretenantAuditEvent()
    new_id = await repo.append_pretenant_event(event)

    assert isinstance(new_id, uuid.UUID)


async def test_inmemory_append_tenant_event_returns_uuid():
    """``InMemory.append_tenant_event`` returns a freshly-allocated UUID."""
    repo = InMemoryAuditEventRepository()
    event = _sample_tenant_event()
    new_id = await repo.append_tenant_event(event)

    assert isinstance(new_id, uuid.UUID)


async def test_inmemory_stores_pretenant_and_tenant_events_independently():
    """Pretenant and tenant events are kept in separate internal dicts.

    The InMemory implementation must not cross-pollinate the two event
    types — type-based dispatch in downstream consumers (e.g., a future
    audit query API) depends on the separation being clean.

    Accessing ``_pretenant`` and ``_tenant`` directly is an intentional
    private-attr test: the public Protocol does not expose lookups in
    Slice 4 (read methods are out of scope per gate §1), so we verify
    storage separation via implementation internals. When read methods
    are added in a future slice, this test should be rewritten to use
    the public surface.
    """
    repo = InMemoryAuditEventRepository()

    pre_event = PretenantAuditEvent()
    ten_event = _sample_tenant_event()

    pre_id = await repo.append_pretenant_event(pre_event)
    ten_id = await repo.append_tenant_event(ten_event)

    # Different UUIDs (basic sanity)
    assert pre_id != ten_id

    # Verified separation: each ID appears in exactly one storage dict
    assert pre_id in repo._pretenant
    assert pre_id not in repo._tenant
    assert ten_id in repo._tenant
    assert ten_id not in repo._pretenant
