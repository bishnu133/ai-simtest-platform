"""Typed error envelope and stable error code registry (Week 6a §11.2).

Every error response in the Week 6a API surface uses one canonical envelope
shape. Error codes are stable strings that clients can branch on without
parsing human-readable messages.

Per the v1.2.2 review note, `cross_tenant_forbidden` (tenant-boundary) and
`forbidden_role` (within-tenant role denial) are distinct codes.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stable error code registry — DO NOT rename without an API version bump
# ---------------------------------------------------------------------------


class ErrorCodes:
    """Stable error code constants. Client contracts depend on these strings."""

    CROSS_TENANT_FORBIDDEN = "cross_tenant_forbidden"
    FORBIDDEN_ROLE = "forbidden_role"
    RUN_NOT_FOUND = "run_not_found"
    COMPARISON_NOT_FOUND = "comparison_not_found"
    COMPARISON_NOT_SUPPORTED = "comparison_not_supported"
    COMPARISON_INELIGIBLE = "comparison_ineligible"
    TRANSCRIPT_NOT_FOUND = "transcript_not_found"
    SIGNED_URL_UNAVAILABLE = "signed_url_unavailable"
    INVALID_CURSOR = "invalid_cursor"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TENANT_CONTEXT_MISSING = "tenant_context_missing"


# ---------------------------------------------------------------------------
# Envelope shape — locked by OpenAPI snapshot
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# Typed exception hierarchy — services and routers raise these
# ---------------------------------------------------------------------------


class APIError(Exception):
    """Base API error. Always carries a stable code and HTTP status."""

    code: str = "internal_error"
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class CrossTenantForbidden(APIError):
    code = ErrorCodes.CROSS_TENANT_FORBIDDEN
    http_status = status.HTTP_403_FORBIDDEN


class ForbiddenRole(APIError):
    code = ErrorCodes.FORBIDDEN_ROLE
    http_status = status.HTTP_403_FORBIDDEN


class RunNotFound(APIError):
    code = ErrorCodes.RUN_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND


class TenantContextMissing(APIError):
    code = ErrorCodes.TENANT_CONTEXT_MISSING
    http_status = status.HTTP_401_UNAUTHORIZED


class InvalidCursor(APIError):
    code = ErrorCodes.INVALID_CURSOR
    http_status = 422


# ---------------------------------------------------------------------------
# Exception handler — installs the canonical envelope shape on every APIError
# ---------------------------------------------------------------------------


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    body = ErrorEnvelope(
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            correlation_id=correlation_id,
        )
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=body.model_dump(),
        headers={"X-Correlation-Id": correlation_id} if correlation_id else {},
    )
