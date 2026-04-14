"""Conversations router — 4 endpoints (Week 6a Turn 2, v1.2.2 §M3 §11.5).

Endpoints:
  GET  /v1/conversations                      — list (cursor-paginated)
  GET  /v1/conversations/{id}                 — single summary
  GET  /v1/conversations/{id}/transcript      — full transcript with redaction shell
  GET  /v1/conversations/{id}/download-url    — signed R2 URL

Security:
  - Ownership pre-check on transcript and download-url (raises 404, never leaks tenant data)
  - Audit events emitted on transcript view and download URL issuance (§11.8)
  - Signed URL response shape locked per §M3
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from src.api.deps import get_tenant_context
from src.api.errors import APIError, InvalidCursor
from src.audit.logger import AuditActions, audit_logger
from src.common.models import TenantContext, utcnow
from src.conversations.models import ConversationSummary, ConversationVerdict
from src.conversations.service import (
    ConversationNotFound,
    ConversationService,
    TranscriptIntegrityError,
)

router = APIRouter(tags=["conversations"])


def _get_conversation_service() -> ConversationService:
    raise RuntimeError("ConversationService dependency must be overridden by app wiring")


# ---- Response schemas (locked) -------------------------------------------


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    next_cursor: str | None = None
    has_more: bool = False


class TranscriptRedaction(BaseModel):
    """Reserved redaction shell (v1.2.2 §11.5). Always present, no enforcement in 6a."""

    applied: bool = False
    policy_version: str | None = None
    masked_field_count: int = 0


class TranscriptResponse(BaseModel):
    conversation_id: str
    transcript: list[dict[str, Any]]
    redaction: TranscriptRedaction = Field(default_factory=TranscriptRedaction)


class SignedDownloadUrlResponse(BaseModel):
    """Locked per v1.2.2 §M3 — DO NOT modify field names."""

    url: str
    method: str = "GET"
    expires_in_seconds: int
    expires_at: datetime
    correlation_id: str | None = None


# ---- Cursor helpers (opaque base64, reusing the asset pattern) -----------


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:
        raise InvalidCursor(f"Malformed cursor: {exc}")


# ---- Typed error mapping --------------------------------------------------


class TranscriptNotFound(APIError):
    code = "transcript_not_found"
    http_status = 404


class ConversationNotFoundError(APIError):
    code = "conversation_not_found"
    http_status = 404


class SignedUrlUnavailable(APIError):
    code = "signed_url_unavailable"
    http_status = 503


# ---- Endpoints ------------------------------------------------------------


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ConversationService = Depends(_get_conversation_service),
    run_id: str | None = None,
    verdict: ConversationVerdict | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    offset = 0
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        offset = int(decoded.get("offset", 0))

    # Use existing list_summaries (Week 5) — additive cursor logic on top
    full = await svc.list_summaries(ctx, run_id=run_id, verdict=verdict, limit=10_000)
    page = full[offset : offset + limit]
    has_more = (offset + limit) < len(full)
    next_cursor = _encode_cursor({"offset": offset + limit}) if has_more else None
    return ConversationListResponse(items=page, next_cursor=next_cursor, has_more=has_more)


@router.get("/conversations/{conversation_id}", response_model=ConversationSummary)
async def get_conversation(
    conversation_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ConversationService = Depends(_get_conversation_service),
):
    try:
        return await svc.get_summary(ctx, conversation_id)
    except ConversationNotFound:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")


@router.get(
    "/conversations/{conversation_id}/transcript", response_model=TranscriptResponse
)
async def get_transcript(
    conversation_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ConversationService = Depends(_get_conversation_service),
):
    # Ownership pre-check via tenant-scoped get_summary
    try:
        await svc.get_summary(ctx, conversation_id)
    except ConversationNotFound:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")

    try:
        transcript = await svc.get_transcript(ctx, conversation_id)
    except (ConversationNotFound, TranscriptIntegrityError) as exc:
        raise TranscriptNotFound(str(exc))

    # Audit read-sensitive action (v1.2.2 §11.8)
    audit_logger.write(
        ctx,
        action=AuditActions.CONVERSATION_TRANSCRIPT_VIEWED,
        resource_type="conversation",
        resource_id=conversation_id,
    )

    return TranscriptResponse(
        conversation_id=conversation_id,
        transcript=[t.model_dump() for t in transcript.turns],
    )


@router.get(
    "/conversations/{conversation_id}/download-url",
    response_model=SignedDownloadUrlResponse,
)
async def get_download_url(
    conversation_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ConversationService = Depends(_get_conversation_service),
):
    # Ownership pre-check
    try:
        await svc.get_summary(ctx, conversation_id)
    except ConversationNotFound:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")

    try:
        signed = await svc.get_transcript_url(
            ctx, conversation_id, ttl_seconds=300
        )
    except (ConversationNotFound, TranscriptIntegrityError):
        raise TranscriptNotFound(f"Conversation {conversation_id} has no transcript")
    except Exception as exc:
        raise SignedUrlUnavailable(f"Could not generate signed URL: {exc}")

    audit_logger.write(
        ctx,
        action=AuditActions.CONVERSATION_DOWNLOAD_URL_ISSUED,
        resource_type="conversation",
        resource_id=conversation_id,
    )

    correlation_id = getattr(request.state, "correlation_id", None)
    return SignedDownloadUrlResponse(
        url=signed.url,
        method="GET",
        expires_in_seconds=300,
        expires_at=signed.expires_at,
        correlation_id=correlation_id,
    )
