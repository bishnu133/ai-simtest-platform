"""Slice 7.8 B.3: regression tests for the F-26 query_all_events pretenant
transformation fix (the narrow L1 exception in src/audit/logger.py).

Before F-26, query_all_events coerced every PretenantAuditEvent to an
AuditEvent with hardcoded resource_type="" / resource_id="", dropping the
top-level fields PretenantAuditEvent actually carries. F-26 maps
pretenant_event.resource_type / .resource_id through to the AuditEvent
shape returned by the test-only read path.

Tests:
  1. pretenant resource_type now surfaces via query_all_events
  2. pretenant resource_id now surfaces via query_all_events
  3. tenant-path mapping unchanged (regression-pin for the tenant
     for-loop block, which F-26 did NOT touch)

Each test uses a fresh AuditLogger bound to a fresh
InMemoryAuditEventRepository to avoid module-singleton state leakage.
"""
from __future__ import annotations

import uuid

from src.audit.context import (
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.audit.logger import AuditLogger
from src.audit.repository import InMemoryAuditEventRepository


def _fresh_logger() -> AuditLogger:
    """A logger bound to an isolated in-memory repository (no singleton)."""
    return AuditLogger(repository=InMemoryAuditEventRepository())


async def test_query_all_events_preserves_pretenant_resource_type() -> None:
    """F-26: a PretenantAuditEvent's top-level resource_type surfaces on
    the AuditEvent returned by query_all_events (no longer hardcoded "")."""
    logger = _fresh_logger()
    event = PretenantAuditEvent(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
        resource_type="tenant",
        resource_id="org_123",
    )
    await logger.aemit_pretenant_event_safe(event)

    results = logger.query_all_events()
    assert len(results) == 1
    assert results[0].resource_type == "tenant"


async def test_query_all_events_preserves_pretenant_resource_id() -> None:
    """F-26: a PretenantAuditEvent's top-level resource_id surfaces on
    the AuditEvent returned by query_all_events (no longer hardcoded "")."""
    logger = _fresh_logger()
    event = PretenantAuditEvent(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
        resource_type="tenant",
        resource_id="org_123",
    )
    await logger.aemit_pretenant_event_safe(event)

    results = logger.query_all_events()
    assert len(results) == 1
    assert results[0].resource_id == "org_123"


async def test_query_all_events_tenant_path_unchanged_behaviorally() -> None:
    """Regression-pin: F-26 touched ONLY the pretenant for-loop. The
    tenant for-loop's field mapping (resource_type / resource_id /
    tenant_id) must be unchanged."""
    logger = _fresh_logger()
    tenant_id = uuid.uuid4()
    event = TenantAuditEvent(
        action="auth.accepted",
        context=AuditContext(
            tenant_id=tenant_id,
            actor_id="user_1",
            actor_type="human",
        ),
        resource_type="run",
        resource_id="run_001",
    )
    await logger.aemit_tenant_event_safe(event)

    results = logger.query_all_events()
    assert len(results) == 1
    assert results[0].resource_type == "run"
    assert results[0].resource_id == "run_001"
    assert results[0].tenant_id == str(tenant_id)
