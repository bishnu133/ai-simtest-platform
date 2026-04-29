"""ClerkAuthProvider — production JWT verification (Turn 3 plan §6.2).

Implements the AuthProvider Protocol for Clerk-issued RS256 JWTs.
Relies on JWKSCache for key resolution.

Configuration (env vars):
  AUTH_PROVIDER           — must be 'clerk' for this provider to run
  CLERK_JWKS_URL          — full URL to Clerk's JWKS endpoint
  CLERK_ISSUER            — expected `iss` claim (e.g., "https://clerk.example.com")
  CLERK_AUDIENCE          — expected `aud` claim. OPTIONAL: if unset, `aud`
                            verification is skipped (per Turn 3 plan v0.2 SR-1)
                            because Clerk session-token templates vary in
                            whether they set an audience. Signature, iss, exp,
                            and nbf are always verified regardless.
  ENVIRONMENT             — 'production' | 'staging' | 'development' | 'test'

Security invariants:
  * RS256 only (reject HS256, none, and other algorithms to prevent
    algorithm-confusion attacks).
  * iss and aud verified on every token.
  * exp and nbf verified with a 30-second leeway (clock skew tolerance).
  * Token `kid` must resolve against JWKS — unknown kids fail closed.
  * Missing required claims (sub, org_id) fail closed with a 401.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

import jwt
from fastapi import Request

from src.api.errors import (
    AuthCredentialInvalid,
    AuthCredentialMissing,
    AuthProviderMisconfigured,
)
from src.auth.jwks_cache import JWKSCache
from src.auth.provider import VerifiedClaims

__all__ = ["ClerkAuthProvider"]


# The only acceptable algorithm for Clerk tokens. Hardcoded to prevent
# algorithm-confusion attacks where an attacker could craft an HS256
# token signed with the public key as the HMAC secret.
_ALLOWED_ALGORITHMS = ["RS256"]

# Clock skew tolerance on exp/nbf verification (seconds).
_CLOCK_SKEW_LEEWAY = 30


class ClerkAuthProvider:
    """Verify incoming Bearer JWTs against Clerk's JWKS.

    Instantiation order:
      1. ClerkAuthProvider(...) — reads env, checks guardrails
      2. For each request: provider.verify(request)

    Tests can inject a mock `jwks_cache` to avoid real network calls.
    """

    # Stable identifier emitted on auth.accepted / auth.rejected audit
    # events so log analysis can attribute traffic to a provider without
    # having to inspect the runtime class name. Hotfix Turn 3 Hotfix 2.
    provider_name: ClassVar[str] = "clerk"

    def __init__(
        self,
        *,
        jwks_url: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        environment: str | None = None,
        jwks_cache: JWKSCache | None = None,
    ) -> None:
        """
        Any constructor arg left as None is resolved from the
        corresponding env var at boot time. Tests pass explicit values;
        production uses env lookup.
        """
        self._jwks_url = jwks_url or os.getenv("CLERK_JWKS_URL")
        self._issuer = issuer or os.getenv("CLERK_ISSUER")
        self._audience = audience or os.getenv("CLERK_AUDIENCE")
        self._environment = environment or os.getenv("ENVIRONMENT", "development")

        if not self._jwks_url:
            raise AuthProviderMisconfigured(
                "CLERK_JWKS_URL not set; ClerkAuthProvider cannot start"
            )
        if not self._issuer:
            raise AuthProviderMisconfigured(
                "CLERK_ISSUER not set; ClerkAuthProvider cannot start"
            )
        # CLERK_AUDIENCE is intentionally OPTIONAL per Turn 3 plan v0.2 SR-1.
        # If unset, aud verification is skipped (see verify() below). Log a
        # one-time warning at construction so the choice is visible in ops.
        if not self._audience:
            import logging
            logging.getLogger(__name__).warning(
                "CLERK_AUDIENCE is not set — 'aud' claim verification will "
                "be SKIPPED. Signature, issuer, exp, and nbf remain enforced. "
                "Set CLERK_AUDIENCE for strict audience validation."
            )

        self._jwks_cache = jwks_cache or JWKSCache(url=self._jwks_url)

    async def verify(self, request: Request) -> VerifiedClaims:
        """Verify the Authorization header's Bearer token."""
        token = _extract_bearer_token(request)

        # Read the token header (unverified) to get the kid.
        try:
            headers = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthCredentialInvalid(
                f"Malformed JWT header: {exc}",
                details={"reason": "malformed_header"},
            ) from exc

        kid = headers.get("kid")
        if not kid:
            raise AuthCredentialInvalid(
                "JWT header missing 'kid' — cannot select verification key",
                details={"reason": "missing_kid"},
            )

        # Resolve JWK from cache — may force a refresh if kid is unknown.
        try:
            jwk = await self._jwks_cache.get_key(kid)
        except KeyError as exc:
            raise AuthCredentialInvalid(
                f"Unknown signing key (kid={kid})",
                details={"reason": "unknown_kid"},
            ) from exc
        except Exception as exc:
            # JWKS fetch failure — provider misconfig, not a client error.
            raise AuthProviderMisconfigured(
                f"JWKS fetch failed: {exc}",
                details={"reason": "jwks_unreachable"},
            ) from exc

        # Decode + verify signature, iss, exp, nbf. Audience verification
        # is conditional on CLERK_AUDIENCE being configured (Turn 3 plan
        # v0.2 SR-1): when unset, verify_aud=False and no 'aud' is passed
        # to jwt.decode. This accommodates Clerk template variance while
        # keeping signature/issuer/expiry enforcement unconditional.
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)

        required_claims = ["exp", "iat", "iss", "sub"]
        decode_options = {
            "require": required_claims,
            "verify_signature": True,
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iat": True,
            "verify_iss": True,
            "verify_aud": False,
        }
        decode_kwargs: dict[str, Any] = {
            "key": public_key,
            "algorithms": _ALLOWED_ALGORITHMS,
            "issuer": self._issuer,
            "leeway": _CLOCK_SKEW_LEEWAY,
            "options": decode_options,
        }

        if self._audience is not None:
            # Audience is configured → strict verification
            decode_options["verify_aud"] = True
            decode_options["require"] = required_claims + ["aud"]
            decode_kwargs["audience"] = self._audience

        try:
            claims = jwt.decode(token, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthCredentialInvalid(
                "Token expired",
                details={"reason": "expired"},
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthCredentialInvalid(
                "Invalid issuer",
                details={"reason": "invalid_iss"},
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthCredentialInvalid(
                "Invalid audience",
                details={"reason": "invalid_aud"},
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise AuthCredentialInvalid(
                f"Token verification failed: {exc}",
                details={"reason": "invalid_signature"},
            ) from exc

        # Translate raw claims into VerifiedClaims shape. Clerk-specific
        # claim names: `sub` for user, `org_id` for org, `org.role` for
        # role. Workspace id is non-standard; we read `workspace_id` if
        # present.
        org_id = claims.get("org_id")
        if not org_id:
            raise AuthCredentialInvalid(
                "Token missing required 'org_id' claim",
                details={"reason": "missing_org_id"},
            )

        return VerifiedClaims(
            user_id=claims["sub"],
            org_id=org_id,
            email=claims.get("email"),
            display_name=claims.get("name"),
            provider_org_role=_extract_org_role(claims),
            workspace_id=claims.get("workspace_id"),
        )


def _extract_bearer_token(request: Request) -> str:
    """Pull the token out of the Authorization header.

    Raises AuthCredentialMissing if the header is absent, or
    AuthCredentialInvalid if it's present but malformed.
    """
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth:
        raise AuthCredentialMissing(
            "Missing Authorization header",
            details={"reason": "no_authorization_header"},
        )

    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthCredentialInvalid(
            "Malformed Authorization header; expected 'Bearer <token>'",
            details={"reason": "malformed_authorization_header"},
        )

    token = parts[1].strip()
    if not token:
        raise AuthCredentialInvalid(
            "Empty bearer token",
            details={"reason": "empty_token"},
        )
    return token


def _extract_org_role(claims: dict[str, Any]) -> str | None:
    """Pull the org role claim. Clerk puts it at `org.role` or `org_role`
    depending on session template configuration; handle both."""
    # Nested shape: { "org": { "role": "admin", ... } }
    org = claims.get("org")
    if isinstance(org, dict):
        role = org.get("role")
        if role:
            return role
    # Flat shape: { "org_role": "admin" }
    flat = claims.get("org_role")
    if flat:
        return flat
    return None
