"""Authentication + authorization surface (Turn 3).

Submodules:
  provider       — AuthProvider Protocol + VerifiedClaims
  clerk_provider — Clerk (RS256 + JWKS) implementation
  dev_provider   — Dev header-based implementation (local dev only)
  jwks_cache     — TTL + forced-refresh JWKS cache
  bootstrap      — ensure_tenant_and_workspace + ensure_membership
  role_mapping   — provider_org_role → internal Role translation
  middleware     — TenantContextMiddleware
  authz          — require_role_or_creator and other authz helpers

Consumers should import from submodules directly:

    from src.auth.provider import AuthProvider, VerifiedClaims
    from src.auth.clerk_provider import ClerkAuthProvider
"""
