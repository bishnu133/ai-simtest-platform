"""Bootstrap hardening tests — Turn 3 Session 2 delta.

Ten additional Postgres-backed tests layered on top of the Step 2
``test_bootstrap_lifecycle.py`` coverage. These close direct-coverage
gaps introduced by Turn 4 Session 2's ``allow_self_serve_provisioning``
work and by the ``use_session_or_admin`` boundary that the lifecycle
suite exercises only implicitly.

Coverage split (10 tests):

* Self-serve provisioning gate (4 tests) — the
  ``allow_self_serve_provisioning`` flag added in Turn 4 Session 2's
  Step 6.5 drift #2 resolution. The lifecycle suite defaults it to
  True everywhere; these tests verify all four leaves of the decision
  table (unknown org × flag True/False, known org × flag True/False)
  and the audit-silence on refused provisioning.

* Session-or-factory boundary (4 tests) — Turn 3 plan §5.0 "no hidden
  session access" invariant, implemented via
  ``src/db/session_or_factory.py::use_session_or_admin``. The lifecycle
  suite passes sessions through bootstrap but never asserts on the
  commit/rollback boundary directly.

* Returning-user and audit-silence (2 tests) — closes the complement
  of lifecycle tests #6 and #19. Test #6 covers the new-user
  onboarding path's ``is_first_user=False``; this suite covers the
  returning-user fast-path's ``is_first_user=False``. Test #19 covers
  positive ``AUTH_BOOTSTRAP_CREATED_TENANT`` emission on first-use;
  this suite covers the negative case (no emission on existing-tenant
  login).

Test infrastructure: reuses the ``clean_db`` fixture from
tests/db/conftest.py (routed via tests/conftest.py re-export).
``audit_logger`` is cleared by the autouse ``reset_audit_log`` fixture
in tests/conftest.py.

Zero-removal / additive-only: this file adds new test functions. It
does not touch ``test_bootstrap_lifecycle.py`` or any other existing
test module.
"""
from __future__ import annotations

import pytest

from src.api.errors import (
    DuplicateMembership,
    TenantStateInvalid,
)
from src.audit.logger import AuditActions, audit_logger
from src.auth.bootstrap import bootstrap
from src.auth.provider import VerifiedClaims
from src.db.session import get_sessionmaker, raw_admin_session
from src.memberships import PostgresMembershipRepository
from src.tenants import PostgresTenantRepository
from src.workspaces import PostgresWorkspaceRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local helpers (mirrors test_bootstrap_lifecycle.py conventions)
# ---------------------------------------------------------------------------


def _claims(
    *,
    user_id: str = "user_alice",
    org_id: str = "org_acme",
    provider_org_role: str | None = "org:admin",
    workspace_id: str | None = None,
) -> VerifiedClaims:
    """Convenience claims builder, identical shape to the lifecycle suite."""
    return VerifiedClaims(
        user_id=user_id,
        org_id=org_id,
        provider_org_role=provider_org_role,
        workspace_id=workspace_id,
    )


# ===========================================================================
# Batch 1 — allow_self_serve_provisioning gate (4 tests)
#
# Decision table the bootstrap flag covers:
#
#                      | flag=True (default) | flag=False                  |
#   -------------------+---------------------+----------------------------+
#   org_id UNKNOWN     | create tenant + ws  | raise TenantStateInvalid   |
#   org_id KNOWN       | reuse tenant + ws   | reuse tenant + ws          |
#
# The lifecycle suite covers only the top-left leaf (flag=True implicit
# via default). These tests cover the other three leaves and the audit
# side-effect of the refused path.
# ===========================================================================


async def test_self_serve_disabled_with_unknown_org_raises_tenant_state_invalid(
    clean_db: str,
) -> None:
    """When ``allow_self_serve_provisioning=False`` and the claim's
    ``org_id`` has no matching tenant, bootstrap must fail-closed with
    ``TenantStateInvalid`` (409).

    This is the core fail-closed invariant Turn 4 Session 2 added: a
    valid Clerk JWT for an org that does not exist in this environment
    should not silently auto-provision; operations must explicitly
    enable provisioning in non-self-serve environments (e.g. enterprise
    tenants with explicit onboarding).
    """
    sm = get_sessionmaker()
    async with sm() as session:
        with pytest.raises(TenantStateInvalid) as exc_info:
            await bootstrap(
                session,
                _claims(
                    user_id="stranger",
                    org_id="org_not_onboarded",
                ),
                allow_self_serve_provisioning=False,
            )

    # The exception carries the provisioning-drift reason in details so
    # ops can distinguish this from other TenantStateInvalid paths.
    assert exc_info.value.http_status == 409
    assert exc_info.value.code == "tenant_state_invalid"
    assert exc_info.value.details.get("org_id") == "org_not_onboarded"
    assert exc_info.value.details.get("reason") == (
        "unknown_org_id_self_serve_disabled"
    )

    # No tenant was created — verify nothing landed in the DB.
    t_repo = PostgresTenantRepository()
    result = await t_repo.get_by_clerk_org_id("org_not_onboarded")
    assert result is None


async def test_self_serve_enabled_with_unknown_org_creates_tenant(
    clean_db: str,
) -> None:
    """When ``allow_self_serve_provisioning=True`` (the default) and the
    ``org_id`` is unknown, bootstrap provisions the tenant and default
    workspace normally.

    This establishes the positive control for the gate: the same claims
    that are refused in the disabled test above must succeed when the
    flag is True. Explicit rather than relying on the default so the
    flag's effect is demonstrated symmetrically.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(
                user_id="founder_explicit",
                org_id="org_onboard_ok",
            ),
            allow_self_serve_provisioning=True,
        )

    assert result.tenant_id
    assert result.workspace_id
    assert result.is_first_user is True
    assert result.role == "owner"

    # Tenant was created with the expected clerk_org_id.
    t_repo = PostgresTenantRepository()
    tenant = await t_repo.get_by_id(result.tenant_id)
    assert tenant is not None
    assert tenant.clerk_org_id == "org_onboard_ok"


async def test_self_serve_disabled_with_existing_tenant_succeeds(
    clean_db: str,
) -> None:
    """The ``allow_self_serve_provisioning`` flag gates tenant CREATION
    only. When a tenant already exists for this ``org_id``, bootstrap
    must succeed even with the flag disabled.

    This is critical for the operator runbook: a production deploy
    that runs with the flag disabled must not lock out users whose
    tenants already exist (e.g. after an initial operator-driven
    provisioning). The flag is a provisioning gate, not an auth gate.
    """
    # Phase 1: pre-seed a tenant + workspace + first member using a
    # separate session, mimicking an operator-driven provisioning
    # flow that happens BEFORE the flag is disabled.
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    m_repo = PostgresMembershipRepository()

    tenant = await t_repo.create(
        name="Preseeded",
        slug="preseeded-tenant",
        clerk_org_id="org_preseeded",
    )
    workspace = await w_repo.create(
        tenant_id=tenant.id,
        name="Default Workspace",
        is_default=True,
    )
    # Seed ONE existing tenant-level membership for a different user so
    # the incoming user is NOT the first-ever member of the tenant.
    # This forces the ensure_membership precedence-onboarding path, not
    # the first-user-as-owner shortcut. The incoming user's claim
    # carries a valid provider_org_role so the onboarding succeeds.
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="existing_admin",
        workspace_id=None,
        role="owner",
    )

    # Phase 2: an already-authorized-tenant user logs in with the flag
    # disabled. Bootstrap should succeed because the tenant already
    # exists — the flag only gates CREATE, not READ.
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(
                user_id="newcomer_with_role",
                org_id="org_preseeded",
                provider_org_role="org:member",
            ),
            allow_self_serve_provisioning=False,
        )

    assert result.tenant_id == tenant.id
    assert result.workspace_id == workspace.id
    assert result.is_first_user is False
    # The newcomer gets 'member' from the provider_org_role mapping
    # because they are onboarding (no pre-existing memberships).
    assert result.role == "member"


async def test_refused_provisioning_does_not_emit_tenant_created_audit(
    clean_db: str,
) -> None:
    """When bootstrap refuses provisioning via ``TenantStateInvalid``,
    neither ``AUTH_BOOTSTRAP_CREATED_TENANT`` nor ``TENANT_CREATED``
    audit events may be emitted.

    The audit log is the compliance record of state-changing events.
    A refused provisioning attempt changes no state, so the audit log
    must not claim otherwise. The bootstrap() implementation places
    the audit emission AFTER the transaction block specifically for
    this reason (plan §5.6.1 "audit reflects committed state" rule);
    this test locks that invariant in.
    """
    audit_logger.clear_all()
    sm = get_sessionmaker()
    async with sm() as session:
        with pytest.raises(TenantStateInvalid):
            await bootstrap(
                session,
                _claims(
                    user_id="stranger_2",
                    org_id="org_no_audit_on_refuse",
                ),
                allow_self_serve_provisioning=False,
            )

    # Neither creation-family audit event may be present.
    tenant_created = audit_logger.query_all_events(action=AuditActions.TENANT_CREATED)
    boot_created = audit_logger.query_all_events(
        action=AuditActions.AUTH_BOOTSTRAP_CREATED_TENANT
    )
    assert tenant_created == []
    assert boot_created == []


# ===========================================================================
# Batch 2 — session_or_factory boundary (4 tests)
#
# Plan §5.0 "no hidden session access" is enforced through the
# ``use_session_or_admin`` helper. The lifecycle suite exercises the
# helper transitively (bootstrap passes a session through; repos
# consume it) but never asserts on the commit/rollback boundary
# directly. These tests lock the contract in.
#
# All four use ``PostgresMembershipRepository.create`` as the
# representative repo method. The membership repo has the cleanest
# IntegrityError path (partial unique index violations) so the
# rollback-on-error semantics are easy to provoke.
# ===========================================================================


async def test_repo_with_external_session_does_not_commit(
    clean_db: str,
) -> None:
    """When the caller passes ``session=`` into a repo, the repo MUST
    NOT call ``commit()`` itself. The caller owns the transaction
    boundary.

    We verify this by:
      1. Opening a session + ``session.begin()`` block in the test
      2. Calling repo.create(..., session=our_session)
      3. Asserting the work is NOT yet visible from a SEPARATE
         connection (because our outer transaction has not committed)
      4. Asserting the work IS visible AFTER our outer commit

    If the repo were to commit internally, step 3 would see the row;
    the fact that step 3 sees nothing proves the repo honors the
    caller-owned transaction boundary.
    """
    # Phase 1: seed a tenant + workspace outside the test transaction
    # so we have something to reference.
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.create(
        name="T",
        slug="external-session-tenant",
        clerk_org_id="org_ext_session",
    )
    await w_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )

    # Phase 2: open a caller-owned session and call repo.create inside
    # a begin() block. Before we commit, the row must NOT be visible
    # from a separate connection.
    sm = get_sessionmaker()
    m_repo = PostgresMembershipRepository()

    async with sm() as caller_session:
        async with caller_session.begin():
            await m_repo.create(
                tenant_id=tenant.id,
                user_id="user_ext_session",
                workspace_id=None,
                role="admin",
                session=caller_session,
            )
            # At this point, before begin() commits, a separate
            # connection must not see the row. Open one and check.
            async with raw_admin_session() as probe_session:
                role_seen = await m_repo.get_role(
                    tenant_id=tenant.id,
                    user_id="user_ext_session",
                    workspace_id=None,
                    session=probe_session,
                )
                assert role_seen is None, (
                    "Repo committed prematurely — external session "
                    "contract violated"
                )

    # Phase 3: now the begin() block has committed. A fresh probe
    # session must see the row.
    async with raw_admin_session() as probe_session:
        role_seen = await m_repo.get_role(
            tenant_id=tenant.id,
            user_id="user_ext_session",
            workspace_id=None,
            session=probe_session,
        )
    assert role_seen == "admin"


async def test_repo_with_external_session_rolls_back_with_parent(
    clean_db: str,
) -> None:
    """When an external-session repo.create completes but the caller's
    outer transaction raises and rolls back, the INSERT must roll back
    with it.

    Because the repo with ``owns=False`` only calls ``flush()``, the
    INSERT is pending in the outer transaction; the outer rollback
    reverts it. This test makes that invariant observable.
    """
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.create(
        name="T",
        slug="rollback-parent-tenant",
        clerk_org_id="org_rollback_parent",
    )
    await w_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )

    sm = get_sessionmaker()
    m_repo = PostgresMembershipRepository()

    class DeliberateAbort(RuntimeError):
        """Marker exception so we can assert on the intended rollback."""

    with pytest.raises(DeliberateAbort):
        async with sm() as caller_session:
            async with caller_session.begin():
                await m_repo.create(
                    tenant_id=tenant.id,
                    user_id="user_rollback",
                    workspace_id=None,
                    role="admin",
                    session=caller_session,
                )
                # Simulate the caller deciding to abort AFTER the
                # repo work. begin() should roll everything back.
                raise DeliberateAbort("simulated outer-scope failure")

    # Verify the INSERT did not persist.
    async with raw_admin_session() as probe_session:
        role_seen = await m_repo.get_role(
            tenant_id=tenant.id,
            user_id="user_rollback",
            workspace_id=None,
            session=probe_session,
        )
    assert role_seen is None, (
        "Outer rollback did not revert the repo INSERT — external "
        "session contract violated"
    )


async def test_repo_without_session_kwarg_commits_on_success(
    clean_db: str,
) -> None:
    """Legacy path: when the caller does NOT pass ``session=``, the
    repo opens its own session via ``raw_admin_session`` and commits
    on clean exit. This path preserves the Turn 2 repo contract
    (14 Turn 2 tests still pass because of this backward-compat).

    We verify this by:
      1. Calling repo.create without session=
      2. Without any outer transaction context, the work must be
         committed by the time the call returns
      3. A completely new session must see the row immediately
    """
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.create(
        name="T",
        slug="legacy-commit-tenant",
        clerk_org_id="org_legacy_commit",
    )
    await w_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )

    m_repo = PostgresMembershipRepository()

    # Call create WITHOUT session= kwarg — legacy path, repo opens
    # and commits its own session.
    record = await m_repo.create(
        tenant_id=tenant.id,
        user_id="user_legacy",
        workspace_id=None,
        role="member",
    )
    assert record.id  # fully-populated post-commit

    # Immediately visible from a fresh session.
    async with raw_admin_session() as probe_session:
        role_seen = await m_repo.get_role(
            tenant_id=tenant.id,
            user_id="user_legacy",
            workspace_id=None,
            session=probe_session,
        )
    assert role_seen == "member"


async def test_repo_without_session_kwarg_rolls_back_on_integrity_error(
    clean_db: str,
) -> None:
    """Legacy path: when the caller does NOT pass ``session=`` and a
    constraint violation fires, the repo rolls its OWN session back
    before raising ``DuplicateMembership``. The database must remain
    in a consistent state (first row present, duplicate rejected).

    We verify this by:
      1. Inserting a tenant-level membership (clean)
      2. Attempting to insert a second tenant-level membership for
         the same user — which fires the partial unique index
      3. Assert ``DuplicateMembership`` is raised
      4. Assert the ORIGINAL row is still intact (the rollback was
         scoped to the failing INSERT, not the earlier successful
         one)
    """
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    tenant = await t_repo.create(
        name="T",
        slug="legacy-rollback-tenant",
        clerk_org_id="org_legacy_rollback",
    )
    await w_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )

    m_repo = PostgresMembershipRepository()

    # Phase 1: clean insert.
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="user_dup",
        workspace_id=None,
        role="admin",
    )

    # Phase 2: second insert with same (tenant_id, user_id, NULL) fires
    # uq_memberships_tenant_level — DuplicateMembership.
    with pytest.raises(DuplicateMembership) as exc_info:
        await m_repo.create(
            tenant_id=tenant.id,
            user_id="user_dup",
            workspace_id=None,
            role="member",  # different role to prove it's the tuple not the value
        )
    assert exc_info.value.details.get("scope") == "tenant_level"

    # Phase 3: the first row must still be intact. If the rollback had
    # been over-broad, the original admin row would be gone.
    async with raw_admin_session() as probe_session:
        role_seen = await m_repo.get_role(
            tenant_id=tenant.id,
            user_id="user_dup",
            workspace_id=None,
            session=probe_session,
        )
    assert role_seen == "admin", (
        "Legacy-path rollback was over-broad — the pre-existing row "
        "was reverted along with the duplicate INSERT"
    )


# ===========================================================================
# Batch 3 — complementary coverage (2 tests)
#
# These close the complement of lifecycle tests #6 and #19:
#
#   * Lifecycle #6 (test_subsequent_user_onboarding_requires_valid_provider_role)
#     covers is_first_user=False on the NEW-user-onboarding path (maps
#     provider_org_role, creates both rows). The RETURNING-user
#     fast-path (precedence step 1 — workspace row already exists)
#     also yields is_first_user=False but through a different code
#     path; that path has no direct test.
#
#   * Lifecycle #19 (test_tenant_created_audit_emitted_on_first_use)
#     covers positive AUTH_BOOTSTRAP_CREATED_TENANT emission. The
#     negative case (no emission on existing-tenant login) has no
#     direct test. Audit hygiene matters: a false-positive emission
#     would corrupt the provisioning timeline in the audit log.
# ===========================================================================


async def test_returning_member_fast_path_sets_is_first_user_false(
    clean_db: str,
) -> None:
    """Returning user with a pre-existing workspace-level membership
    takes the v0.4 §5.6 precedence step-1 fast-path: repo.get_role
    returns the ws role, bootstrap returns WITHOUT calling
    ``map_provider_org_role`` or creating any new rows.

    The returning-user branch is distinct from the onboarding branch
    covered by lifecycle #6: onboarding exercises ``ensure_membership``
    step 3 (maps role + creates both rows); the returning-user
    fast-path short-circuits at step 1.

    Assertions:
      1. ``result.is_first_user is False`` (not onboarding)
      2. ``result.role`` equals the pre-seeded workspace role
         (NOT the provider_org_role claim — precedence wins)
      3. No audit events on existing-tenant login (sanity: fast-path
         is intentionally silent per bootstrap.py §step-1 docstring)
    """
    # Phase 1: seed tenant + workspace + workspace-level row for user.
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    m_repo = PostgresMembershipRepository()

    tenant = await t_repo.create(
        name="T",
        slug="returning-member-tenant",
        clerk_org_id="org_returning",
    )
    workspace = await w_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )
    # Seed one "other" tenant member so the first-user-as-owner branch
    # is skipped. Otherwise bootstrap would take the first-user
    # shortcut and never exercise ensure_membership.
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="some_other_member",
        workspace_id=None,
        role="owner",
    )
    # Seed the returning user's workspace-level membership with a role
    # that is DIFFERENT from what the provider_org_role claim maps to.
    # If precedence is honored, the result.role must match the
    # pre-seeded 'admin', NOT the claim's 'org:member' → 'member'.
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="returning_user",
        workspace_id=workspace.id,
        role="admin",
    )

    # Phase 2: the returning user logs in. Claim carries a differing
    # provider_org_role; precedence must ignore it.
    audit_logger.clear_all()
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(
                user_id="returning_user",
                org_id="org_returning",
                provider_org_role="org:member",  # would map to 'member' if used
            ),
        )

    assert result.is_first_user is False
    assert result.tenant_id == tenant.id
    assert result.workspace_id == workspace.id
    # Role came from the pre-seeded workspace-level row, NOT from the
    # provider_org_role claim. This is the v0.4 §5.6 MF-1 invariant.
    assert result.role == "admin"

    # Fast-path is intentionally silent on AUTH_MEMBERSHIP_DENIED
    # (no onboarding attempt). Also no new audit events about tenant
    # creation because the tenant already existed.
    denied = audit_logger.query_all_events(action=AuditActions.AUTH_MEMBERSHIP_DENIED)
    created = audit_logger.query_all_events(action=AuditActions.TENANT_CREATED)
    boot_created = audit_logger.query_all_events(
        action=AuditActions.AUTH_BOOTSTRAP_CREATED_TENANT
    )
    assert denied == []
    assert created == []
    assert boot_created == []


async def test_existing_tenant_login_does_not_emit_tenant_created_audit(
    clean_db: str,
) -> None:
    """When a user logs into a tenant that already exists, neither
    ``AUTH_BOOTSTRAP_CREATED_TENANT`` nor ``TENANT_CREATED`` audit
    events may be emitted — even if the user is genuinely new to the
    tenant (i.e. the onboarding path creates membership rows).

    This is the complement of lifecycle test #19 (positive emission
    on first-use). Without this negative test, a regression that
    emitted tenant-creation audit on every login would pass #19 but
    corrupt the audit log.

    Scenario:
      1. Pre-seed an existing tenant + workspace (without first-use
         bootstrap, so tenant_just_created is False on the subsequent
         login).
      2. A genuinely new user (no memberships yet) logs in with a
         valid provider_org_role. Onboarding creates membership rows.
      3. Assert NO tenant-created audit events fire, even though
         NEW ROWS DID land in memberships.
    """
    # Phase 1: pre-seed tenant + workspace + one pre-existing member
    # so the new user is not the first and takes the onboarding path.
    t_repo = PostgresTenantRepository()
    w_repo = PostgresWorkspaceRepository()
    m_repo = PostgresMembershipRepository()

    tenant = await t_repo.create(
        name="T",
        slug="existing-tenant-audit",
        clerk_org_id="org_existing_audit",
    )
    await w_repo.create(
        tenant_id=tenant.id,
        name="Default",
        is_default=True,
    )
    await m_repo.create(
        tenant_id=tenant.id,
        user_id="original_owner",
        workspace_id=None,
        role="owner",
    )

    # Phase 2: new-to-tenant user logs in with valid provider_org_role.
    # Onboarding path creates membership rows but the TENANT itself
    # was not just created.
    audit_logger.clear_all()
    sm = get_sessionmaker()
    async with sm() as session:
        result = await bootstrap(
            session,
            _claims(
                user_id="brand_new_user",
                org_id="org_existing_audit",
                provider_org_role="org:member",
            ),
        )

    # Sanity: onboarding DID create membership rows.
    assert result.is_first_user is False
    assert result.role == "member"
    rows = await m_repo.list_for_user_in_tenant(
        tenant_id=tenant.id,
        user_id="brand_new_user",
    )
    assert len(rows) == 2  # dual-membership onboarding

    # Phase 3: assert NO tenant-creation audit events fired despite
    # the new membership rows.
    tenant_created = audit_logger.query_all_events(action=AuditActions.TENANT_CREATED)
    boot_created = audit_logger.query_all_events(
        action=AuditActions.AUTH_BOOTSTRAP_CREATED_TENANT
    )
    assert tenant_created == [], (
        "TENANT_CREATED audit fired on existing-tenant login — "
        "audit log falsely claims provisioning happened"
    )
    assert boot_created == [], (
        "AUTH_BOOTSTRAP_CREATED_TENANT audit fired on existing-tenant "
        "login — audit log falsely claims provisioning happened"
    )
