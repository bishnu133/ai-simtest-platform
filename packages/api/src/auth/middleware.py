"""TenantContextMiddleware — the five-step request pipeline (plan §6.3).

Pipeline:
    1. extract_bearer(request)              -> token  (defensive early-401 guard)
    2. provider.verify(request)             -> VerifiedClaims
    3. ensure_tenant_and_workspace + bootstrap -> BootstrapResult
    4. role resolution (happens inside bootstrap for new users,
       fast-path via BootstrapResult.role for returning users)
    5. build TenantContext, attach to request.state

Failure mapping:
    * Step 1 (no/malformed bearer) → 401 ``missing_bearer_token`` + ``auth.rejected``
    * Step 2 (provider.verify raises) → 401 provider-specific code + ``auth.rejected``
    * Step 3 (bootstrap tenant/workspace fails) → 500 ``tenant_bootstrap_failed``
    * Step 4 (membership denied / role missing / unknown) → 403 with plan §6.5
      error code + ``auth.membership_denied``
    * Step 5 success → emits ``auth.accepted`` and passes to handler

The middleware is additive: `main.create_app()` only registers it when
`auth_enabled=True`, preserving Turn 2 test behavior when False.

Session lifecycle: the middleware opens its own AsyncSession for the
bootstrap transaction and closes it on teardown. This session is NOT
the same as the per-request session that route dependencies acquire
via `get_request_session`; bootstrap runs to completion BEFORE the
route executes, so there is no session overlap.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Awaitable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.errors import (
    APIError,
    AuthCredentialInvalid,
    AuthCredentialMissing,
    AuthProviderMisconfigured,
    CrossTenantForbidden,
    MissingProviderOrgRole,
    NotMemberOfTenant,
    UnknownProviderOrgRole,
    WorkspaceNotFound,
)
from src.audit.logger import AuditActions, audit_logger
from src.auth.bootstrap import bootstrap
from src.api.errors import TenantStateInvalid
from src.auth.provider import AuthProvider
from src.common.models import ActorRef, TenantContext

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

__all__ = ["TenantContextMiddleware"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response builders — each failure path returns a shaped JSONResponse.
# The shape matches existing exception-handler behavior in src/api/errors.py
# so client code sees one consistent error envelope across auth and
# non-auth failures.
# ---------------------------------------------------------------------------


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    """Build an error response that matches the canonical ErrorEnvelope.

    The shape mirrors src/api/errors.py::api_error_handler so clients see
    ONE envelope across middleware and post-middleware failures:

        {"error": {"code": str, "message": str, "details": dict,
                   "correlation_id": str | None}}
    """
    body: dict = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "correlation_id": None,
        }
    }
    return JSONResponse(status_code=status_code, content=body)


def _extract_bearer(request: Request) -> str | None:
    """Pull the bearer token out of the Authorization header.

    Returns None when the header is missing or malformed. The caller
    distinguishes None (step 1 failure) from a populated token
    (step 1 success, on to step 2).
    """
    header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _build_audit_ctx_from_claims(
    claims, tenant_id: str | None = None, workspace_id: str | None = None
) -> TenantContext:
    """Build a minimal TenantContext for audit emission.

    Used when we need to log an auth event but the full bootstrap has
    not completed. We fill in placeholder IDs when tenant/workspace
    haven't been resolved yet; the audit write captures whatever we
    know at the time of failure.
    """
    return TenantContext(
        tenant_id=tenant_id or "00000000-0000-0000-0000-000000000000",
        workspace_id=workspace_id or "00000000-0000-0000-0000-000000000000",
        actor=ActorRef(
            actor_id=claims.user_id if claims else "unknown",
            actor_type="human",
        ),
    )


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Starlette middleware implementing the Turn 3 §6.3 five-step pipeline.

    Parameters
    ----------
    app:
        The ASGI app (supplied by Starlette when added to the stack).
    provider:
        An ``AuthProvider`` instance. ``ClerkAuthProvider`` in prod,
        ``DevAuthProvider`` in dev. Production guardrail lives in
        ``app_factory.create_app`` (Turn 4), not here.
    sessionmaker:
        An ``async_sessionmaker`` for the per-request bootstrap
        transaction. Injected so tests can substitute a test-scoped
        sessionmaker.
    exempt_paths:
        Iterable of path prefixes that skip the pipeline entirely
        (typically ``/health``, ``/docs``, ``/openapi.json``). Empty by
        default — callers pass what they need from ``main.create_app``.
    """

    def __init__(
        self,
        app,
        *,
        provider: AuthProvider,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        exempt_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__(app)
        self._provider = provider
        self._sessionmaker = sessionmaker
        self._exempt_paths = tuple(exempt_paths)

    # ----- main entry point ------------------------------------------------

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Short-circuit on exempt paths (health checks, docs).
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in self._exempt_paths):
            return await call_next(request)

        # ----- Step 1: extract bearer ------------------------------------
        token = _extract_bearer(request)
        if token is None:
            # Can't audit with an actor — emit an audit event with the
            # reserved "anonymous" placeholder, to keep the auth stream
            # complete.
            anon_ctx = TenantContext(
                tenant_id="00000000-0000-0000-0000-000000000000",
                workspace_id="00000000-0000-0000-0000-000000000000",
                actor=ActorRef(actor_id="anonymous", actor_type="human"),
            )
            audit_logger.write(
                ctx=anon_ctx,
                action=AuditActions.AUTH_REJECTED,
                resource_type="auth",
                resource_id="request",
                metadata={
                    "step": "extract_bearer",
                    "reason": "missing_bearer_token",
                    "path": path,
                },
            )
            return _error_response(
                401,
                "missing_bearer_token",
                "Authorization header is missing or malformed.",
            )

        # ----- Step 2: provider.verify -----------------------------------
        # Per AuthProvider Protocol (src/auth/provider.py §6.1), verify()
        # takes the Request and owns ALL credential extraction. The Step 1
        # _extract_bearer above is a defensive early-401 guard so missing/
        # malformed Authorization headers fail fast with an audit event
        # before delegating to the provider. The `token` local is unused
        # downstream — kept only for the Step 1 short-circuit above.
        # Hotfix: Turn 3 Session 3 → Turn 3 Hotfix 2 (April 20, 2026).
        try:
            claims = await self._provider.verify(request)
        except (AuthCredentialMissing, AuthCredentialInvalid) as exc:
            anon_ctx = TenantContext(
                tenant_id="00000000-0000-0000-0000-000000000000",
                workspace_id="00000000-0000-0000-0000-000000000000",
                actor=ActorRef(actor_id="anonymous", actor_type="human"),
            )
            audit_logger.write(
                ctx=anon_ctx,
                action=AuditActions.AUTH_REJECTED,
                resource_type="auth",
                resource_id="request",
                metadata={
                    "step": "provider.verify",
                    "reason": exc.code,
                    "provider": self._provider.provider_name,
                    "path": path,
                },
            )
            return _error_response(
                exc.http_status,
                exc.code,
                exc.message or "Token verification failed.",
                details=exc.details or None,
            )
        except AuthProviderMisconfigured as exc:
            # 500 — operator-facing; no user-caused reason to audit as auth.rejected
            logger.error(
                "AuthProvider misconfigured: %s", exc.message, exc_info=True
            )
            return _error_response(
                exc.http_status,
                exc.code,
                "Authentication provider is misconfigured.",
            )
        except APIError as exc:
            # Any other APIError raised by the provider — surface with its own code/status.
            return _error_response(
                exc.http_status, exc.code, exc.message, details=exc.details or None
            )
        except Exception:  # pragma: no cover — unexpected provider bug
            logger.exception("Unexpected error during provider.verify")
            return _error_response(
                500,
                "auth_provider_error",
                "Internal error verifying credentials.",
            )

        # ----- Steps 3 + 4: bootstrap (tenant + workspace + membership) --
        # bootstrap() owns its own transaction via `async with session.begin()`.
        # We open a session scoped to this middleware call and close it on
        # the way out regardless of success/failure.
        #
        # Turn 4 §3.10 — read the provisioning policy from the
        # AppSettings that app_factory stashed on app.state. Defaults
        # to True when settings are not present (legacy / pre-Turn-4
        # callers that constructed the middleware directly), preserving
        # the original behaviour.
        settings = getattr(request.app.state, "settings", None)
        allow_self_serve = (
            settings.allow_self_serve_provisioning
            if settings is not None
            else True
        )

        session = self._sessionmaker()
        try:
            try:
                result = await bootstrap(
                    session,
                    claims,
                    allow_self_serve_provisioning=allow_self_serve,
                )
            except (MissingProviderOrgRole, UnknownProviderOrgRole) as exc:
                # Fail-closed role mapping — audit already emitted by
                # ensure_membership before raising.
                return _error_response(
                    exc.http_status,
                    exc.code,
                    exc.message or "Membership denied.",
                    details=exc.details or None,
                )
            except NotMemberOfTenant as exc:
                # Explicit non-membership failure (future-proofing; bootstrap
                # does not currently raise this itself, but step-4-like
                # handlers may).
                audit_ctx = _build_audit_ctx_from_claims(claims)
                audit_logger.write(
                    ctx=audit_ctx,
                    action=AuditActions.AUTH_MEMBERSHIP_DENIED,
                    resource_type="membership",
                    resource_id=claims.user_id,
                    metadata={"step": "bootstrap", "reason": exc.code},
                )
                return _error_response(
                    exc.http_status,
                    exc.code,
                    exc.message or "Actor is not a member of this tenant.",
                    details=exc.details or None,
                )
            except CrossTenantForbidden as exc:
                # Explicit workspace claim crossed tenants — 403.
                audit_ctx = _build_audit_ctx_from_claims(claims)
                audit_logger.write(
                    ctx=audit_ctx,
                    action=AuditActions.AUTH_MEMBERSHIP_DENIED,
                    resource_type="workspace",
                    resource_id=claims.workspace_id or "unknown",
                    metadata={
                        "step": "bootstrap",
                        "reason": "cross_tenant_workspace_claim",
                        "org_id": claims.org_id,
                    },
                )
                return _error_response(
                    exc.http_status,
                    exc.code,
                    exc.message or "Workspace claim crosses tenant boundary.",
                    details=exc.details or None,
                )
            except WorkspaceNotFound as exc:
                audit_ctx = _build_audit_ctx_from_claims(claims)
                audit_logger.write(
                    ctx=audit_ctx,
                    action=AuditActions.AUTH_REJECTED,
                    resource_type="workspace",
                    resource_id=claims.workspace_id or "unknown",
                    metadata={
                        "step": "bootstrap",
                        "reason": "workspace_not_found",
                    },
                )
                return _error_response(
                    exc.http_status,
                    exc.code,
                    exc.message or "Workspace not found.",
                    details=exc.details or None,
                )
            except TenantStateInvalid as exc:
                # Turn 4 §3.10 — structural state corruption surfaced by
                # bootstrap. Distinct audit action so dashboards can
                # alert on it independently of the credential-shaped
                # AUTH_REJECTED stream.
                audit_ctx = _build_audit_ctx_from_claims(claims)
                audit_logger.write(
                    ctx=audit_ctx,
                    action=AuditActions.AUTH_TENANT_STATE_INVALID,
                    resource_type="tenant",
                    resource_id=claims.org_id or "unknown",
                    metadata={
                        "step": "bootstrap",
                        "reason": exc.code,
                        "details": exc.details or {},
                    },
                )
                logger.error(
                    "TenantStateInvalid during bootstrap (org_id=%s): %s",
                    claims.org_id,
                    exc.message,
                )
                return _error_response(
                    exc.http_status,
                    exc.code,
                    exc.message or "Tenant state is invalid.",
                    details=exc.details or None,
                )
            except APIError as exc:
                # Any other typed API error from bootstrap — surface with code/status.
                logger.warning("APIError during bootstrap: %s", exc.code)
                return _error_response(
                    exc.http_status,
                    exc.code,
                    exc.message,
                    details=exc.details or None,
                )
            except Exception:
                logger.exception("Unexpected error during bootstrap")
                audit_ctx = _build_audit_ctx_from_claims(claims)
                audit_logger.write(
                    ctx=audit_ctx,
                    action=AuditActions.AUTH_REJECTED,
                    resource_type="auth",
                    resource_id="bootstrap",
                    metadata={
                        "step": "bootstrap",
                        "reason": "tenant_bootstrap_failed",
                    },
                )
                return _error_response(
                    500,
                    "tenant_bootstrap_failed",
                    "Tenant bootstrap failed.",
                )
        finally:
            await session.close()

        # ----- Step 5: build TenantContext + attach to request.state -----
        ctx = TenantContext(
            tenant_id=result.tenant_id,
            workspace_id=result.workspace_id,
            actor=ActorRef(
                actor_id=claims.user_id,
                actor_type="human",
                display_name=claims.display_name,
                email=claims.email,
            ),
        )

        # Fast-path: expose the resolved role on the context so
        # `get_actor_role` can short-circuit the DB lookup for the
        # current request. Pydantic models are frozen by default in our
        # codebase; we use object.__setattr__ to bypass the freeze —
        # this matches the existing test-injection pattern from deps.py.
        object.__setattr__(ctx, "role", result.role)

        # Plan §6.5 #7 invariant — DO NOT override an outer-middleware-set
        # context. Resolved Turn 3 Session 3 (Bishnu Option 1A): if a
        # prior middleware already attached a TenantContext to
        # request.state, preserve it rather than clobber. This guards
        # nested-middleware composition (e.g. service-account paths
        # where an outer layer synthesizes a context). Audit still
        # emits using THIS middleware's resolved ctx so the auth event
        # reflects the credential-shaped resolution that actually
        # happened, even though the outer ctx is what the route sees.
        existing = getattr(request.state, "tenant_context", None)
        if existing is None:
            request.state.tenant_context = ctx

        # Emit auth.accepted audit event for this request.
        audit_logger.write(
            ctx=ctx,
            action=AuditActions.AUTH_ACCEPTED,
            resource_type="auth",
            resource_id="request",
            metadata={
                "provider": self._provider.provider_name,
                "role": result.role,
                "is_first_user": result.is_first_user,
                "path": path,
            },
        )

        return await call_next(request)
