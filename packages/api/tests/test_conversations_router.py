"""Week 6a Turn 2: conversations router tests (10 tests)."""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_tenant_context
from src.api.errors import APIError, api_error_handler
from src.audit.logger import AuditActions, audit_logger
from src.common.models import ActorRef, TenantContext
from src.conversations.models import ConversationVerdict, Turn
from src.conversations.router import (
    _get_conversation_service,
    router as conversations_router,
)
from src.conversations.service import ConversationService
from src.storage.local import LocalFilesystemAdapter


@pytest.fixture
def actor() -> ActorRef:
    return ActorRef(actor_id="alice", actor_type="human")


@pytest.fixture
def ctx_a(actor):
    return TenantContext(tenant_id="t_a", workspace_id="w_main", actor=actor)


@pytest.fixture
def ctx_b():
    return TenantContext(
        tenant_id="t_b",
        workspace_id="w_main",
        actor=ActorRef(actor_id="bob", actor_type="human"),
    )


@pytest_asyncio.fixture
async def app(tmp_path, ctx_a):
    audit_logger.clear_all()
    storage = LocalFilesystemAdapter(
        root=str(tmp_path / "s"), bucket="b", signing_secret="x"
    )
    svc = ConversationService(storage)

    # Seed 3 conversations in tenant_a
    from src.conversations.models import ConversationTranscript

    for i in range(3):
        transcript = ConversationTranscript(
            conversation_id=f"conv_{i}",
            run_id="run_1",
            persona_id="persona_x",
            turns=[
                Turn(turn_number=1, role="user", content="hi"),
                Turn(turn_number=2, role="assistant", content="hello"),
            ],
        )
        await svc.store_conversation(
            ctx=ctx_a,
            run_id="run_1",
            persona_id="persona_x",
            persona_name="Persona X",
            transcript=transcript,
            verdict="pass",
        )

    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(conversations_router, prefix="/v1")
    app.dependency_overrides[_get_conversation_service] = lambda: svc
    app.state._test_ctx = ctx_a
    app.dependency_overrides[get_tenant_context] = lambda: app.state._test_ctx
    app.state._svc = svc
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_list_conversations_returns_items(client):
    resp = client.get("/v1/conversations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_list_conversations_cursor_paginates(client):
    r1 = client.get("/v1/conversations?limit=2")
    body = r1.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    cursor = body["next_cursor"]
    r2 = client.get(f"/v1/conversations?limit=2&cursor={cursor}")
    assert len(r2.json()["items"]) == 1


def test_list_conversations_invalid_cursor_returns_422(client):
    resp = client.get("/v1/conversations?cursor=not-base64!!!")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cursor"


def test_get_conversation_happy_path(client, app):
    summaries = client.get("/v1/conversations").json()["items"]
    cid = summaries[0]["id"]
    resp = client.get(f"/v1/conversations/{cid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cid


def test_get_conversation_404(client):
    resp = client.get("/v1/conversations/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "conversation_not_found"


def test_get_conversation_cross_tenant_denial(client, app, ctx_b):
    cid = client.get("/v1/conversations").json()["items"][0]["id"]
    app.state._test_ctx = ctx_b
    resp = client.get(f"/v1/conversations/{cid}")
    # tenant_b cannot see tenant_a's conversation -> looks like 404 (info-leak guard)
    assert resp.status_code == 404


def test_get_transcript_returns_redaction_shell(client):
    cid = client.get("/v1/conversations").json()["items"][0]["id"]
    resp = client.get(f"/v1/conversations/{cid}/transcript")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conversation_id"] == cid
    assert body["redaction"] == {
        "applied": False,
        "policy_version": None,
        "masked_field_count": 0,
    }
    assert len(body["transcript"]) == 2


def test_get_transcript_emits_audit_event(client):
    cid = client.get("/v1/conversations").json()["items"][0]["id"]
    audit_logger.clear_all()
    client.get(f"/v1/conversations/{cid}/transcript")
    events = audit_logger.query_all_events(action=AuditActions.CONVERSATION_TRANSCRIPT_VIEWED)
    assert len(events) == 1
    assert events[0].resource_id == cid


def test_download_url_locked_response_shape_and_audit(client):
    cid = client.get("/v1/conversations").json()["items"][0]["id"]
    audit_logger.clear_all()
    resp = client.get(f"/v1/conversations/{cid}/download-url")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Locked shape per v1.2.2 §M3
    assert set(body.keys()) == {
        "url",
        "method",
        "expires_in_seconds",
        "expires_at",
        "correlation_id",
    }
    assert body["method"] == "GET"
    assert body["expires_in_seconds"] == 300
    assert body["url"].startswith(("http", "file://"))

    events = audit_logger.query_all_events(action=AuditActions.CONVERSATION_DOWNLOAD_URL_ISSUED)
    assert len(events) == 1


def test_download_url_cross_tenant_denial(client, app, ctx_b):
    cid = client.get("/v1/conversations").json()["items"][0]["id"]
    app.state._test_ctx = ctx_b
    resp = client.get(f"/v1/conversations/{cid}/download-url")
    assert resp.status_code == 404
