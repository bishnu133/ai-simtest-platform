"""Slice 5: unit tests for AuditLogger async surface.

Tests cover (7 sacred names locked at Plan v0.2):
  1. test_aemit_tenant_event_returns_uuid_from_repository
  2. test_aemit_pretenant_event_returns_uuid_from_repository
  3. test_aemit_tenant_event_delegates_to_injected_repository
  4. test_aemit_pretenant_event_delegates_to_injected_repository
  5. test_aemit_tenant_event_propagates_repository_exceptions
  6. test_aemit_pretenant_event_propagates_audit_event_insert_rejected
  7. test_audit_logger_default_constructor_binds_inmemory_repository

No DB required. Uses a spy variant of AuditEventRepository for delegation
checks and a raising variant for failure-propagation checks (Q2=F1).

Sync write() is not tested here — Slice 5 introduces no sacred sync logger
test set (there was no prior tests/audit/test_logger.py to preserve). The
sync path remains exercised indirectly via tests/test_results_router.py.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.audit.context import (
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.audit.logger import AuditLogger
from src.audit.repository import (
    AuditEventInsertRejected,
    InMemoryAuditEventRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant_event() -> TenantAuditEvent:
    """Construct a minimal, valid TenantAuditEvent for delegation assertions."""
    ctx = AuditContext(
        tenant_id=uuid4(),
        actor_id="user_slice5_async",
        actor_type="human",
        workspace_id=uuid4(),
        correlation_id="cor_slice5_async_t",
    )
    return TenantAuditEvent(
        action="asset.created",
        context=ctx,
        resource_type="asset",
        resource_id="asset_slice5_unit",
        details={"slice": 5},
    )


def _make_pretenant_event() -> PretenantAuditEvent:
    """Construct a minimal, valid PretenantAuditEvent (auth.rejected only)."""
    return PretenantAuditEvent(
        action="auth.rejected",
        actor_id="anonymous_slice5",
        actor_type="system",
        details={"reason": "missing_credentials"},
        correlation_id="cor_slice5_async_pre",
    )


class _SpyRepository:
    """Test double recording calls and returning predictable UUIDs.

    Implements the AuditEventRepository Protocol structurally.
    """

    def __init__(self) -> None:
        self.tenant_calls: list[tuple[TenantAuditEvent, object]] = []
        self.pretenant_calls: list[tuple[PretenantAuditEvent, object]] = []
        self.next_tenant_uuid: UUID = uuid4()
        self.next_pretenant_uuid: UUID = uuid4()

    async def append_tenant_event(self, event, session=None):
        self.tenant_calls.append((event, session))
        return self.next_tenant_uuid

    async def append_pretenant_event(self, event, session=None):
        self.pretenant_calls.append((event, session))
        return self.next_pretenant_uuid


class _RaisingRepository:
    """Test double that always raises. Verifies Slice 5 Q2=F1 propagation."""

    def __init__(self, tenant_exc: Exception, pretenant_exc: Exception) -> None:
        self._tenant_exc = tenant_exc
        self._pretenant_exc = pretenant_exc

    async def append_tenant_event(self, event, session=None):
        raise self._tenant_exc

    async def append_pretenant_event(self, event, session=None):
        raise self._pretenant_exc


# ---------------------------------------------------------------------------
# 7 sacred tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aemit_tenant_event_returns_uuid_from_repository():
    """Q3=R1: aemit_tenant_event returns the UUID the repository returned."""
    spy = _SpyRepository()
    audit_logger_under_test = AuditLogger(repository=spy)

    result = await audit_logger_under_test.aemit_tenant_event(_make_tenant_event())

    assert isinstance(result, UUID)
    assert result == spy.next_tenant_uuid


@pytest.mark.asyncio
async def test_aemit_pretenant_event_returns_uuid_from_repository():
    """Q3=R1: aemit_pretenant_event returns the UUID the repository returned."""
    spy = _SpyRepository()
    audit_logger_under_test = AuditLogger(repository=spy)

    result = await audit_logger_under_test.aemit_pretenant_event(_make_pretenant_event())

    assert isinstance(result, UUID)
    assert result == spy.next_pretenant_uuid


@pytest.mark.asyncio
async def test_aemit_tenant_event_delegates_to_injected_repository():
    """Q1=B1: aemit_tenant_event invokes append_tenant_event on the injected
    repo with the same event and session, and does not mutate the sync
    in-memory _events list."""
    spy = _SpyRepository()
    audit_logger_under_test = AuditLogger(repository=spy)
    event = _make_tenant_event()

    await audit_logger_under_test.aemit_tenant_event(event, session=None)

    assert len(spy.tenant_calls) == 1
    delegated_event, delegated_session = spy.tenant_calls[0]
    assert delegated_event is event
    assert delegated_session is None
    # Sync path untouched by async call.
    assert audit_logger_under_test._events == []


@pytest.mark.asyncio
async def test_aemit_pretenant_event_delegates_to_injected_repository():
    """Q1=B1: aemit_pretenant_event invokes append_pretenant_event on the
    injected repo and does not mutate the sync in-memory _events list."""
    spy = _SpyRepository()
    audit_logger_under_test = AuditLogger(repository=spy)
    event = _make_pretenant_event()

    await audit_logger_under_test.aemit_pretenant_event(event, session=None)

    assert len(spy.pretenant_calls) == 1
    delegated_event, delegated_session = spy.pretenant_calls[0]
    assert delegated_event is event
    assert delegated_session is None
    assert audit_logger_under_test._events == []


@pytest.mark.asyncio
async def test_aemit_tenant_event_propagates_repository_exceptions():
    """Q2=F1: a generic exception from append_tenant_event propagates."""
    boom = RuntimeError("simulated DB connection failure")
    audit_logger_under_test = AuditLogger(
        repository=_RaisingRepository(tenant_exc=boom, pretenant_exc=boom),
    )

    with pytest.raises(RuntimeError, match="simulated DB connection failure"):
        await audit_logger_under_test.aemit_tenant_event(_make_tenant_event())


@pytest.mark.asyncio
async def test_aemit_pretenant_event_propagates_audit_event_insert_rejected():
    """Q2=F1: AuditEventInsertRejected from append_pretenant_event propagates
    with the original message intact."""
    rejected = AuditEventInsertRejected("action not in PRETENANT_ACTION_ALLOWLIST")
    audit_logger_under_test = AuditLogger(
        repository=_RaisingRepository(
            tenant_exc=RuntimeError("unused"),
            pretenant_exc=rejected,
        ),
    )

    with pytest.raises(AuditEventInsertRejected, match="PRETENANT_ACTION_ALLOWLIST"):
        await audit_logger_under_test.aemit_pretenant_event(_make_pretenant_event())


def test_audit_logger_default_constructor_binds_inmemory_repository():
    """Q1=B1 + Q4=N2: AuditLogger() with no args binds an
    InMemoryAuditEventRepository.

    This contract keeps the module-level singleton
        audit_logger = AuditLogger()
    backward-compatible while enabling Slice 6 to inject Postgres later.
    """
    audit_logger_under_test = AuditLogger()

    assert isinstance(
        audit_logger_under_test._audit_repository,
        InMemoryAuditEventRepository,
    )
