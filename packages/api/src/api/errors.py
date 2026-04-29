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
    CROSS_WORKSPACE_FORBIDDEN = "cross_workspace_forbidden"
    FORBIDDEN_ROLE = "forbidden_role"
    RUN_NOT_FOUND = "run_not_found"
    TENANT_NOT_FOUND = "tenant_not_found"
    WORKSPACE_NOT_FOUND = "workspace_not_found"
    DUPLICATE_TENANT = "duplicate_tenant"
    DUPLICATE_WORKSPACE = "duplicate_workspace"
    DUPLICATE_MEMBERSHIP = "duplicate_membership"
    COMPARISON_NOT_FOUND = "comparison_not_found"
    COMPARISON_NOT_SUPPORTED = "comparison_not_supported"
    COMPARISON_INELIGIBLE = "comparison_ineligible"
    TRANSCRIPT_NOT_FOUND = "transcript_not_found"
    SIGNED_URL_UNAVAILABLE = "signed_url_unavailable"
    INVALID_CURSOR = "invalid_cursor"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TENANT_CONTEXT_MISSING = "tenant_context_missing"
    # Turn 3 — auth lifecycle error codes. Stable strings; clients
    # branch on these to distinguish missing-creds from invalid-creds
    # from authorized-but-no-membership.
    AUTH_CREDENTIAL_MISSING = "auth_credential_missing"
    AUTH_CREDENTIAL_INVALID = "auth_credential_invalid"
    AUTH_PROVIDER_MISCONFIGURED = "auth_provider_misconfigured"
    NOT_MEMBER_OF_TENANT = "not_member_of_tenant"
    MISSING_PROVIDER_ORG_ROLE = "missing_provider_org_role"
    UNKNOWN_PROVIDER_ORG_ROLE = "unknown_provider_org_role"
    # Turn 4 — bootstrap state-invariant violation. Distinct from
    # NotMemberOfTenant because the failure mode is structural (tenant
    # row exists in a state inconsistent with the bootstrap flow) rather
    # than membership-shaped.
    TENANT_STATE_INVALID = "tenant_state_invalid"


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


class CrossWorkspaceForbidden(APIError):
    """Raised when a request scoped to one workspace tries to access a resource
    in another workspace within the same tenant.

    The info-leak guard pattern from `CrossTenantForbidden` extends to
    workspaces per Turn 2 plan §5.7: if a read returns no rows under the
    current (tenant_id, workspace_id) but a row exists under the same
    tenant_id with a different workspace_id, repositories raise this
    exception instead of NotFound so existence probing across workspace
    boundaries within a tenant is blocked.
    """

    code = ErrorCodes.CROSS_WORKSPACE_FORBIDDEN
    http_status = status.HTTP_403_FORBIDDEN


class ForbiddenRole(APIError):
    code = ErrorCodes.FORBIDDEN_ROLE
    http_status = status.HTTP_403_FORBIDDEN


class RunNotFound(APIError):
    code = ErrorCodes.RUN_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND


class TenantNotFound(APIError):
    """Raised when a tenant lookup by id/slug/clerk_org_id finds no row."""

    code = ErrorCodes.TENANT_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND


class WorkspaceNotFound(APIError):
    """Raised when a workspace lookup finds no row under the given tenant."""

    code = ErrorCodes.WORKSPACE_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND


class DuplicateTenant(APIError):
    """Raised when a tenant insert violates a uniqueness constraint
    (slug or clerk_org_id)."""

    code = ErrorCodes.DUPLICATE_TENANT
    http_status = status.HTTP_409_CONFLICT


class DuplicateWorkspace(APIError):
    """Raised when a workspace insert violates the
    `at most one default per tenant` partial unique index."""

    code = ErrorCodes.DUPLICATE_WORKSPACE
    http_status = status.HTTP_409_CONFLICT


class DuplicateMembership(APIError):
    """Raised when a membership insert violates the §7.1 partial unique
    indexes (tenant-level or workspace-level)."""

    code = ErrorCodes.DUPLICATE_MEMBERSHIP
    http_status = status.HTTP_409_CONFLICT


class TenantContextMissing(APIError):
    code = ErrorCodes.TENANT_CONTEXT_MISSING
    http_status = status.HTTP_401_UNAUTHORIZED


class InvalidCursor(APIError):
    code = ErrorCodes.INVALID_CURSOR
    http_status = 422


# ---------------------------------------------------------------------------
# Turn 3 — auth lifecycle exceptions
# ---------------------------------------------------------------------------
#
# Shape distinctions:
#   AuthCredentialMissing   — 401 — no Authorization header / token
#   AuthCredentialInvalid   — 401 — signature / expiry / issuer / audience fail
#   AuthProviderMisconfigured — 500 — JWKS unreachable, env misconfigured
#   NotMemberOfTenant       — 403 — creds ok, but no membership rows at all
#   MissingProviderOrgRole  — 403 — creds ok, but Clerk token missing
#                                   `org.role` claim (admin forgot template)
#   UnknownProviderOrgRole  — 403 — creds ok, org.role claim present but value
#                                   not mappable to any internal role
#
# These are distinct so clients and audit events can distinguish
# "problem with the credential" from "problem with the membership shape"
# from "problem with the provider config".


class AuthCredentialMissing(APIError):
    code = ErrorCodes.AUTH_CREDENTIAL_MISSING
    http_status = status.HTTP_401_UNAUTHORIZED


class AuthCredentialInvalid(APIError):
    code = ErrorCodes.AUTH_CREDENTIAL_INVALID
    http_status = status.HTTP_401_UNAUTHORIZED


class AuthProviderMisconfigured(APIError):
    code = ErrorCodes.AUTH_PROVIDER_MISCONFIGURED
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


class NotMemberOfTenant(APIError):
    code = ErrorCodes.NOT_MEMBER_OF_TENANT
    http_status = status.HTTP_403_FORBIDDEN


class MissingProviderOrgRole(APIError):
    code = ErrorCodes.MISSING_PROVIDER_ORG_ROLE
    http_status = status.HTTP_403_FORBIDDEN


class UnknownProviderOrgRole(APIError):
    code = ErrorCodes.UNKNOWN_PROVIDER_ORG_ROLE
    http_status = status.HTTP_403_FORBIDDEN


class TenantStateInvalid(APIError):
    """Raised when external auth succeeds but tenant provisioning is
    inconsistent for this credential.

    Examples
    --------
    * JWT ``org_id`` has never been bootstrapped AND self-serve
      provisioning is disabled for this environment
      (``allow_self_serve_provisioning=False``).
    * Tenant row exists but required workspace row does not (structural
      corruption).
    * Bootstrap retry budget exhausted because the same row keeps
      failing the post-condition check.

    ``409 Conflict`` because the request is syntactically valid and the
    credentials verified, but the platform's identity/provisioning
    state does not match. Clients can handle this meaningfully
    (surface a configuration-conflict message rather than retry);
    operators can distinguish it from 5xx server faults.

    Distinct from ``NotMemberOfTenant`` (which is a membership-row
    absence inside an otherwise-provisioned tenant).
    """

    code = ErrorCodes.TENANT_STATE_INVALID
    http_status = status.HTTP_409_CONFLICT


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
