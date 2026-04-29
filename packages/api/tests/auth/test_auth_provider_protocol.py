"""AuthProvider Protocol + VerifiedClaims shape tests (v0.4 §6.1).

Three tests verifying:
  1. VerifiedClaims is frozen (immutable after construction).
  2. AuthProvider Protocol exposes exactly the `verify` method.
  3. VerifiedClaims rejects empty user_id.

These are cheap, non-DB, synchronous-enough-to-be-fast. They run first
on every CI run to catch accidental Protocol-surface drift.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from src.auth.provider import AuthProvider, VerifiedClaims


def test_verified_claims_is_frozen() -> None:
    """VerifiedClaims must be immutable once constructed.

    Freezing prevents bootstrap / middleware code from mutating the
    claims object and accidentally polluting a downstream audit event
    with a tweaked value."""
    claims = VerifiedClaims(user_id="u1", org_id="org_1")

    # model_config = ConfigDict(frozen=True) → Pydantic raises
    # ValidationError on any attribute set after construction.
    with pytest.raises(ValidationError):
        claims.user_id = "u2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        claims.provider_org_role = "admin"  # type: ignore[misc]


def test_auth_provider_protocol_shape() -> None:
    """AuthProvider is a Protocol with exactly one async `verify` method.

    Any provider implementation MUST expose `async def verify(request)
    -> VerifiedClaims`. This test pins that surface so a refactor can't
    quietly add/remove methods without a corresponding plan update.
    """
    # Get the Protocol's declared methods (excluding dunder + inherited)
    proto_methods = {
        name
        for name, value in inspect.getmembers(AuthProvider)
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert proto_methods == {"verify"}, (
        f"AuthProvider Protocol surface changed. Expected only 'verify', "
        f"got {proto_methods}."
    )

    # Confirm verify is async
    verify_method = AuthProvider.verify
    assert inspect.iscoroutinefunction(verify_method), (
        "AuthProvider.verify must be async — implementations need to "
        "await network/DB calls for JWKS, introspection, etc."
    )


def test_verified_claims_rejects_empty_user_id() -> None:
    """user_id must be a non-empty, non-whitespace string.

    Bootstrap uses user_id as the memberships.user_id foreign key; an
    empty or whitespace-only value would create unowned memberships
    rows. Fail-fast at the claims boundary.
    """
    # Empty string — rejected by min_length=1
    with pytest.raises(ValidationError):
        VerifiedClaims(user_id="", org_id="org_1")

    # Whitespace only — rejected by _strip_and_non_empty validator
    with pytest.raises(ValidationError):
        VerifiedClaims(user_id="   ", org_id="org_1")

    # Whitespace around valid content is stripped, not rejected —
    # Clerk sometimes includes trailing whitespace in subject claims.
    claims = VerifiedClaims(user_id="  user_123  ", org_id="org_1")
    assert claims.user_id == "user_123"

    # Same validation applies to org_id
    with pytest.raises(ValidationError):
        VerifiedClaims(user_id="u1", org_id="")
    with pytest.raises(ValidationError):
        VerifiedClaims(user_id="u1", org_id="   ")
