"""Provider-role → internal-role mapping (Turn 3 plan §6.5).

Maps the raw `provider_org_role` string from VerifiedClaims to one
of the internal Role literals (`viewer | member | admin | owner |
service_account`). Fail-closed per plan v0.5.1 MF-2: missing or
unknown claims raise a 403 exception, never a silent viewer
fallback.

Mapping table uses case-insensitive comparison because Clerk
session-token templates sometimes return title-case strings
(`"Admin"` vs `"admin"`). The INTERNAL Role values remain
lowercase — we normalize on the way in.

Where this is called from:
  - `ensure_membership` (src/auth/bootstrap.py) — ONLY on the
    no-memberships path (genuinely new user). Existing members
    bypass this via the precedence fast-path (plan v0.4 §5.6).

The mapping deliberately does NOT accept `service_account` as a
valid provider_org_role. Service-account auth is a separate
provider (FH F-series) with a different VerifiedClaims
construction path.
"""
from __future__ import annotations

from typing import Literal

from src.api.errors import (
    MissingProviderOrgRole,
    UnknownProviderOrgRole,
)
from src.auth.provider import VerifiedClaims

# Re-export the Role type from deps.py to keep the single source of
# truth (extended to 5 roles in Turn 3 Step 0).
MappedRole = Literal["viewer", "member", "admin", "owner"]

__all__ = ["map_provider_org_role", "PROVIDER_ROLE_MAP"]


# Mapping table. Keys are lowercased for case-insensitive lookup.
# Values are internal MappedRole literals. Intentionally does NOT
# include 'service_account' — that's a separate auth path.
#
# Clerk's default session-template `org.role` values are:
#   "org:admin"  → admin   (Clerk "Admin" role in org)
#   "org:member" → member  (Clerk "Member" role in org)
# Plus we accept the bare names in case the template strips the
# prefix. Also accept `owner` if Clerk is configured with a custom
# owner role.
PROVIDER_ROLE_MAP: dict[str, MappedRole] = {
    # Clerk-prefixed values
    "org:admin": "admin",
    "org:member": "member",
    "org:viewer": "viewer",
    "org:owner": "owner",
    # Bare values
    "admin": "admin",
    "member": "member",
    "viewer": "viewer",
    "owner": "owner",
    # Common synonyms some providers use
    "basic_member": "member",
    "guest": "viewer",
}


def map_provider_org_role(claims: VerifiedClaims) -> MappedRole:
    """Translate claims.provider_org_role to an internal Role.

    Fail-closed contract:
      * Missing (None or empty after strip) → MissingProviderOrgRole (403)
      * Present but not in PROVIDER_ROLE_MAP → UnknownProviderOrgRole (403)
      * Present and mappable → returns the MappedRole

    Both exception paths include the offending value in `details` so
    ops can diagnose misconfigured session templates quickly.
    """
    raw = claims.provider_org_role
    if raw is None or not raw.strip():
        raise MissingProviderOrgRole(
            "Token missing 'provider_org_role' claim. "
            "Confirm the Clerk session template includes {{org.role}} "
            "(or equivalent).",
            details={
                "user_id": claims.user_id,
                "org_id": claims.org_id,
                "provider_org_role": raw,
            },
        )

    normalized = raw.strip().lower()
    mapped = PROVIDER_ROLE_MAP.get(normalized)
    if mapped is None:
        raise UnknownProviderOrgRole(
            f"Unknown provider_org_role value: {raw!r}. "
            f"Accepted values: {sorted(set(PROVIDER_ROLE_MAP.values()))}.",
            details={
                "user_id": claims.user_id,
                "org_id": claims.org_id,
                "provider_org_role": raw,
                "normalized": normalized,
            },
        )
    return mapped
