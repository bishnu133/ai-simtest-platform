"""FastAPI router for the Asset Registry.

Exposes the full asset lifecycle + hybrid-storage operations as HTTP
endpoints. All routes require a TenantContext injected via FastAPI
dependency — the middleware layer (S1) resolves this from JWT + tenant
headers. For Week 5 tests, we inject the context directly via dependency
override.

Error mapping:
  AssetNotFound      → 404
  AssetAlreadyExists → 409
  AssetImmutableError→ 409
  AssetStateError    → 409
  AssetIntegrityError→ 500
  AssetServiceError  → 400
  TenantIsolationError → 403
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from src.assets.models import (
    AssetApprovalRequest,
    AssetCreateRequest,
    AssetListFilter,
    AssetUpdateRequest,
    AssetVersionRequest,
)
from src.assets.schemas import (
    AssetCloneRequest,
    AssetListResponse,
    AssetResponse,
    AssetVersionListResponse,
    ErrorResponse,
    SignedUrlResponse,
)
from src.assets.service import (
    AssetAlreadyExists,
    AssetImmutableError,
    AssetIntegrityError,
    AssetNotFound,
    AssetService,
    AssetServiceError,
    AssetStateError,
)
from src.common.models import AssetStatus, AssetType, TenantContext
from src.storage.models import TenantIsolationError


# ---------------------------------------------------------------------------
# Dependencies — overridden in tests
# ---------------------------------------------------------------------------


async def get_tenant_context(request: Request) -> TenantContext:
    """Default dependency stub. Tests override this.

    In production, this is provided by the S1 tenant middleware which
    resolves JWT claims to a TenantContext with RLS session variables.
    """
    ctx = getattr(request.state, "tenant_context", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No tenant context on request",
        )
    return ctx


async def get_asset_service(request: Request) -> AssetService:
    """Default dependency stub. Tests override this."""
    svc = getattr(request.app.state, "asset_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Asset service not configured",
        )
    return svc


# ---------------------------------------------------------------------------
# Error mapping helper
# ---------------------------------------------------------------------------


def _map_service_error(exc: Exception) -> HTTPException:
    """Convert service-layer exceptions to typed HTTPException responses."""
    correlation_id = None  # Populated by middleware in production
    if isinstance(exc, AssetNotFound):
        return HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error_code="asset_not_found",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    if isinstance(exc, AssetAlreadyExists):
        return HTTPException(
            status_code=409,
            detail=ErrorResponse(
                error_code="asset_slug_conflict",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    if isinstance(exc, AssetImmutableError):
        return HTTPException(
            status_code=409,
            detail=ErrorResponse(
                error_code="asset_immutable",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    if isinstance(exc, AssetStateError):
        return HTTPException(
            status_code=409,
            detail=ErrorResponse(
                error_code="asset_invalid_state_transition",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    if isinstance(exc, AssetIntegrityError):
        return HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="asset_integrity_failure",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    if isinstance(exc, TenantIsolationError):
        return HTTPException(
            status_code=403,
            detail=ErrorResponse(
                error_code="tenant_isolation_violation",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    if isinstance(exc, AssetServiceError):
        return HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error_code="asset_bad_request",
                message=str(exc),
                correlation_id=correlation_id,
            ).model_dump(),
        )
    raise exc  # Unknown — bubble up to FastAPI default handler


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter(prefix="/v1/assets", tags=["assets"])


@router.get("", response_model=AssetListResponse)
async def list_assets(
    asset_type: AssetType | None = Query(None),
    status_filter: AssetStatus | None = Query(None, alias="status"),
    slug: str | None = Query(None),
    tag_key: str | None = Query(None),
    tag_value: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetListResponse:
    """List assets with filters and cursor pagination."""
    try:
        page = await svc.list_assets(
            ctx,
            AssetListFilter(
                asset_type=asset_type,
                status=status_filter,
                slug=slug,
                tag_key=tag_key,
                tag_value=tag_value,
                limit=limit,
                cursor=cursor,
            ),
        )
    except AssetServiceError as exc:
        raise _map_service_error(exc)

    return AssetListResponse(
        items=[AssetResponse.from_record(r) for r in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset(
    req: AssetCreateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.create_asset(ctx, req)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: str,
    version: int | None = Query(None, ge=1),
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.get_asset(ctx, asset_id, version=version)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_draft(
    asset_id: str,
    req: AssetUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.update_draft(ctx, asset_id, req)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.get("/{asset_id}/versions", response_model=AssetVersionListResponse)
async def list_versions(
    asset_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetVersionListResponse:
    try:
        versions = await svc.list_versions(ctx, asset_id)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetVersionListResponse(
        asset_id=asset_id,
        versions=[AssetResponse.from_record(v) for v in versions],
    )


@router.post(
    "/{asset_id}/versions",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_version(
    asset_id: str,
    req: AssetVersionRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.create_version(ctx, asset_id, req)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.post("/{asset_id}/approve", response_model=AssetResponse)
async def approve_asset(
    asset_id: str,
    req: AssetApprovalRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.approve(ctx, asset_id, req)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.post("/{asset_id}/deprecate", response_model=AssetResponse)
async def deprecate_asset(
    asset_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.deprecate(ctx, asset_id)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.post(
    "/{asset_id}/clone",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_asset(
    asset_id: str,
    req: AssetCloneRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        record = await svc.clone(ctx, asset_id, req.new_slug, req.new_name)
    except AssetServiceError as exc:
        raise _map_service_error(exc)
    return AssetResponse.from_record(record)


@router.get("/{asset_id}/download-url", response_model=SignedUrlResponse)
async def get_asset_download_url(
    asset_id: str,
    version: int | None = Query(None, ge=1),
    ttl_seconds: int = Query(900, ge=60, le=3600),
    ctx: TenantContext = Depends(get_tenant_context),
    svc: AssetService = Depends(get_asset_service),
) -> SignedUrlResponse:
    """Generate a signed URL for downloading a large-payload asset.

    Only works for assets in object-tier storage. Assets with inline
    content return 400 — use the regular GET endpoint instead.
    """
    try:
        record = await svc.get_asset(ctx, asset_id, version=version)
    except AssetServiceError as exc:
        raise _map_service_error(exc)

    if record.payload_ref is None:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error_code="asset_inline_storage",
                message="Asset is inline-stored; use GET /assets/{id} instead",
            ).model_dump(),
        )

    signed = await svc._storage.sign_url(
        tenant_id=ctx.tenant_id,
        key=record.payload_ref.key,
        method="GET",
        expires_in=timedelta(seconds=ttl_seconds),
    )
    return SignedUrlResponse(
        url=signed.url,
        method=signed.method,
        expires_at=signed.expires_at,
        headers=signed.headers,
    )
