"""AuthProvider Protocol + VerifiedClaims model (Turn 3 plan §6.1).

This is the contract boundary between any auth provider
implementation and the rest of the system. Bootstrap, middleware, and
authz all consume `VerifiedClaims` and never know whether the claims
came from Clerk JWT verification, a dev header, or a future service
account token.

Keep this module thin: no imports beyond stdlib + pydantic + fastapi.
Concrete implementations live in sibling modules so tests can verify
the Protocol shape without pulling in provider-specific dependencies
(PyJWT, jwks fetchers, etc).
"""
from __future__ import annotations

from typing import Protocol

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["VerifiedClaims", "AuthProvider"]


class VerifiedClaims(BaseModel):
    """Provider-side identity. Translated to internal tenant_id elsewhere.

    Field semantics:
      user_id — provider subject (Clerk `sub`). Opaque string. Used as
        `memberships.user_id` per plan §2.6 identity contract.
      org_id — provider org id (Clerk `org_id`). Looked up by
        `tenants.get_by_clerk_org_id` during bootstrap.
      email / display_name — optional enrichment for audit/UI only.
      provider_org_role — raw role string from the provider. Mapped to
        an internal Role via src.auth.role_mapping. Missing or
        unmappable values fail closed per plan v0.5.1 MF-2.
      workspace_id — optional explicit workspace claim. If None, the
        default workspace for the tenant is used.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    email: str | None = None
    display_name: str | None = None
    provider_org_role: str | None = None
    workspace_id: str | None = None

    @field_validator("user_id", "org_id")
    @classmethod
    def _strip_and_non_empty(cls, v: str) -> str:
        """Reject whitespace-only values. Pydantic's min_length=1 counts
        whitespace; we want real content."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("must not be empty or whitespace")
        return stripped


class AuthProvider(Protocol):
    """Abstract credential verifier (Turn 3 plan §6.1).

    Implementations:
      ClerkAuthProvider   — production, RS256 JWT via JWKS
      DevAuthProvider     — local development, X-Dev-User-Id header

    Future:
      ServiceAccountAuthProvider — Foundation Hardening F-series

    The verify() method owns ALL credential-extraction and validation
    logic: reading headers, parsing bearer tokens, fetching JWKS,
    calling out to the provider's userinfo endpoint if needed.
    Middleware calls verify() once per request and acts on the result.

    Implementations MUST also expose a `provider_name: str` class
    attribute used for audit-event attribution. This is a contract
    requirement promoted into the Protocol by Turn 3 Hotfix 2 — prior
    to the hotfix, middleware referenced `provider.provider_name`
    without the Protocol declaring it. Allowed values are short
    lowercase identifiers (`"clerk"`, `"dev"`, future `"service_account"`).
    """

    provider_name: str

    async def verify(self, request: Request) -> VerifiedClaims:
        """Verify the request's credentials and return claims.

        Raises:
          AuthCredentialMissing — no credentials on the request (401)
          AuthCredentialInvalid — signature/expiry/malformed (401)
          AuthProviderMisconfigured — JWKS unreachable etc. (500)
        """
        ...
