"""Slice 5: unit tests for AuditLogger async surface.

Tests cover (9 sacred names; 7 from Slice 5 + 2 from FH-S7.5 B.6.2):
  1. test_aemit_tenant_event_returns_uuid_from_repository
  2. test_aemit_pretenant_event_returns_uuid_from_repository
  3. test_aemit_tenant_event_delegates_to_injected_repository
  4. test_aemit_pretenant_event_delegates_to_injected_repository
  5. test_aemit_tenant_event_propagates_repository_exceptions
  6. test_aemit_pretenant_event_propagates_audit_event_insert_rejected
  7. test_audit_logger_default_constructor_binds_inmemory_repository
  8. test_aemit_pretenant_event_safe_accepts_auth_tenant_state_invalid
  9. test_aemit_pretenant_event_safe_still_rejects_non_allowlist_action

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

# ============================================================================
# FH-S7.5 widening — companion tests for aemit_pretenant_event_safe
# carrying the new auth.tenant_state_invalid action (plan v0.2.1 §9.2.2).
# Covers the fail-open Slice 7 variant used by middleware M3/M4/M6 (B.5).
# ============================================================================


@pytest.mark.asyncio
async def test_aemit_pretenant_event_safe_accepts_auth_tenant_state_invalid():
    """FH-S7.5 §5 + §7.3: aemit_pretenant_event_safe accepts a
    PretenantAuditEvent carrying the newly-allowed action
    'auth.tenant_state_invalid' and delegates to the injected repository.

    End-to-end positive companion to
    test_pretenant_audit_event_accepts_auth_tenant_state_invalid
    (test_context.py) — that test covers dataclass construction;
    this test covers the full async bridge through the Slice 7
    fail-open variant used by middleware M6 (B.5)."""
    spy = _SpyRepository()
    audit_logger_under_test = AuditLogger(repository=spy)
    event = PretenantAuditEvent(
        action="auth.tenant_state_invalid",
        actor_id="anonymous_fh_s75",
        actor_type="system",
        details={
            "step": "bootstrap",
            "reason": "unknown_org_id_self_serve_disabled",
        },
        correlation_id="cor_fh_s75_tsi",
    )

    result = await audit_logger_under_test.aemit_pretenant_event_safe(event)

    # Returns UUID (not None) because underlying aemit_pretenant_event
    # succeeded — fail-open only kicks in on exception.
    assert isinstance(result, UUID)
    assert result == spy.next_pretenant_uuid
    # Spy captured exactly one call with the TSI event verbatim.
    assert len(spy.pretenant_calls) == 1
    captured_event, captured_session = spy.pretenant_calls[0]
    assert captured_event is event
    assert captured_event.action == "auth.tenant_state_invalid"
    assert captured_session is None


@pytest.mark.asyncio
async def test_aemit_pretenant_event_safe_still_rejects_non_allowlist_action():
    """FH-S7.5 regression: aemit_pretenant_event_safe's fail-open
    try/except CANNOT swallow PretenantAuditEvent __post_init__
    ValueError, because __post_init__ runs at argument evaluation
    BEFORE the safe variant is invoked.

    Critical safety property: the Slice 7 fail-open design only
    suppresses repository-level (DB) failures — never caller-bug
    construction errors. A future refactor that moved event
    construction INSIDE the safe variant (wrapping it in the same
    try/except) would silently lose this regression test.

    Target action: 'auth.accepted'. After B.2 widened the pretenant
    allowlist to {'auth.rejected', 'auth.tenant_state_invalid'},
    'auth.accepted' remains outside — confirming the widening did
    not accidentally widen too far."""
    audit_logger_under_test = AuditLogger(repository=_SpyRepository())

    with pytest.raises(ValueError, match="allowlist"):
        # PretenantAuditEvent(action="auth.accepted") raises ValueError
        # at __post_init__ — argument evaluation order means the safe
        # variant is never entered. The fail-open try/except therefore
        # has no chance to swallow this.
        await audit_logger_under_test.aemit_pretenant_event_safe(
            PretenantAuditEvent(action="auth.accepted")
        )
