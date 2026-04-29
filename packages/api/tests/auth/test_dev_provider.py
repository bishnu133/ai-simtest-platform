"""DevAuthProvider tests (v0.4 §6.3) — 3 tests.

  1. Happy path: X-Dev-User-Id header → VerifiedClaims
  2. Missing header → AuthCredentialMissing (401)
  3. ENVIRONMENT=production → refuses to construct

No network, no DB, no real FastAPI app. We build minimal `Request`
objects via Starlette's test helper."""
from __future__ import annotations

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from src.api.errors import AuthCredentialMissing, AuthProviderMisconfigured
from src.auth.dev_provider import (
    DEFAULT_DEV_ORG_ID,
    HEADER_ORG_ID,
    HEADER_ROLE,
    HEADER_USER_ID,
    HEADER_WORKSPACE_ID,
    DevAuthProvider,
)


def _make_request(headers: dict[str, str]) -> Request:
    """Build a Starlette Request with the given headers. Minimal scope
    shape that Starlette's Request class will accept."""
    raw_headers = [
        (key.lower().encode(), value.encode())
        for key, value in headers.items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
    }
    return Request(scope)


async def test_dev_user_id_header_returns_claims() -> None:
    """Happy path — X-Dev-User-Id (plus optional headers) yields
    VerifiedClaims with sensible defaults where optional fields are
    absent."""
    provider = DevAuthProvider(environment="development")

    # Bare user_id only — defaults fill in the rest
    request = _make_request({HEADER_USER_ID: "dev_actor_1"})
    claims = await provider.verify(request)
    assert claims.user_id == "dev_actor_1"
    assert claims.org_id == DEFAULT_DEV_ORG_ID
    assert claims.provider_org_role is None
    assert claims.workspace_id is None

    # With all headers set, all values propagate
    request = _make_request({
        HEADER_USER_ID: "alice",
        HEADER_ORG_ID: "org_abc",
        HEADER_ROLE: "admin",
        HEADER_WORKSPACE_ID: "ws_123",
    })
    claims = await provider.verify(request)
    assert claims.user_id == "alice"
    assert claims.org_id == "org_abc"
    assert claims.provider_org_role == "admin"
    assert claims.workspace_id == "ws_123"


async def test_missing_dev_header_raises_401() -> None:
    """No X-Dev-User-Id → AuthCredentialMissing.

    No silent default user, no anonymous fallback. The provider fails
    closed even in dev, matching the fail-closed stance of the rest
    of the auth stack."""
    provider = DevAuthProvider(environment="development")

    # No headers at all
    request = _make_request({})
    with pytest.raises(AuthCredentialMissing) as exc_info:
        await provider.verify(request)
    assert exc_info.value.http_status == 401
    assert exc_info.value.details.get("reason") == "no_dev_user_id_header"

    # Other dev headers present but X-Dev-User-Id missing — still rejected
    request = _make_request({
        HEADER_ORG_ID: "org_abc",
        HEADER_ROLE: "admin",
    })
    with pytest.raises(AuthCredentialMissing):
        await provider.verify(request)


def test_dev_provider_refuses_prod_env() -> None:
    """Construction MUST fail under ENVIRONMENT=production or
    =staging.

    This is the critical safety guardrail: DevAuthProvider cannot be
    active in a production process by accident. The check happens at
    construction time, so the error surfaces at app boot, not on the
    first request."""
    # Production — blocked
    with pytest.raises(AuthProviderMisconfigured) as exc_info:
        DevAuthProvider(environment="production")
    assert exc_info.value.http_status == 500
    assert exc_info.value.details.get("environment") == "production"

    # Staging — blocked
    with pytest.raises(AuthProviderMisconfigured):
        DevAuthProvider(environment="staging")

    # Empty env — blocked (don't default to permissive)
    with pytest.raises(AuthProviderMisconfigured):
        DevAuthProvider(environment="")

    # Unknown env — blocked (allowlist, not denylist)
    with pytest.raises(AuthProviderMisconfigured):
        DevAuthProvider(environment="canary")

    # Allowed envs — each one must construct without error
    for env in ["development", "test", "local"]:
        provider = DevAuthProvider(environment=env)
        assert provider is not None
