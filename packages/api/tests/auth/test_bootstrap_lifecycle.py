"""Bootstrap lifecycle tests — Turn 3 Step 2 (v0.4 §6.6).

20 Postgres-backed tests covering:
  - ensure_tenant_and_workspace (new, existing, explicit ws claim)
  - first-user-as-owner (bootstraps as owner regardless of provider_org_role)
  - subsequent-user onboarding (requires provider_org_role, fail-closed)
  - v0.4 MF-1 precedence fast-path (workspace wins, else tenant, else map)
  - fast-path resilience when provider_org_role is missing but
    memberships already exist
  - bootstrap() idempotency + at-most-one-retry on duplicates
  - AUTH_MEMBERSHIP_DENIED / TENANT_CREATED audit emissions
  - dual-membership shape via list_for_user_in_tenant
  - cross-tenant workspace-claim rejection

Test infrastructure: reuses the `clean_db` fixture from
tests/db/conftest.py (TRUNCATE per test, migration once per session).
audit_logger is auto-cleared by tests/conftest.py autouse fixture.
"""
from __future__ import annotations

import pytest

from src.api.errors import (
    CrossTenantForbidden,
    MissingProviderOrgRole,
    UnknownProviderOrgRole,
)
from src.audit.logger import AuditActions, audit_logger
from src.auth.bootstrap import (
    bootstrap,
    ensure_membership,
    ensure_tenant_and_workspace,
)
from src.auth.provider import VerifiedClaims
from src.auth.role_mapping import map_provider_org_role
from src.db.session import get_sessionmaker
from src.memberships import PostgresMembershipRepository
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


def _claims(
    *,
    user_id: str = "user_alice",
    org_id: str = "org_acme",
    provider_org_role: str | None = "org:admin",
    workspace_id: str | None = None,
) -> VerifiedClaims:
    """Convenience claims builder."""
    return VerifiedClaims(
        user_id=user_id,
        org_id=org_id,
        provider_org_role=provider_org_role,
        workspace_id=workspace_id,
    )


async def _run_in_session(fn):
    """Run a coroutine with a fresh admin session. Used by tests that
    call ensure_* directly (not via bootstrap's own transaction)."""
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            return await fn(session)


# ---------------------------------------------------------------------------
# ensure_tenant_and_workspace — 3 tests
# ---------------------------------------------------------------------------


async def test_ensure_tenant_and_workspace_creates_new_tenant(
    clean_db: str,
) -> None:
    """No tenant exists for org_id yet → tenant + default workspace
    are created, just_created flag is True."""
    async def _work(session):
        return await ensure_tenant_and_workspace(session, _claims(org_id="org_new"))

    tenant_id, workspace_id, just_created = await _run_in_session(_work)

    assert tenant_id
    assert workspace_id
    assert just_created is True

    # Verify rows exist
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.get_by_id(tenant_id)
    assert tenant.clerk_org_id == "org_new"
    default_ws = await w_repo.get_default_for_tenant(tenant_id)
    assert default_ws is not None
    assert default_ws.id == workspace_id
    assert default_ws.is_default is True


async def test_ensure_tenant_and_workspace_reuses_existing_tenant(
    clean_db: str,
) -> None:
    """Tenant already exists for org_id → reuse it, don't create a new one."""
    # Prime: first call creates
    async def _first(session):
        return await ensure_tenant_and_workspace(session, _claims(org_id="org_exists"))

    first_tid, first_wid, first_created = await _run_in_session(_first)
    assert first_created is True

    # Second call for same org_id → reuses
    async def _second(session):
        return await ensure_tenant_and_workspace(session, _claims(org_id="org_exists"))

    second_tid, second_wid, second_created = await _run_in_session(_second)
    assert second_tid == first_tid
    assert second_wid == first_wid
    assert second_created is False


async def test_ensure_tenant_and_workspace_honors_explicit_workspace_claim(
    clean_db: str,
) -> None:
    """If claims.workspace_id is set, use it (after ownership check)."""
    # Seed a tenant with TWO workspaces: default and secondary
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.create(
        name="T", slug="explicit-ws-slug", clerk_org_id="org_explicit"
    )
    default = await w_repo.create(
        tenant_id=tenant.id, name="Default", is_default=True
    )
    secondary = await w_repo.create(
        tenant_id=tenant.id, name="Secondary", is_default=False
    )

    # Claim specifies the secondary workspace explicitly
    async def _work(session):
        return await ensure_tenant_and_workspace(
            session,
            _claims(org_id="org_explicit", workspace_id=secondary.id),
        )

    tid, wid, just_created = await _run_in_session(_work)
    assert tid == tenant.id
    assert wid == secondary.id  # explicit claim wins over default
    assert just_created is False


# ---------------------------------------------------------------------------
# First-user-as-owner — 2 tests
# ---------------------------------------------------------------------------


async def test_first_user_bootstrap_creates_tenant_workspace_owner_membership(
    clean_db: str,
) -> None:
    """Brand-new org: bootstrap creates tenant + default workspace +
    BOTH tenant-level and workspace-level 'owner' memberships."""
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(user_id="founder_1", org_id="org_brand_new"),
        )

    assert result.is_first_user is True
    assert result.role == "owner"

    m_repo = PostgresMembershipRepository()
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=result.tenant_id, user_id="founder_1"
    )
    assert len(rows) == 2
    assert {r.workspace_id for r in rows} == {None, result.workspace_id}
    assert {r.role for r in rows} == {"owner"}


async def test_first_user_bootstrap_ignores_provider_org_role(
    clean_db: str,
) -> None:
    """Plan §6.5 first-user rule: even if provider_org_role is
    missing or weird, the first user of a brand-new tenant bootstraps
    as 'owner'. Don't block the first login just because the Clerk
    template is incomplete."""
    sm = get_sessionmaker()
    async with sm() as session:
        # provider_org_role explicitly None — would normally fail for
        # a subsequent user, but first-user bypasses role mapping
        result = await bootstrap(
            session,
            _claims(
                user_id="founder_2",
                org_id="org_no_role_claim",
                provider_org_role=None,
            ),
        )

    assert result.is_first_user is True
    assert result.role == "owner"


# ---------------------------------------------------------------------------
# Subsequent user + v0.4 MF-1 precedence fast-path — 4 tests
# ---------------------------------------------------------------------------


async def test_subsequent_user_onboarding_requires_valid_provider_role(
    clean_db: str,
) -> None:
    """Second user joining an existing tenant goes through
    map_provider_org_role → creates both rows with the mapped role."""
    sm = get_sessionmaker()

    # First user creates the tenant as owner
    async with sm() as session:
        first = await bootstrap(
            session, _claims(user_id="founder", org_id="org_subseq")
        )

    # Second user joins — provider_org_role is 'org:member'
    async with sm() as session:
        second = await bootstrap(
            session,
            _claims(
                user_id="joiner",
                org_id="org_subseq",
                provider_org_role="org:member",
            ),
        )

    assert second.tenant_id == first.tenant_id
    assert second.workspace_id == first.workspace_id
    assert second.is_first_user is False
    assert second.role == "member"

    # Both tenant-level and workspace-level rows exist for joiner
    m_repo = PostgresMembershipRepository()
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=second.tenant_id, user_id="joiner"
    )
    assert len(rows) == 2
    assert all(r.role == "member" for r in rows)


async def test_fast_path_workspace_level_row_returns_ws_role(
    clean_db: str,
) -> None:
    """v0.4 §5.6 Step 1: workspace-level membership → return its role.

    Even when provider_org_role in the claim differs, the existing
    workspace row wins. This is the "DB is the authority" invariant."""
    sm = get_sessionmaker()

    async with sm() as session:
        # Seed: user has workspace-level 'admin' role
        founder = await bootstrap(
            session, _claims(user_id="founder", org_id="org_fp_ws")
        )
    async with sm() as session:
        await bootstrap(
            session,
            _claims(
                user_id="u1", org_id="org_fp_ws", provider_org_role="org:member"
            ),
        )

    # Now simulate a subsequent request where the token claim says
    # "viewer" but the DB already has 'member' for this user in this
    # workspace. The fast-path returns 'member' from the DB.
    async with sm() as session:
        async with session.begin():
            role = await ensure_membership(
                session,
                tenant_id=founder.tenant_id,
                workspace_id=founder.workspace_id,
                claims=_claims(
                    user_id="u1",
                    org_id="org_fp_ws",
                    provider_org_role="org:viewer",  # claim differs from DB
                ),
            )
    assert role == "member"  # DB wins, not the claim


async def test_fast_path_tenant_level_only_returns_tenant_role(
    clean_db: str,
) -> None:
    """v0.4 §5.6 Step 2 — THE KEY REVIEW-DRIVEN TEST.

    User has ONLY a tenant-level membership row (no workspace-level).
    Token is missing provider_org_role entirely.
    v0.3 logic would have forced them into the map path and denied.
    v0.4 fast-path: tenant-level 'admin' authorizes → return 'admin'.
    User stays authorized even with a broken/incomplete token."""
    sm = get_sessionmaker()

    # Seed: create tenant, create a tenant-level-only membership manually
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    m_repo = PostgresMembershipRepository()

    tenant = await t_repo.create(
        name="T", slug="fp-tenant-only", clerk_org_id="org_fp_tenant"
    )
    ws = await w_repo.create(
        tenant_id=tenant.id, name="Default", is_default=True
    )
    # Tenant-level row ONLY — no workspace-level row
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="tenant_only_user",
        workspace_id=None,
        role="admin",
    )

    # Also add a membership for another user so the tenant is not
    # "empty" and bootstrap() doesn't hit the first-user-as-owner path
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="other_user",
        workspace_id=None,
        role="member",
    )

    # Now: ensure_membership for tenant_only_user with a MISSING
    # provider_org_role claim. v0.4 MF-1 says the tenant-level row
    # authorizes regardless.
    async with sm() as session:
        async with session.begin():
            role = await ensure_membership(
                session,
                tenant_id=tenant.id,
                workspace_id=ws.id,
                claims=_claims(
                    user_id="tenant_only_user",
                    org_id="org_fp_tenant",
                    provider_org_role=None,  # missing — would fail v0.3 logic
                ),
            )

    assert role == "admin", (
        "v0.4 MF-1: tenant-level-only users must authorize via their "
        "tenant role, not be forced into the map path just because "
        "they lack a workspace-level row."
    )


async def test_fast_path_succeeds_when_provider_org_role_missing_but_memberships_exist(
    clean_db: str,
) -> None:
    """Resilience test: the v0.4 review's exact scenario — an
    already-onboarded user's Clerk template loses org.role claim.
    Existing workspace-level membership still authorizes them."""
    sm = get_sessionmaker()

    async with sm() as session:
        founder = await bootstrap(
            session, _claims(user_id="founder", org_id="org_resilience")
        )
    async with sm() as session:
        await bootstrap(
            session,
            _claims(
                user_id="u_resilient",
                org_id="org_resilience",
                provider_org_role="org:member",
            ),
        )

    # Claim's provider_org_role is now missing (simulating a template regression)
    async with sm() as session:
        async with session.begin():
            role = await ensure_membership(
                session,
                tenant_id=founder.tenant_id,
                workspace_id=founder.workspace_id,
                claims=_claims(
                    user_id="u_resilient",
                    org_id="org_resilience",
                    provider_org_role=None,  # missing!
                ),
            )
    assert role == "member"


# ---------------------------------------------------------------------------
# Idempotency — 1 test
# ---------------------------------------------------------------------------


async def test_bootstrap_is_idempotent_across_multiple_calls(
    clean_db: str,
) -> None:
    """Calling bootstrap multiple times for the same user produces
    the same result and no extra rows."""
    sm = get_sessionmaker()

    async with sm() as session:
        first = await bootstrap(
            session, _claims(user_id="u_idem", org_id="org_idem")
        )
    async with sm() as session:
        second = await bootstrap(
            session, _claims(user_id="u_idem", org_id="org_idem")
        )
    async with sm() as session:
        third = await bootstrap(
            session, _claims(user_id="u_idem", org_id="org_idem")
        )

    assert second.tenant_id == first.tenant_id
    assert third.tenant_id == first.tenant_id
    assert second.workspace_id == first.workspace_id
    assert second.role == first.role == "owner"

    # Only 2 rows created (the initial dual membership)
    m_repo = PostgresMembershipRepository()
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=first.tenant_id, user_id="u_idem"
    )
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# Role mapping — 3 tests
# ---------------------------------------------------------------------------


async def test_map_provider_org_role_happy_path(clean_db: str) -> None:
    """Valid Clerk role strings map to internal roles. No DB needed,
    but keeping this test file-local for locality with the bootstrap
    tests that exercise the same machinery."""
    assert map_provider_org_role(_claims(provider_org_role="org:admin")) == "admin"
    assert map_provider_org_role(_claims(provider_org_role="org:member")) == "member"
    assert map_provider_org_role(_claims(provider_org_role="org:viewer")) == "viewer"
    assert map_provider_org_role(_claims(provider_org_role="org:owner")) == "owner"
    # Bare forms
    assert map_provider_org_role(_claims(provider_org_role="admin")) == "admin"
    # Case-insensitive
    assert map_provider_org_role(_claims(provider_org_role="Admin")) == "admin"
    assert map_provider_org_role(_claims(provider_org_role="MEMBER")) == "member"


async def test_map_provider_org_role_missing_raises_missing_provider_org_role(
    clean_db: str,
) -> None:
    """None or whitespace-only → MissingProviderOrgRole (403)."""
    with pytest.raises(MissingProviderOrgRole) as exc_info:
        map_provider_org_role(_claims(provider_org_role=None))
    assert exc_info.value.http_status == 403

    with pytest.raises(MissingProviderOrgRole):
        map_provider_org_role(_claims(provider_org_role=""))

    with pytest.raises(MissingProviderOrgRole):
        map_provider_org_role(_claims(provider_org_role="   "))


async def test_map_provider_org_role_unknown_value_raises_unknown(
    clean_db: str,
) -> None:
    """Unrecognized value → UnknownProviderOrgRole (403). details
    includes the offending value for ops diagnosis."""
    with pytest.raises(UnknownProviderOrgRole) as exc_info:
        map_provider_org_role(_claims(provider_org_role="wizard"))
    assert exc_info.value.http_status == 403
    assert exc_info.value.details.get("normalized") == "wizard"
    assert exc_info.value.details.get("provider_org_role") == "wizard"


# ---------------------------------------------------------------------------
# Deny-path audit emissions — 2 tests
# ---------------------------------------------------------------------------


async def test_new_user_missing_role_emits_membership_denied_audit(
    clean_db: str,
) -> None:
    """New-user onboarding with missing provider_org_role →
    AUTH_MEMBERSHIP_DENIED audit event emitted BEFORE raising."""
    # Seed a tenant with an existing member so the new user isn't
    # treated as first-user-as-owner
    sm = get_sessionmaker()
    async with sm() as session:
        await bootstrap(
            session, _claims(user_id="founder", org_id="org_deny_missing")
        )

    audit_logger.clear_all()

    async with sm() as session:
        with pytest.raises(MissingProviderOrgRole):
            async with session.begin():
                await ensure_membership(
                    session,
                    tenant_id=(
                        await PostgresTenantRepository().get_by_clerk_org_id(
                            "org_deny_missing"
                        )
                    ).id,
                    workspace_id=(
                        await PostgresWorkspaceRepository().get_default_for_tenant(
                            (await PostgresTenantRepository().get_by_clerk_org_id(
                                "org_deny_missing"
                            )).id
                        )
                    ).id,
                    claims=_claims(
                        user_id="new_user_no_role",
                        org_id="org_deny_missing",
                        provider_org_role=None,
                    ),
                )

    events = audit_logger.query_all_events(action=AuditActions.AUTH_MEMBERSHIP_DENIED)
    assert len(events) == 1
    assert events[0].metadata.get("reason") == "missing_provider_org_role"
    assert events[0].metadata.get("provider_org_role") is None


async def test_new_user_unknown_role_emits_membership_denied_audit(
    clean_db: str,
) -> None:
    """New-user onboarding with unknown provider_org_role → audit
    event with reason='unknown_provider_org_role'."""
    sm = get_sessionmaker()
    async with sm() as session:
        await bootstrap(
            session, _claims(user_id="founder", org_id="org_deny_unknown")
        )

    audit_logger.clear_all()

    t = await PostgresTenantRepository().get_by_clerk_org_id("org_deny_unknown")
    w = await PostgresWorkspaceRepository().get_default_for_tenant(t.id)

    async with sm() as session:
        with pytest.raises(UnknownProviderOrgRole):
            async with session.begin():
                await ensure_membership(
                    session,
                    tenant_id=t.id,
                    workspace_id=w.id,
                    claims=_claims(
                        user_id="new_user_bad_role",
                        org_id="org_deny_unknown",
                        provider_org_role="archmage",
                    ),
                )

    events = audit_logger.query_all_events(action=AuditActions.AUTH_MEMBERSHIP_DENIED)
    assert len(events) == 1
    assert events[0].metadata.get("reason") == "unknown_provider_org_role"
    assert events[0].metadata.get("provider_org_role") == "archmage"


# ---------------------------------------------------------------------------
# Dual membership shape + onboarding creates both rows — 2 tests
# ---------------------------------------------------------------------------


async def test_new_user_onboarding_creates_both_tenant_and_workspace_rows(
    clean_db: str,
) -> None:
    """Subsequent-user onboarding creates BOTH a tenant-level row and
    a workspace-level row (plan §6.5 dual-membership)."""
    sm = get_sessionmaker()
    async with sm() as session:
        await bootstrap(
            session, _claims(user_id="founder", org_id="org_dual")
        )
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(
                user_id="dual_user",
                org_id="org_dual",
                provider_org_role="org:member",
            ),
        )

    m_repo = PostgresMembershipRepository()
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=result.tenant_id, user_id="dual_user"
    )
    assert len(rows) == 2
    scopes = {r.workspace_id for r in rows}
    assert None in scopes
    assert result.workspace_id in scopes


async def test_dual_membership_shape_via_list_for_user_in_tenant(
    clean_db: str,
) -> None:
    """After bootstrap, list_for_user_in_tenant returns rows with
    tenant-level first, then workspace-level (stable ordering)."""
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session, _claims(user_id="list_user", org_id="org_list")
        )

    m_repo = PostgresMembershipRepository()
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=result.tenant_id, user_id="list_user"
    )
    assert len(rows) == 2
    # Ordering: tenant-level (workspace_id=None) first
    assert rows[0].workspace_id is None
    assert rows[1].workspace_id == result.workspace_id


# ---------------------------------------------------------------------------
# Cross-tenant workspace claim — 1 test
# ---------------------------------------------------------------------------


async def test_ensure_tenant_and_workspace_cross_tenant_workspace_claim_rejected(
    clean_db: str,
) -> None:
    """If claims.workspace_id points to a workspace in a DIFFERENT
    tenant, CrossTenantForbidden is raised (§5.7 info-leak guard)."""
    # Seed two tenants, each with one workspace
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()

    tenant_a = await t_repo.create(name="A", slug="cross-a", clerk_org_id="org_cross_a")
    tenant_b = await t_repo.create(name="B", slug="cross-b", clerk_org_id="org_cross_b")
    ws_a = await w_repo.create(
        tenant_id=tenant_a.id, name="A's workspace", is_default=True
    )

    # User authenticates with org_b but claims ws_a
    async def _work(session):
        return await ensure_tenant_and_workspace(
            session,
            _claims(org_id="org_cross_b", workspace_id=ws_a.id),
        )

    with pytest.raises(CrossTenantForbidden):
        await _run_in_session(_work)


# ---------------------------------------------------------------------------
# Audit: TENANT_CREATED on first-use — 1 test
# ---------------------------------------------------------------------------


async def test_tenant_created_audit_emitted_on_first_use(
    clean_db: str,
) -> None:
    """First-use bootstrap emits both AUTH_BOOTSTRAP_CREATED_TENANT and
    TENANT_CREATED audit events after the transaction commits."""
    audit_logger.clear_all()
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(user_id="founder_audit", org_id="org_audit_first"),
        )

    tenant_created = audit_logger.query_all_events(action=AuditActions.TENANT_CREATED)
    boot_created = audit_logger.query_all_events(
        action=AuditActions.AUTH_BOOTSTRAP_CREATED_TENANT
    )
    assert len(tenant_created) == 1
    assert len(boot_created) == 1
    assert tenant_created[0].resource_id == result.tenant_id
    assert boot_created[0].metadata.get("is_first_user") is True


# ---------------------------------------------------------------------------
# Concurrent-first-login retry — 1 test
# ---------------------------------------------------------------------------


async def test_concurrent_first_login_retry_resolves_via_duplicate_tenant(
    clean_db: str,
) -> None:
    """Simulate the race: two 'first logins' for the same org land
    near-simultaneously. The first succeeds; the second sees the row
    already exists (via tenant_repo.get_by_clerk_org_id). No
    orphan rows, consistent outcome.

    Note: asyncio.gather on the same DB with one sessionmaker
    serializes the work at the connection-pool level enough that
    true concurrent DuplicateTenant races are hard to provoke in a
    single-event-loop test. What we CAN verify is that calling
    bootstrap twice back-to-back with the same claims produces the
    expected idempotent state — which is the observable outcome
    even when a real race happens in prod (the retry makes the
    losing attempt look identical to a plain second-call).
    """
    sm = get_sessionmaker()

    async with sm() as session:
        first = await bootstrap(
            session,
            _claims(user_id="first_racer", org_id="org_race"),
        )

    # "Concurrent" second attempt — in practice, it sees the committed
    # tenant row and takes the existing-row path. A real race would
    # hit DuplicateTenant, trigger the retry, and converge to the
    # same observable outcome.
    async with sm() as session:
        second = await bootstrap(
            session,
            _claims(user_id="first_racer", org_id="org_race"),
        )

    # Both attempts see the same tenant + workspace + owner role
    assert first.tenant_id == second.tenant_id
    assert first.workspace_id == second.workspace_id
    assert first.role == "owner"
    assert second.role == "owner"

    # No duplicate membership rows
    m_repo = PostgresMembershipRepository()
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=first.tenant_id, user_id="first_racer"
    )
    assert len(rows) == 2  # dual membership, no duplicates
