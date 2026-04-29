"""Tests for TenantContextMiddleware — the Turn 3 §6.3 pipeline.

Eight tests cover the five-step pipeline:

    1. extract_bearer fails             -> 401 missing_bearer_token
    2. malformed bearer                 -> 401 missing_bearer_token
    3. provider.verify fails            -> 401 + audit.rejected
    4. missing provider_org_role (new user) -> 403 + audit.membership_denied
    5. happy path (first user)          -> 200 + ctx attached + audit.accepted
    6. returning user with no role      -> still 200 via fast-path
    7. exempt path                      -> 200, middleware short-circuits
    8. tenant_context attached to state -> verified via probe route

Test infrastructure mirrors tests/auth/test_bootstrap_lifecycle.py:
  * `clean_db` fixture (from tests/db/conftest.py) TRUNCATEs between tests
  * `get_sessionmaker()` provides a live sessionmaker bound to the test DB

Provider is stubbed because real JWT verification is covered by
`tests/auth/test_clerk_provider.py` from Session 1.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.errors import AuthCredentialInvalid
from src.audit.logger import AuditActions, audit_logger
from src.auth.bootstrap import bootstrap
from src.auth.middleware import TenantContextMiddleware
from src.auth.provider import VerifiedClaims
from src.db.session import get_sessionmaker

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test stub — scripted AuthProvider
# ---------------------------------------------------------------------------


class StubAuthProvider:
    """AuthProvider stub whose `verify` behavior is configurable per-test.

    Hotfix Turn 3 Hotfix 2: signature aligned with AuthProvider Protocol
    (`verify(request: Request)` rather than `verify(token: str)`). The
    stub body still ignores the argument — it returns scripted claims
    or raises a scripted exception — so behavior is unchanged for the
    11 existing middleware tests.
    """

    provider_name = "stub"

    def __init__(
        self, claims: VerifiedClaims | None = None, exc: Exception | None = None
    ):
        self._claims = claims
        self._exc = exc

    async def verify(self, request: Request) -> VerifiedClaims:
        if self._exc is not None:
            raise self._exc
        assert self._claims is not None, "stub needs either claims or exc"
        return self._claims


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(provider, sessionmaker, *, exempt_paths=()):
    """Build a minimal FastAPI app with the middleware and one probe route."""
    app = FastAPI()
    app.add_middleware(
        TenantContextMiddleware,
        provider=provider,
        sessionmaker=sessionmaker,
        exempt_paths=exempt_paths,
    )

    @app.get("/probe")
    async def probe(request: Request):
        ctx = request.state.tenant_context
        return {
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.workspace_id,
            "actor_id": ctx.actor.actor_id,
            "role": getattr(ctx, "role", None),
        }

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path, headers=headers or {})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_missing_bearer_returns_401_missing_bearer_token(
    clean_db: str,
) -> None:
    """Step 1 failure — no Authorization header."""
    audit_logger.clear()
    sm = get_sessionmaker()
    provider = StubAuthProvider(exc=AuthCredentialInvalid("should not be called"))
    app = _make_app(provider, sm)

    resp = await _get(app, "/probe")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "missing_bearer_token"
    events = audit_logger.query(action=AuditActions.AUTH_REJECTED)
    assert len(events) == 1
    assert events[0].metadata.get("reason") == "missing_bearer_token"


async def test_malformed_bearer_returns_401_missing_bearer_token(
    clean_db: str,
) -> None:
    """Step 1 failure — header present but not Bearer-shaped."""
    audit_logger.clear()
    sm = get_sessionmaker()
    provider = StubAuthProvider(exc=AuthCredentialInvalid("should not be called"))
    app = _make_app(provider, sm)

    resp = await _get(app, "/probe", headers={"Authorization": "NotBearer xyz"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "missing_bearer_token"


async def test_provider_verify_failure_returns_401_and_audits_rejection(
    clean_db: str,
) -> None:
    """Step 2 failure — verify raises AuthCredentialInvalid."""
    audit_logger.clear()
    sm = get_sessionmaker()
    provider = StubAuthProvider(
        exc=AuthCredentialInvalid(
            "bad signature", details={"reason": "sig_invalid"}
        )
    )
    app = _make_app(provider, sm)

    resp = await _get(app, "/probe", headers={"Authorization": "Bearer any"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_credential_invalid"
    events = audit_logger.query(action=AuditActions.AUTH_REJECTED)
    assert len(events) == 1
    assert events[0].metadata.get("step") == "provider.verify"


async def test_missing_provider_org_role_returns_403_membership_denied(
    clean_db: str,
) -> None:
    """Step 4 failure — token valid, no provider_org_role claim for new user.

    Tenant is seeded first so the incoming user is NOT the first.
    That path requires a valid role claim, and missing fails closed
    per v0.5.1 MF-2.
    """
    audit_logger.clear()
    sm = get_sessionmaker()

    # Seed: first user establishes the tenant as owner
    seed = VerifiedClaims(
        user_id="user_seed",
        org_id="org_denyroletest",
        provider_org_role="admin",
        display_name="Seed",
    )
    async with sm() as s:
        await bootstrap(s, seed)

    audit_logger.clear()  # discard seeding events; focus on middleware call

    # Second user arrives with NO role claim
    second = VerifiedClaims(
        user_id="user_second",
        org_id="org_denyroletest",
        provider_org_role=None,
    )
    app = _make_app(StubAuthProvider(claims=second), sm)

    resp = await _get(app, "/probe", headers={"Authorization": "Bearer any"})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "missing_provider_org_role"
    denied = audit_logger.query(action=AuditActions.AUTH_MEMBERSHIP_DENIED)
    assert len(denied) >= 1


async def test_happy_path_first_user_attaches_context_and_audits_accept(
    clean_db: str,
) -> None:
    """Step 5 success — first-user path bootstraps as owner, emits auth.accepted."""
    audit_logger.clear()
    sm = get_sessionmaker()

    claims = VerifiedClaims(
        user_id="user_happyfirst",
        org_id="org_happypath",
        display_name="Happy First",
    )
    app = _make_app(StubAuthProvider(claims=claims), sm)

    resp = await _get(app, "/probe", headers={"Authorization": "Bearer any"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["actor_id"] == "user_happyfirst"
    assert body["role"] == "owner", "first user must be bootstrapped as owner"
    assert body["tenant_id"]
    assert body["workspace_id"]

    accepted = audit_logger.query(action=AuditActions.AUTH_ACCEPTED)
    assert len(accepted) == 1
    assert accepted[0].metadata.get("is_first_user") is True
    assert accepted[0].metadata.get("role") == "owner"


async def test_happy_path_returning_user_fast_path_skips_role_mapping(
    clean_db: str,
) -> None:
    """A returning user's role comes from existing membership (fast-path).

    Even when provider_org_role is missing on the second request —
    proves the v0.4 §5.6 fast-path.
    """
    audit_logger.clear()
    sm = get_sessionmaker()

    # First login establishes user + dual membership as owner (first-user rule)
    first = VerifiedClaims(
        user_id="user_return",
        org_id="org_returnpath",
        provider_org_role="admin",
    )
    async with sm() as s:
        await bootstrap(s, first)

    audit_logger.clear()

    # Second login — provider_org_role dropped, should still succeed
    second = VerifiedClaims(
        user_id="user_return",
        org_id="org_returnpath",
        provider_org_role=None,  # deliberately stripped
    )
    app = _make_app(StubAuthProvider(claims=second), sm)

    resp = await _get(app, "/probe", headers={"Authorization": "Bearer any"})

    assert resp.status_code == 200
    assert resp.json()["role"] == "owner"
    accepted = audit_logger.query(action=AuditActions.AUTH_ACCEPTED)
    assert len(accepted) == 1
    assert accepted[0].metadata.get("is_first_user") is False


async def test_exempt_path_skips_middleware(clean_db: str) -> None:
    """/health bypasses the pipeline entirely — no bearer required, no audit."""
    audit_logger.clear()
    sm = get_sessionmaker()
    provider = StubAuthProvider(exc=AuthCredentialInvalid("should not be called"))
    app = _make_app(provider, sm, exempt_paths=("/health",))

    resp = await _get(app, "/health")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert audit_logger.query(action=AuditActions.AUTH_REJECTED) == []
    assert audit_logger.query(action=AuditActions.AUTH_ACCEPTED) == []


async def test_tenant_context_attached_to_request_state(clean_db: str) -> None:
    """Confirms middleware attaches ctx at request.state.tenant_context."""
    audit_logger.clear()
    sm = get_sessionmaker()

    claims = VerifiedClaims(
        user_id="user_statetest",
        org_id="org_statetest",
        display_name="State Test",
        email="state@test.example",
    )
    app = _make_app(StubAuthProvider(claims=claims), sm)

    resp = await _get(app, "/probe", headers={"Authorization": "Bearer any"})

    assert resp.status_code == 200
    body = resp.json()
    # If state wasn't set, the probe would raise AttributeError and 500
    assert body["tenant_id"]
    assert body["workspace_id"]
    assert body["actor_id"] == "user_statetest"
    assert body["role"] == "owner"
