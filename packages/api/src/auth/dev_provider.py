"""DevAuthProvider — local development only (Turn 3 plan §5).

Trusts a simple `X-Dev-User-Id` header. Refuses to run under
`ENVIRONMENT=production` or `ENVIRONMENT=staging` — there is no way
to configure this provider into a non-dev environment.

Claims shape:
  user_id          = X-Dev-User-Id header value (required)
  org_id           = X-Dev-Org-Id header (optional; defaults to __dev__)
  provider_org_role = X-Dev-Role header (optional)
  workspace_id     = X-Dev-Workspace-Id header (optional)

The defaults align with `scripts/seed_dev_tenant.py`, which seeds a
tenant slugged `__dev__` with a default workspace and owner-role
memberships for `dev_actor_1`. A bare `X-Dev-User-Id: dev_actor_1`
header is sufficient to authenticate locally without any other config.

Production-safety guardrail:
  * Checked at construction time (boot), NOT at first-request time.
    An app booting with `AUTH_PROVIDER=dev` and
    `ENVIRONMENT=production` raises AuthProviderMisconfigured before
    the app can serve a single request.
  * No runtime override. No "bypass in debug mode". No magic flag.
"""
from __future__ import annotations

import logging
import os
from typing import ClassVar

from fastapi import Request

from src.api.errors import (
    AuthCredentialInvalid,
    AuthCredentialMissing,
    AuthProviderMisconfigured,
)
from src.auth.provider import VerifiedClaims

__all__ = ["DevAuthProvider"]

logger = logging.getLogger(__name__)


# Environments under which DevAuthProvider is permitted. Anything else
# (production, staging, unknown, empty) fails-closed at construction.
_ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "local"})

# Header names. Kept short and X-prefixed per long-standing convention
# for non-standard headers.
HEADER_USER_ID = "X-Dev-User-Id"
HEADER_ORG_ID = "X-Dev-Org-Id"
HEADER_ROLE = "X-Dev-Role"
HEADER_WORKSPACE_ID = "X-Dev-Workspace-Id"

# Default org id when the X-Dev-Org-Id header is absent. Matches the
# seed script's __dev__ tenant slug so a bare user-id header works
# against a freshly-seeded local DB.
DEFAULT_DEV_ORG_ID = "__dev__"


class DevAuthProvider:
    """Header-based auth for local development.

    Usage in main.py::

        if os.getenv("AUTH_PROVIDER") == "dev":
            provider = DevAuthProvider()  # raises if ENVIRONMENT=production
        else:
            provider = ClerkAuthProvider()
    """

    # Stable identifier emitted on auth.accepted / auth.rejected audit
    # events so log analysis can attribute traffic to a provider without
    # having to inspect the runtime class name. Hotfix Turn 3 Hotfix 2.
    provider_name: ClassVar[str] = "dev"

    def __init__(self, *, environment: str | None = None) -> None:
        """
        environment: explicit override for tests. Production uses env var.
        """
        env = environment if environment is not None else os.getenv(
            "ENVIRONMENT", ""
        )
        env_normalized = env.strip().lower()

        if env_normalized not in _ALLOWED_ENVIRONMENTS:
            raise AuthProviderMisconfigured(
                "DevAuthProvider refuses to run under "
                f"ENVIRONMENT={env!r}. Allowed environments: "
                f"{sorted(_ALLOWED_ENVIRONMENTS)}. "
                "Use ClerkAuthProvider (or another real provider) in "
                "production and staging.",
                details={"environment": env, "provider": "dev"},
            )

        self._environment = env_normalized
        logger.info(
            "DevAuthProvider enabled (ENVIRONMENT=%s). "
            "This provider accepts unauthenticated header-based "
            "identity — never enable in production.",
            env_normalized,
        )

    async def verify(self, request: Request) -> VerifiedClaims:
        """Extract dev identity from request headers."""
        user_id = request.headers.get(HEADER_USER_ID)
        if not user_id:
            # Missing the required header → 401 (same shape as Clerk's
            # missing-Authorization case). Callers differentiate "no
            # credentials" from "bad credentials" via the exception type.
            raise AuthCredentialMissing(
                f"Missing {HEADER_USER_ID} header",
                details={"reason": "no_dev_user_id_header"},
            )

        user_id = user_id.strip()
        if not user_id:
            raise AuthCredentialInvalid(
                f"Empty {HEADER_USER_ID} header",
                details={"reason": "empty_dev_user_id"},
            )

        # Optional headers — fall through to defaults. No deny-paths
        # here; the bootstrap layer handles missing-role via its
        # provider_org_role mapping (fail-closed only on genuinely-new
        # users per v0.4 §5.6).
        org_id = (
            request.headers.get(HEADER_ORG_ID, "").strip()
            or DEFAULT_DEV_ORG_ID
        )
        provider_org_role = (
            request.headers.get(HEADER_ROLE, "").strip() or None
        )
        workspace_id = (
            request.headers.get(HEADER_WORKSPACE_ID, "").strip() or None
        )

        return VerifiedClaims(
            user_id=user_id,
            org_id=org_id,
            provider_org_role=provider_org_role,
            workspace_id=workspace_id,
        )
