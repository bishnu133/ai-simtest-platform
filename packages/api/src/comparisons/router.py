"""Comparisons router (Week 6a Turn 3, v1.2.2 Correction 3 HTTP matrix).

POST /v1/comparisons response codes:
  201 Created           — first successful create
  200 OK                — idempotent replay
  409 Conflict          — idempotency_conflict OR comparison_ineligible
  503 Service Unavail.  — comparison_not_supported (provider unavailable)
  403 Forbidden         — forbidden_role (within-tenant) or cross_tenant_forbidden
"""
from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response, status

from src.api.deps import get_tenant_context, require_role
from src.api.errors import APIError, InvalidCursor, SecretLeakRejected
from src.audit._compat import to_tenant_audit_event_lenient
from src.audit.logger import AuditActions, audit_logger
from src.secrets.denylist import SecretLeakDetected, check_no_secrets
from src.common.models import TenantContext
from src.comparisons.models import (
    ComparisonListResponse,
    ComparisonRecord,
    CreateComparisonRequest,
    RegressionSignal,
)
from src.comparisons.service import (
    ComparisonService,
    IdempotentReplay,
)

router = APIRouter(tags=["comparisons"])


def _get_comparison_service() -> ComparisonService:
    raise RuntimeError("ComparisonService dependency must be overridden by app wiring")


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:
        raise InvalidCursor(f"Malformed cursor: {exc}")


@router.post(
    "/comparisons",
    response_model=ComparisonRecord,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("member"))],
)
async def create_comparison(
    body: CreateComparisonRequest,
    response: Response,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ComparisonService = Depends(_get_comparison_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # FH-Tier-2 Slice 2 (Design B): reject secret-shaped config at the HTTP
    # boundary BEFORE any business workflow. ctx is resolved here, so the
    # event is tenant-scoped; safe-emit keeps the response 422 even if audit
    # persistence fails.
    try:
        check_no_secrets(body.config)
    except SecretLeakDetected as leak:
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event_lenient(
                ctx,
                AuditActions.COMPARISON_CREATE_REJECTED_SECRET_LEAK,
                resource_type="comparison",
                resource_id="comparison:create",
                metadata={"field_path": leak.field_path, "reason": "secret_leak_detected"},
            )
        )
        raise SecretLeakRejected(
            "Comparison config contains a denylisted credential-shaped key",
            details={"field_path": leak.field_path},
        )

    try:
        record = await svc.create_comparison(
            ctx, body.left_run_id, body.right_run_id, idempotency_key=idempotency_key
        )
    except IdempotentReplay as replay:
        # Return existing with 200 OK, not 201
        response.status_code = status.HTTP_200_OK
        response.headers["Location"] = f"/v1/comparisons/{replay.existing.id}"
        if idempotency_key:
            response.headers["Idempotency-Key"] = idempotency_key
        return replay.existing

    response.headers["Location"] = f"/v1/comparisons/{record.id}"
    if idempotency_key:
        response.headers["Idempotency-Key"] = idempotency_key
    return record


@router.get("/comparisons/{comparison_id}", response_model=ComparisonRecord)
async def get_comparison(
    comparison_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ComparisonService = Depends(_get_comparison_service),
):
    return await svc.get_comparison(ctx, comparison_id)


@router.get("/comparisons", response_model=ComparisonListResponse)
async def list_comparisons(
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ComparisonService = Depends(_get_comparison_service),
    limit: int = 50,
    cursor: str | None = None,
):
    offset = 0
    if cursor is not None:
        offset = int(_decode_cursor(cursor).get("offset", 0))

    full = await svc.list_comparisons(ctx)  # already sorted: created_at DESC, id DESC
    page = full[offset : offset + limit]
    has_more = (offset + limit) < len(full)
    next_cursor = _encode_cursor({"offset": offset + limit}) if has_more else None
    return ComparisonListResponse(items=page, next_cursor=next_cursor, has_more=has_more)


@router.get(
    "/comparisons/{comparison_id}/regression-signals",
    response_model=list[RegressionSignal],
)
async def get_regression_signals(
    comparison_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: ComparisonService = Depends(_get_comparison_service),
):
    record = await svc.get_comparison(ctx, comparison_id)
    return record.regression_signals
