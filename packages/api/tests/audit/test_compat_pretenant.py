"""Slice 7.8 B.2: unit tests for to_pretenant_audit_event resource_type /
resource_id kwargs (F-20 helper side).

Covers the additive signature extension landed in B.1 (commit 27e57a6):
  * default kwargs preserve back-compat (dataclass defaults flow through)
  * explicit resource_type / resource_id kwargs surface as top-level fields
  * metadata still flows to details independently of the new kwargs
  * empty resource_type / resource_id are rejected by the
    PretenantAuditEvent __post_init__ validator (delegated)

Pure-domain tests — no DB, no async, no logger import.
"""
from __future__ import annotations

import pytest

from src.audit._compat import to_pretenant_audit_event
from src.audit.context import (
    PRETENANT_DEFAULT_RESOURCE_ID,
    PRETENANT_DEFAULT_RESOURCE_TYPE,
    PretenantAuditEvent,
)


def test_to_pretenant_audit_event_default_kwargs_preserves_backcompat() -> None:
    """Without resource_type/resource_id kwargs, the event carries the
    PretenantAuditEvent dataclass defaults (back-compat for unmigrated
    call sites)."""
    event = to_pretenant_audit_event(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
    )
    assert isinstance(event, PretenantAuditEvent)
    assert event.resource_type == PRETENANT_DEFAULT_RESOURCE_TYPE
    assert event.resource_id == PRETENANT_DEFAULT_RESOURCE_ID


def test_to_pretenant_audit_event_with_resource_type_kwarg() -> None:
    """An explicit resource_type kwarg surfaces as the top-level field."""
    event = to_pretenant_audit_event(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
        resource_type="tenant",
    )
    assert event.resource_type == "tenant"
    assert event.resource_id == PRETENANT_DEFAULT_RESOURCE_ID


def test_to_pretenant_audit_event_with_resource_id_kwarg() -> None:
    """An explicit resource_id kwarg surfaces as the top-level field."""
    event = to_pretenant_audit_event(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
        resource_id="org_123",
    )
    assert event.resource_id == "org_123"
    assert event.resource_type == PRETENANT_DEFAULT_RESOURCE_TYPE


def test_to_pretenant_audit_event_with_both_resource_kwargs() -> None:
    """Both kwargs flow through to top-level fields simultaneously."""
    event = to_pretenant_audit_event(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
        resource_type="workspace",
        resource_id="ws_456",
    )
    assert event.resource_type == "workspace"
    assert event.resource_id == "ws_456"


def test_to_pretenant_audit_event_metadata_still_flows_to_details() -> None:
    """metadata is independent of the new kwargs — it still lands in
    details, and the new kwargs do not leak into details."""
    event = to_pretenant_audit_event(
        action="auth.rejected",
        actor_id="anonymous",
        actor_type="human",
        metadata={"step": "extract_bearer", "reason": "missing_bearer_token"},
        resource_type="tenant",
        resource_id="org_123",
    )
    assert event.details == {
        "step": "extract_bearer",
        "reason": "missing_bearer_token",
    }
    assert "resource_type" not in event.details
    assert "resource_id" not in event.details
    assert event.resource_type == "tenant"
    assert event.resource_id == "org_123"


def test_to_pretenant_audit_event_empty_resource_type_raises_value_error() -> None:
    """An empty resource_type is rejected by the PretenantAuditEvent
    __post_init__ validator (delegated; helper does not pre-validate)."""
    with pytest.raises(ValueError):
        to_pretenant_audit_event(
            action="auth.rejected",
            actor_id="anonymous",
            actor_type="human",
            resource_type="",
        )


def test_to_pretenant_audit_event_empty_resource_id_raises_value_error() -> None:
    """An empty resource_id is rejected by the PretenantAuditEvent
    __post_init__ validator (delegated; helper does not pre-validate)."""
    with pytest.raises(ValueError):
        to_pretenant_audit_event(
            action="auth.rejected",
            actor_id="anonymous",
            actor_type="human",
            resource_id="",
        )
