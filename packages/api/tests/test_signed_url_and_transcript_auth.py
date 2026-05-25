"""Week 6a Turn 3: signed URL + transcript auth security tests (6 tests).

Distinct from Turn 2's conversation router tests — this suite isolates
security-review surface for enterprise validation.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_tenant_context
from src.api.errors import APIError, api_error_handler
from src.audit.logger import AuditActions, audit_logger
from src.common.models import ActorRef, TenantContext
from src.conversations.models import ConversationTranscript, Turn
from src.conversations.router import _get_conversation_service, router as conv_router
from src.conversations.service import ConversationService
from src.storage.local import LocalFilesystemAdapter


def _ctx(tenant: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant, workspace_id="w_main",
        actor=ActorRef(actor_id=f"u_{tenant}", actor_type="human"),
    )


@pytest_asyncio.fixture
async def setup(tmp_path):
    audit_logger.clear_all()
    storage = LocalFilesystemAdapter(root=str(tmp_path / "s"), bucket="b", signing_secret="x")
    svc = ConversationService(storage)
    ctx_a = _ctx("t_a")
    transcript = ConversationTranscript(
        conversation_id="conv_x", run_id="run_1", persona_id="p1",
        turns=[Turn(turn_number=1, role="user", content="hi")],
    )
    summary = await svc.store_conversation(
        ctx=ctx_a, run_id="run_1", persona_id="p1", persona_name="P1",
        transcript=transcript, verdict="pass",
    )
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(conv_router, prefix="/v1")
    app.dependency_overrides[_get_conversation_service] = lambda: svc
    app.state._test_ctx = ctx_a
    app.dependency_overrides[get_tenant_context] = lambda: app.state._test_ctx
    return app, TestClient(app), summary.id


def test_ownership_pre_check_before_signed_url(setup):
    app, client, cid = setup
    # Switch to tenant_b — pre-check must reject before any signing
    app.state._test_ctx = _ctx("t_b")
    r = client.get(f"/v1/conversations/{cid}/download-url")
    assert r.status_code == 404
    # Audit MUST NOT contain a download_url_issued event for this attempt
    events = audit_logger.query_all_events(action=AuditActions.CONVERSATION_DOWNLOAD_URL_ISSUED)
    assert len(events) == 0


def test_signed_url_audit_event_payload(setup):
    _, client, cid = setup
    audit_logger.clear_all()
    r = client.get(f"/v1/conversations/{cid}/download-url")
    assert r.status_code == 200
    events = audit_logger.query_all_events(action=AuditActions.CONVERSATION_DOWNLOAD_URL_ISSUED)
    assert len(events) == 1
    assert events[0].resource_type == "conversation"
    assert events[0].resource_id == cid
    assert events[0].tenant_id == "t_a"


def test_transcript_view_audit_event_payload(setup):
    _, client, cid = setup
    audit_logger.clear_all()
    client.get(f"/v1/conversations/{cid}/transcript")
    events = audit_logger.query_all_events(action=AuditActions.CONVERSATION_TRANSCRIPT_VIEWED)
    assert len(events) == 1
    assert events[0].resource_id == cid


def test_signed_url_response_has_expires_at(setup):
    _, client, cid = setup
    body = client.get(f"/v1/conversations/{cid}/download-url").json()
    assert "expires_at" in body
    assert body["expires_in_seconds"] == 300


def test_transcript_cross_tenant_attack_via_known_id(setup):
    """Tenant B knows tenant A's conversation_id and tries to fetch it."""
    app, client, cid = setup
    app.state._test_ctx = _ctx("t_b")
    r = client.get(f"/v1/conversations/{cid}/transcript")
    assert r.status_code == 404


def test_missing_conversation_returns_typed_error(setup):
    _, client, _ = setup
    r = client.get("/v1/conversations/never-existed/download-url")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "conversation_not_found"
