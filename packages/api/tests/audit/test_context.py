"""Tests for ``src/audit/context.py`` — FH-Tier-1 Slice 1.

29 sacred tests organized by concern:

    AuditContext (7):
      - construction with required fields
      - rejects invalid actor_type
      - rejects empty actor_id
      - rejects string tenant_id (must be UUID instance)
      - rejects string workspace_id (must be UUID instance when set)
      - with_correlation returns new instance (frozen-safe)
      - frozen-dataclass immutability

    PretenantAuditEvent (14):
      - construction with defaults
      - rejects non-allowlist action
      - rejects invalid actor_type
      - rejects empty action / actor_id / resource_type / resource_id (4)
      - rejects non-dict details (TypeError)
      - frozen-dataclass immutability
      - to_function_args has 9 keys
      - to_function_args defaults match function signature
      - to_function_args serializes details as JSON string
      - to_function_args handles UUID/datetime in details via default=str
      - to_function_args propagates full custom values

    TenantAuditEvent (5):
      - construction with context
      - rejects pretenant-only action
      - rejects empty action
      - rejects non-dict details (TypeError)
      - frozen-dataclass immutability

    Module-level invariants (3):
      - PRETENANT_ACTION_ALLOWLIST shape
      - ACTOR_TYPE_ALLOWLIST shape
      - pure-domain import invariant (AST-based)

All test function names are sacred from Slice 1 close onward.
Renames or removals require a versioned plan amendment.

Plan reference: ``fh_tier1_plan_v0_3_4.md`` §5; slice1_phase_b_gate.md §4.
"""
from __future__ import annotations

import ast
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from src.audit.context import (
    ACTOR_TYPE_ALLOWLIST,
    PRETENANT_ACTION_ALLOWLIST,
    PRETENANT_DEFAULT_ACTOR_ID,
    PRETENANT_DEFAULT_ACTOR_TYPE,
    PRETENANT_DEFAULT_RESOURCE_ID,
    PRETENANT_DEFAULT_RESOURCE_TYPE,
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)


# Sample valid tenant / workspace UUIDs for tests (deterministic for diagnostics).
_VALID_TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_VALID_WORKSPACE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _sample_audit_context() -> AuditContext:
    """Small helper — used by TenantAuditEvent tests that need a context."""
    return AuditContext(
        tenant_id=_VALID_TENANT_ID,
        actor_id="user_123",
        actor_type="human",
    )


# ============================================================================
# AuditContext (7 tests)
# ============================================================================

def test_audit_context_construction_with_required_fields():
    """AuditContext can be built with the minimum required fields."""
    ctx = AuditContext(
        tenant_id=_VALID_TENANT_ID,
        actor_id="user_123",
        actor_type="human",
    )
    assert ctx.tenant_id == _VALID_TENANT_ID
    assert ctx.actor_id == "user_123"
    assert ctx.actor_type == "human"
    # Optional fields default to None.
    assert ctx.workspace_id is None
    assert ctx.actor_display is None
    assert ctx.correlation_id is None
    assert ctx.ip_address is None
    assert ctx.user_agent is None


def test_audit_context_rejects_invalid_actor_type():
    """actor_type must be in ACTOR_TYPE_ALLOWLIST."""
    with pytest.raises(ValueError, match="actor_type"):
        AuditContext(
            tenant_id=_VALID_TENANT_ID,
            actor_id="user_123",
            actor_type="robot",  # type: ignore[arg-type]
        )


def test_audit_context_rejects_empty_actor_id():
    """actor_id must be a non-empty string."""
    with pytest.raises(ValueError, match="actor_id"):
        AuditContext(
            tenant_id=_VALID_TENANT_ID,
            actor_id="",
            actor_type="human",
        )


def test_audit_context_rejects_string_tenant_id():
    """tenant_id must be a UUID instance — not a string.

    Plan v0.3.4 §5 ground rule: do not auto-convert string tenant IDs.
    Conversion is the responsibility of the API/provider boundary.
    """
    with pytest.raises(TypeError, match="tenant_id"):
        AuditContext(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",  # type: ignore[arg-type]
            actor_id="user_123",
            actor_type="human",
        )


def test_audit_context_rejects_string_workspace_id():
    """workspace_id, when set, must be a UUID instance — not a string."""
    with pytest.raises(TypeError, match="workspace_id"):
        AuditContext(
            tenant_id=_VALID_TENANT_ID,
            actor_id="user_123",
            actor_type="human",
            workspace_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",  # type: ignore[arg-type]
        )


def test_audit_context_with_correlation_returns_new_instance():
    """with_correlation produces a copy; frozen-dataclass-safe."""
    original = AuditContext(
        tenant_id=_VALID_TENANT_ID,
        actor_id="user_123",
        actor_type="human",
    )
    updated = original.with_correlation("req-abc-123")

    assert updated is not original
    assert updated.correlation_id == "req-abc-123"
    # Original is unchanged.
    assert original.correlation_id is None
    # All other fields are preserved on the copy.
    assert updated.tenant_id == original.tenant_id
    assert updated.actor_id == original.actor_id
    assert updated.actor_type == original.actor_type


def test_audit_context_is_frozen_dataclass():
    """AuditContext instances are immutable (frozen=True)."""
    ctx = AuditContext(
        tenant_id=_VALID_TENANT_ID,
        actor_id="user_123",
        actor_type="human",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.actor_id = "user_456"  # type: ignore[misc]


# ============================================================================
# PretenantAuditEvent (14 tests)
# ============================================================================

def test_pretenant_audit_event_construction_defaults():
    """PretenantAuditEvent() with no args uses the sentinel defaults that
    match the audit_pretenant_insert function parameter defaults."""
    event = PretenantAuditEvent()

    assert event.action == "auth.rejected"
    assert event.actor_id == PRETENANT_DEFAULT_ACTOR_ID
    assert event.actor_type == PRETENANT_DEFAULT_ACTOR_TYPE
    assert event.resource_type == PRETENANT_DEFAULT_RESOURCE_TYPE
    assert event.resource_id == PRETENANT_DEFAULT_RESOURCE_ID
    assert event.correlation_id is None
    assert event.details == {}
    assert event.ip_address is None
    assert event.user_agent is None


def test_pretenant_audit_event_rejects_non_allowlist_action():
    """action must be in PRETENANT_ACTION_ALLOWLIST (currently only
    'auth.rejected')."""
    with pytest.raises(ValueError, match="allowlist"):
        PretenantAuditEvent(action="auth.accepted")  # type: ignore[arg-type]


def test_pretenant_audit_event_rejects_invalid_actor_type():
    """actor_type must be in ACTOR_TYPE_ALLOWLIST."""
    with pytest.raises(ValueError, match="actor_type"):
        PretenantAuditEvent(actor_type="robot")  # type: ignore[arg-type]


def test_pretenant_audit_event_rejects_empty_action():
    """Empty-string action is rejected (defense in depth — allowlist also
    rejects, but the empty check fires first with a clearer message)."""
    with pytest.raises(ValueError, match="action"):
        PretenantAuditEvent(action="")  # type: ignore[arg-type]


def test_pretenant_audit_event_rejects_empty_actor_id():
    """actor_id must be non-empty."""
    with pytest.raises(ValueError, match="actor_id"):
        PretenantAuditEvent(actor_id="")


def test_pretenant_audit_event_rejects_empty_resource_type():
    """resource_type must be non-empty."""
    with pytest.raises(ValueError, match="resource_type"):
        PretenantAuditEvent(resource_type="")


def test_pretenant_audit_event_rejects_empty_resource_id():
    """resource_id must be non-empty."""
    with pytest.raises(ValueError, match="resource_id"):
        PretenantAuditEvent(resource_id="")


def test_pretenant_audit_event_rejects_non_dict_details():
    """details must be a dict — rejects list, str, None, etc.

    Audit details must be a JSON object; non-dict types could silently
    round-trip through json.dumps and land in the jsonb column as
    something other than an object.
    """
    with pytest.raises(TypeError, match="details"):
        PretenantAuditEvent(details=["not", "a", "dict"])  # type: ignore[arg-type]


def test_pretenant_audit_event_is_frozen_dataclass():
    """PretenantAuditEvent instances are immutable."""
    event = PretenantAuditEvent()
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.actor_id = "someone_else"  # type: ignore[misc]


def test_pretenant_audit_event_to_function_args_has_nine_keys():
    """to_function_args returns a dict with exactly 9 keys matching the
    audit_pretenant_insert function parameters (v0.3.4 §5)."""
    args = PretenantAuditEvent().to_function_args()
    expected_keys = {
        "action",
        "actor_id",
        "actor_type",
        "resource_type",
        "resource_id",
        "correlation_id",
        "details",
        "ip_address",
        "user_agent",
    }
    assert set(args.keys()) == expected_keys
    assert len(args) == 9


def test_pretenant_audit_event_to_function_args_defaults_match_function_signature():
    """Default-constructed PretenantAuditEvent maps to the same values the
    function would receive when all positional args are omitted."""
    args = PretenantAuditEvent().to_function_args()

    assert args["action"] == "auth.rejected"
    assert args["actor_id"] == "anonymous"
    assert args["actor_type"] == "system"
    assert args["resource_type"] == "auth"
    assert args["resource_id"] == "session"
    assert args["correlation_id"] is None
    assert args["details"] == "{}"  # json.dumps({}, default=str)
    assert args["ip_address"] is None
    assert args["user_agent"] is None


def test_pretenant_audit_event_to_function_args_serializes_details_as_json_string():
    """details dict is serialized to a JSON string for jsonb binding."""
    event = PretenantAuditEvent(
        details={"reason": "invalid_signature", "attempt_count": 3}
    )
    args = event.to_function_args()

    # details is a JSON string, not a dict.
    assert isinstance(args["details"], str)
    # Parseable back to the original dict.
    assert json.loads(args["details"]) == {
        "reason": "invalid_signature",
        "attempt_count": 3,
    }


def test_pretenant_audit_event_to_function_args_handles_uuid_in_details_via_default_str():
    """details with non-JSON-native values (UUID, datetime) serialize via
    default=str rather than raising TypeError. This matters because audit
    details often include request IDs (UUID) and timestamps."""
    sample_uuid = UUID("11111111-1111-1111-1111-111111111111")
    sample_ts = datetime(2026, 5, 16, 18, 30, 0, tzinfo=timezone.utc)

    event = PretenantAuditEvent(
        details={"request_id": sample_uuid, "timestamp": sample_ts}
    )

    # Must not raise.
    args = event.to_function_args()
    parsed = json.loads(args["details"])

    # default=str coerces UUID and datetime to their str() representation.
    assert parsed["request_id"] == str(sample_uuid)
    assert parsed["timestamp"] == str(sample_ts)


def test_pretenant_audit_event_to_function_args_full_custom_values():
    """All 9 fields can be overridden and propagate to to_function_args."""
    event = PretenantAuditEvent(
        action="auth.rejected",
        actor_id="user_abc",
        actor_type="human",
        resource_type="login",
        resource_id="session_xyz",
        correlation_id="req-456",
        details={"k": "v"},
        ip_address="192.0.2.1",
        user_agent="Mozilla/5.0",
    )
    args = event.to_function_args()

    assert args["action"] == "auth.rejected"
    assert args["actor_id"] == "user_abc"
    assert args["actor_type"] == "human"
    assert args["resource_type"] == "login"
    assert args["resource_id"] == "session_xyz"
    assert args["correlation_id"] == "req-456"
    assert json.loads(args["details"]) == {"k": "v"}
    assert args["ip_address"] == "192.0.2.1"
    assert args["user_agent"] == "Mozilla/5.0"


# ============================================================================
# TenantAuditEvent (5 tests)
# ============================================================================

def test_tenant_audit_event_construction_with_context():
    """TenantAuditEvent requires action + context; resource fields optional."""
    ctx = _sample_audit_context()
    event = TenantAuditEvent(action="run.created", context=ctx)

    assert event.action == "run.created"
    assert event.context is ctx
    assert event.resource_type is None
    assert event.resource_id is None
    assert event.details == {}
    assert event.asset_versions is None


def test_tenant_audit_event_rejects_pretenant_action():
    """Pretenant-only actions cannot be used for TenantAuditEvent —
    use PretenantAuditEvent instead."""
    ctx = _sample_audit_context()
    with pytest.raises(ValueError, match="pretenant"):
        TenantAuditEvent(action="auth.rejected", context=ctx)


def test_tenant_audit_event_rejects_empty_action():
    """action must be non-empty."""
    ctx = _sample_audit_context()
    with pytest.raises(ValueError, match="action"):
        TenantAuditEvent(action="", context=ctx)


def test_tenant_audit_event_rejects_non_dict_details():
    """details must be a dict — rejects str, list, etc.

    Symmetric with PretenantAuditEvent: audit details must be a JSON
    object, not any other JSON value.
    """
    ctx = _sample_audit_context()
    with pytest.raises(TypeError, match="details"):
        TenantAuditEvent(
            action="run.created",
            context=ctx,
            details="bogus_string",  # type: ignore[arg-type]
        )


def test_tenant_audit_event_is_frozen_dataclass():
    """TenantAuditEvent instances are immutable."""
    event = TenantAuditEvent(
        action="run.created",
        context=_sample_audit_context(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.action = "run.deleted"  # type: ignore[misc]


# ============================================================================
# Module-level invariants (3 tests)
# ============================================================================

def test_pretenant_action_allowlist_is_frozenset_with_auth_rejected():
    """The pretenant action allowlist is exactly {'auth.rejected'} as a
    frozenset. Widening requires migration + function + CHECK constraint
    update (lockstep across 4 layers per v0.3.4 §5)."""
    assert isinstance(PRETENANT_ACTION_ALLOWLIST, frozenset)
    assert PRETENANT_ACTION_ALLOWLIST == frozenset({"auth.rejected"})


def test_actor_type_allowlist_contains_exactly_four_values():
    """ACTOR_TYPE_ALLOWLIST has the 4 expected actor types. Matches the
    audit_events table _ACTOR_TYPE_CHECK constraint."""
    assert isinstance(ACTOR_TYPE_ALLOWLIST, frozenset)
    assert ACTOR_TYPE_ALLOWLIST == frozenset(
        {"human", "service_account", "system", "support"}
    )


def test_audit_context_module_has_no_infrastructure_imports():
    """Pure-domain invariant (test-enforced): src/audit/context.py imports
    ONLY from the Python standard library.

    No DB, ORM, repository, app_factory, or third-party non-stdlib
    packages are allowed. Future-1 convergence with WriteContext will
    require deliberately updating this allowlist as part of a coordinated
    plan change.

    Allowed top-level modules: __future__, dataclasses, typing, uuid, json.
    """
    allowed_top_levels = frozenset({
        "__future__",
        "dataclasses",
        "typing",
        "uuid",
        "json",
    })

    context_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "audit"
        / "context.py"
    )
    assert context_path.exists(), (
        f"src/audit/context.py not found at {context_path} — "
        f"is this test running from the expected location?"
    )

    tree = ast.parse(context_path.read_text())

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level not in allowed_top_levels:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top_level = mod.split(".")[0]
            if top_level not in allowed_top_levels:
                violations.append(f"from {mod} import ...")

    assert not violations, (
        f"src/audit/context.py has disallowed imports: {violations}. "
        f"Pure-domain rule allows only: {sorted(allowed_top_levels)}. "
        f"See module docstring for the rationale."
    )
