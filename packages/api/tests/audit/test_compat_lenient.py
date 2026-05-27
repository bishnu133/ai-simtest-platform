"""Slice 7.7 Phase B — Unit tests for the lenient sibling helper.

Covers:
  * `to_tenant_audit_event_lenient` happy paths (valid UUIDs)
  * `_coerce_uuid_lenient` synth branch (non-UUID → uuid5)
  * Determinism (same input → same synthesized UUID)
  * Log shape (contains required fields)
  * Log payload guardrail (no metadata leakage) — v0.2 §17
  * Strict `to_tenant_audit_event` body byte-identity (regression-pin
    against sacred-surface anchor 94a027f) — v0.2 §6 test 12
"""
from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace
from uuid import UUID, uuid5

import pytest

from src.audit._compat import (
    _TEST_SENTINEL_NAMESPACE,
    _coerce_uuid_lenient,
    to_tenant_audit_event,
    to_tenant_audit_event_lenient,
)
from src.audit.context import TenantAuditEvent


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_VALID_TENANT_UUID = "00000000-0000-0000-0000-00000000000a"
_VALID_WORKSPACE_UUID = "00000000-0000-0000-0000-0000000000aa"
_VALID_CORRELATION_UUID = "00000000-0000-0000-0000-00000000ffff"


def _make_ctx(
    tenant_id: str = _VALID_TENANT_UUID,
    workspace_id: str | None = None,
    actor_id: str = "test-actor",
    actor_type: str = "service_account",
    correlation_id: str = _VALID_CORRELATION_UUID,
):
    """Construct a minimal context exposing only the attributes
    `to_tenant_audit_event_lenient` reads. Decouples tests from
    `TenantContext`'s exact constructor shape.
    """
    return SimpleNamespace(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=SimpleNamespace(actor_id=actor_id, actor_type=actor_type),
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Test 1: happy path — valid UUID tenant_id is preserved exactly
# ---------------------------------------------------------------------------

def test_lenient_with_valid_uuid_tenant_returns_same_uuid():
    ctx = _make_ctx(tenant_id=_VALID_TENANT_UUID)
    event = to_tenant_audit_event_lenient(
        ctx, action="test.action", resource_type="test_resource",
        resource_id="r1",
    )
    assert event.context.tenant_id == UUID(_VALID_TENANT_UUID)


# ---------------------------------------------------------------------------
# Test 2: happy path — valid UUID workspace_id is preserved exactly
# ---------------------------------------------------------------------------

def test_lenient_with_valid_uuid_workspace_returns_same_uuid():
    ctx = _make_ctx(
        tenant_id=_VALID_TENANT_UUID,
        workspace_id=_VALID_WORKSPACE_UUID,
    )
    event = to_tenant_audit_event_lenient(
        ctx, action="test.action", resource_type="test_resource",
        resource_id="r1",
    )
    assert event.context.workspace_id == UUID(_VALID_WORKSPACE_UUID)


# ---------------------------------------------------------------------------
# Test 3: workspace_id=None branch (matches strict's `if ctx.workspace_id else None`)
# ---------------------------------------------------------------------------

def test_lenient_with_none_workspace_returns_event_with_none_workspace():
    ctx = _make_ctx(tenant_id=_VALID_TENANT_UUID, workspace_id=None)
    event = to_tenant_audit_event_lenient(
        ctx, action="test.action", resource_type="test_resource",
        resource_id="r1",
    )
    assert event.context.workspace_id is None


# ---------------------------------------------------------------------------
# Test 4: workspace_id="" (falsy) — does NOT synth; matches strict's branch
# ---------------------------------------------------------------------------

def test_lenient_with_empty_string_workspace_returns_event_with_none_workspace():
    ctx = _make_ctx(tenant_id=_VALID_TENANT_UUID, workspace_id="")
    event = to_tenant_audit_event_lenient(
        ctx, action="test.action", resource_type="test_resource",
        resource_id="r1",
    )
    assert event.context.workspace_id is None


# ---------------------------------------------------------------------------
# Test 5: synth — non-UUID tenant_id is coerced via uuid5
# ---------------------------------------------------------------------------

def test_lenient_with_non_uuid_tenant_synthesizes_uuid5(caplog):
    ctx = _make_ctx(tenant_id="t_a")
    with caplog.at_level(logging.WARNING, logger="src.audit._compat"):
        event = to_tenant_audit_event_lenient(
            ctx, action="test.action", resource_type="test_resource",
            resource_id="r1",
        )
    expected_uuid = uuid5(_TEST_SENTINEL_NAMESPACE, "t_a")
    assert event.context.tenant_id == expected_uuid


# ---------------------------------------------------------------------------
# Test 6: synth — non-UUID workspace_id (tenant valid) is coerced via uuid5
# ---------------------------------------------------------------------------

def test_lenient_with_non_uuid_workspace_synthesizes_uuid5(caplog):
    ctx = _make_ctx(tenant_id=_VALID_TENANT_UUID, workspace_id="w_main")
    with caplog.at_level(logging.WARNING, logger="src.audit._compat"):
        event = to_tenant_audit_event_lenient(
            ctx, action="test.action", resource_type="test_resource",
            resource_id="r1",
        )
    expected_uuid = uuid5(_TEST_SENTINEL_NAMESPACE, "w_main")
    assert event.context.workspace_id == expected_uuid


# ---------------------------------------------------------------------------
# Test 7: synth — both tenant_id and workspace_id non-UUID in same call
# ---------------------------------------------------------------------------

def test_lenient_with_both_non_uuid_synthesizes_both(caplog):
    ctx = _make_ctx(tenant_id="t_a", workspace_id="w_main")
    with caplog.at_level(logging.WARNING, logger="src.audit._compat"):
        event = to_tenant_audit_event_lenient(
            ctx, action="test.action", resource_type="test_resource",
            resource_id="r1",
        )
    assert event.context.tenant_id == uuid5(_TEST_SENTINEL_NAMESPACE, "t_a")
    assert event.context.workspace_id == uuid5(_TEST_SENTINEL_NAMESPACE, "w_main")


# ---------------------------------------------------------------------------
# Test 8: determinism — "t_a" twice yields the same synthesized UUID
# ---------------------------------------------------------------------------

def test_lenient_synth_is_deterministic_across_calls(caplog):
    ctx1 = _make_ctx(tenant_id="t_a")
    ctx2 = _make_ctx(tenant_id="t_a")
    with caplog.at_level(logging.WARNING, logger="src.audit._compat"):
        event1 = to_tenant_audit_event_lenient(
            ctx1, action="a.1", resource_type="r", resource_id="r1",
        )
        event2 = to_tenant_audit_event_lenient(
            ctx2, action="a.2", resource_type="r", resource_id="r2",
        )
    assert event1.context.tenant_id == event2.context.tenant_id


# ---------------------------------------------------------------------------
# Test 9: log shape — WARNING contains field_name, raw value, synth UUID, action
# ---------------------------------------------------------------------------

def test_lenient_synth_logs_warning_with_field_and_action(caplog):
    ctx = _make_ctx(tenant_id="t_a")
    with caplog.at_level(logging.WARNING, logger="src.audit._compat"):
        to_tenant_audit_event_lenient(
            ctx, action="test.action.unique", resource_type="r",
            resource_id="r1",
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"Expected 1 WARNING, got {len(warnings)}"
    msg = warnings[0].getMessage()
    assert "tenant_id" in msg
    assert "'t_a'" in msg  # raw value via %r → single-quoted
    assert "test.action.unique" in msg
    expected_uuid = str(uuid5(_TEST_SENTINEL_NAMESPACE, "t_a"))
    assert expected_uuid in msg


# ---------------------------------------------------------------------------
# Test 10: logging guardrail (v0.2 §17) — log MUST NOT contain metadata payload
# ---------------------------------------------------------------------------

def test_lenient_synth_log_does_not_contain_metadata_payload(caplog):
    sensitive_marker = "DO_NOT_LOG_THIS_VALUE_X9X9X9X9"
    ctx = _make_ctx(tenant_id="t_a")
    with caplog.at_level(logging.WARNING, logger="src.audit._compat"):
        to_tenant_audit_event_lenient(
            ctx, action="test.action", resource_type="test_resource",
            resource_id="r1",
            metadata={"sensitive_key": sensitive_marker},
        )
    assert sensitive_marker not in caplog.text, (
        "Lenient helper WARNING log leaked metadata payload — "
        "logging guardrail violation (Slice 7.7 v0.2 §17)."
    )


# ---------------------------------------------------------------------------
# Test 11: return type sanity
# ---------------------------------------------------------------------------

def test_lenient_returns_tenant_audit_event_instance():
    ctx = _make_ctx(tenant_id=_VALID_TENANT_UUID)
    event = to_tenant_audit_event_lenient(
        ctx, action="test.action", resource_type="r", resource_id="r1",
    )
    assert isinstance(event, TenantAuditEvent)


# ---------------------------------------------------------------------------
# Test 12: REGRESSION-PIN — strict to_tenant_audit_event body byte-identical
# Sacred surface anchor: 94a027f. Slice 7.7 §5 additive-only contract.
# Snapshot captured from B.0.2 via `inspect.getsource()`.
# ---------------------------------------------------------------------------

_STRICT_HELPER_SOURCE_SNAPSHOT = 'def to_tenant_audit_event(\n    ctx: TenantContext,\n    action: str,\n    resource_type: str,\n    resource_id: str,\n    metadata: dict[str, Any] | None = None,\n) -> TenantAuditEvent:\n    """Build a Slice 1 TenantAuditEvent from a sync TenantContext.\n\n    Used at migrated Tier-1 tenant-path call sites:\n      * M8 (middleware.py)        — auth.accepted\n      * B1 (bootstrap.py)         — auth.membership_denied\n      * B2 (bootstrap.py)         — auth.bootstrap_created_tenant\n      * B3 (bootstrap.py)         — tenant.created\n\n    Coerces string IDs in TenantContext to UUID objects required by\n    AuditContext (Slice 1 sacred — domain uses UUID for tenant/workspace\n    identity).\n\n    Raises:\n        ValueError: if ctx.tenant_id or ctx.workspace_id is not a valid\n            UUID string. This indicates the caller has a malformed\n            context — fail loud rather than emit a corrupt audit row.\n    """\n    return TenantAuditEvent(\n        action=action,\n        context=AuditContext(\n            tenant_id=UUID(ctx.tenant_id),\n            actor_id=ctx.actor.actor_id,\n            actor_type=ctx.actor.actor_type,\n            workspace_id=UUID(ctx.workspace_id) if ctx.workspace_id else None,\n            correlation_id=ctx.correlation_id,\n        ),\n        resource_type=resource_type,\n        resource_id=resource_id,\n        details=dict(metadata) if metadata else {},\n    )\n'


def test_strict_to_tenant_audit_event_body_unchanged():
    actual = inspect.getsource(to_tenant_audit_event)
    assert actual == _STRICT_HELPER_SOURCE_SNAPSHOT, (
        f"Strict to_tenant_audit_event source has changed "
        f"(Slice 7.7 sacred surface, anchor 94a027f). "
        f"Expected {len(_STRICT_HELPER_SOURCE_SNAPSHOT)} chars, "
        f"got {len(actual)} chars."
    )
