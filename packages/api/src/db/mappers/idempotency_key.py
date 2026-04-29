"""IdempotencyKey mapper — simple cache row round-trip.

The shipped Week 6a idempotency store is in-memory (`InMemoryIdempotencyStore`
in src/comparisons/idempotency.py) and uses a minimal 3-field shape:
body_hash, comparison_id, expires_at. The ORM stores the full HTTP
cache row shape: idempotency_key, request_hash, response_payload (the
full cached response body as JSONB), status_code, expires_at.

Turn 1b exposes a small domain record around the ORM shape so Turn 2's
Postgres-backed idempotency store can live alongside the in-memory one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import IdempotencyKey


class IdempotencyKeyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    workspace_id: str  # added in Turn 2.6 Step 3 / migration 0003
    idempotency_key: str
    request_hash: str
    response_payload: dict[str, Any] = Field(default_factory=dict)
    status_code: int
    created_at: datetime
    expires_at: datetime


def idempotency_key_to_domain(row: IdempotencyKey) -> IdempotencyKeyRecord:
    return IdempotencyKeyRecord(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        workspace_id=str(row.workspace_id),
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        response_payload=dict(row.response_payload or {}),
        status_code=row.status_code,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def idempotency_key_to_orm(record: IdempotencyKeyRecord) -> IdempotencyKey:
    return IdempotencyKey(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        idempotency_key=record.idempotency_key,
        request_hash=record.request_hash,
        response_payload=dict(record.response_payload),
        status_code=record.status_code,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )
