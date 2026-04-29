"""ClerkAuthProvider tests (v0.4 §6.2) — 5 tests.

  1. Valid RS256 token → VerifiedClaims
  2. Invalid signature → 401
  3. Expired token → 401
  4. Production guardrail on dev provider composition
  5. Missing CLERK_AUDIENCE env → aud verification skipped (v0.4 SR-1)

Each test generates a fresh RSA keypair in-test, signs tokens with
the private key, and wires the corresponding JWK into a mock JWKS
cache. No real network, no real JWKS endpoint.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request

from src.api.errors import (
    AuthCredentialInvalid,
    AuthProviderMisconfigured,
)
from src.auth.clerk_provider import ClerkAuthProvider
from src.auth.jwks_cache import JWKSCache


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _generate_keypair() -> tuple[Any, str]:
    """Generate an RSA keypair for signing test tokens.

    Returns:
      (private_key_pem_bytes, jwk_dict)

    The private PEM is what `jwt.encode` wants; the JWK is what the
    JWKS cache returns. They represent the same key.
    """
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Derive the JWK from the public part
    pub_numbers = priv.public_key().public_numbers()

    def _b64url_uint(x: int) -> str:
        raw = x.to_bytes((x.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "test-kid",
        "n": _b64url_uint(pub_numbers.n),
        "e": _b64url_uint(pub_numbers.e),
    }
    return priv_pem, jwk


def _sign_token(
    priv_pem: bytes,
    *,
    kid: str = "test-kid",
    sub: str = "user_123",
    org_id: str = "org_abc",
    iss: str = "https://test.clerk.com",
    aud: str | None = "test-aud",
    exp: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a JWT signed with the given private key."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "org_id": org_id,
        "iss": iss,
        "iat": now,
        "exp": exp if exp is not None else now + 3600,
    }
    if aud is not None:
        payload["aud"] = aud
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        priv_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


class _FakeJWKSCache:
    """Stand-in for JWKSCache that returns a single pre-configured JWK."""

    def __init__(self, jwk: dict[str, Any]) -> None:
        self._jwk = jwk

    async def get_key(self, kid: str) -> dict[str, Any]:
        if kid != self._jwk["kid"]:
            raise KeyError(f"kid {kid} not in fake cache")
        return self._jwk


def _make_request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": raw}
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_valid_rs256_token_returns_claims() -> None:
    """Happy path — properly-signed token with all required claims
    round-trips through verify() into VerifiedClaims."""
    priv_pem, jwk = _generate_keypair()
    provider = ClerkAuthProvider(
        jwks_url="https://example.com/jwks",
        issuer="https://test.clerk.com",
        audience="test-aud",
        environment="test",
        jwks_cache=_FakeJWKSCache(jwk),
    )

    token = _sign_token(
        priv_pem,
        sub="user_123",
        org_id="org_abc",
        extra_claims={
            "email": "alice@example.com",
            "name": "Alice",
            "org": {"role": "admin"},
        },
    )
    request = _make_request({"Authorization": f"Bearer {token}"})
    claims = await provider.verify(request)

    assert claims.user_id == "user_123"
    assert claims.org_id == "org_abc"
    assert claims.email == "alice@example.com"
    assert claims.display_name == "Alice"
    assert claims.provider_org_role == "admin"


async def test_invalid_signature_raises_401() -> None:
    """A token signed with the WRONG key must be rejected.

    The attacker-model scenario: someone intercepts the JWKS URL,
    signs a forged token with a different keypair. Verification must
    fail because the JWK served from our (trusted) cache doesn't
    match the signing key."""
    _real_priv, real_jwk = _generate_keypair()
    attacker_priv, _ = _generate_keypair()

    provider = ClerkAuthProvider(
        jwks_url="https://example.com/jwks",
        issuer="https://test.clerk.com",
        audience="test-aud",
        environment="test",
        jwks_cache=_FakeJWKSCache(real_jwk),
    )

    # Sign with the attacker's key but claim the real key's kid
    forged = _sign_token(attacker_priv, kid=real_jwk["kid"])
    request = _make_request({"Authorization": f"Bearer {forged}"})

    with pytest.raises(AuthCredentialInvalid) as exc_info:
        await provider.verify(request)
    assert exc_info.value.http_status == 401
    assert exc_info.value.details.get("reason") == "invalid_signature"


async def test_expired_token_raises_401() -> None:
    """A token whose `exp` has already passed must be rejected.

    PyJWT raises ExpiredSignatureError; provider translates to 401
    with reason='expired'. The 30-second clock leeway is NOT enough
    to rescue a token that's 10 minutes old."""
    priv_pem, jwk = _generate_keypair()
    provider = ClerkAuthProvider(
        jwks_url="https://example.com/jwks",
        issuer="https://test.clerk.com",
        audience="test-aud",
        environment="test",
        jwks_cache=_FakeJWKSCache(jwk),
    )

    # Token expired 600 seconds ago (well outside the 30s leeway)
    now = int(time.time())
    expired = _sign_token(priv_pem, exp=now - 600)
    request = _make_request({"Authorization": f"Bearer {expired}"})

    with pytest.raises(AuthCredentialInvalid) as exc_info:
        await provider.verify(request)
    assert exc_info.value.http_status == 401
    assert exc_info.value.details.get("reason") == "expired"


def test_production_guardrail_refuses_dev_mode() -> None:
    """Booting a process with AUTH_PROVIDER=dev and ENVIRONMENT=production
    must fail at DevAuthProvider construction time.

    This is the guardrail that prevents the dev provider from ever
    being active in a real environment. Test lives here (not
    test_dev_provider.py only) because it's the cross-provider safety
    story: Clerk is the production path, Dev refuses prod, and a
    misconfigured env causes a hard boot failure rather than a silent
    security downgrade."""
    # Import here rather than at module top — keeps the dev provider
    # dependency localized to this test
    from src.auth.dev_provider import DevAuthProvider

    with pytest.raises(AuthProviderMisconfigured) as exc_info:
        DevAuthProvider(environment="production")
    assert exc_info.value.details.get("environment") == "production"

    # Conversely: ClerkAuthProvider can run in production (it's built
    # for it). Just confirm construction doesn't refuse.
    provider = ClerkAuthProvider(
        jwks_url="https://example.com/jwks",
        issuer="https://test.clerk.com",
        audience="test-aud",
        environment="production",
    )
    assert provider is not None


async def test_missing_audience_env_skips_aud_verification() -> None:
    """v0.4 SR-1: if CLERK_AUDIENCE is not configured, aud verification
    is skipped while signature, iss, exp, nbf remain enforced.

    Scenario:
      * Provider constructed with audience=None (env var unset)
      * Token minted WITHOUT an aud claim at all
      * Token still verifies — because aud is not enforced
      * Signature/iss/exp still validated normally (we prove this by
        minting a second token with a WRONG issuer and confirming it
        still fails)
    """
    priv_pem, jwk = _generate_keypair()
    provider = ClerkAuthProvider(
        jwks_url="https://example.com/jwks",
        issuer="https://test.clerk.com",
        audience=None,  # explicitly unset
        environment="test",
        jwks_cache=_FakeJWKSCache(jwk),
    )

    # Token with NO aud — should verify successfully
    token = _sign_token(priv_pem, aud=None)
    request = _make_request({"Authorization": f"Bearer {token}"})
    claims = await provider.verify(request)
    assert claims.user_id == "user_123"

    # Sanity check: signature/iss/exp verification still fires even
    # with audience disabled. Wrong issuer → 401.
    bad_iss_token = _sign_token(priv_pem, aud=None, iss="https://evil.com")
    request = _make_request({"Authorization": f"Bearer {bad_iss_token}"})
    with pytest.raises(AuthCredentialInvalid) as exc_info:
        await provider.verify(request)
    assert exc_info.value.details.get("reason") == "invalid_iss"
