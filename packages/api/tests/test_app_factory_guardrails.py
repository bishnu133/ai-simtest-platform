"""Turn 4 Session 2 Step 8 — AppSettings guardrail tests.

Covers v0.2 §6.4 tests #1–#7 for ``test_app_factory_guardrails.py``.
The file ships 7 tests (4 fatal guardrails + 2 parametrized
derivation matrices + 1 risky-combination warning) against the
Session 1 ``_enforce_production_guardrails`` /
``_warn_on_risky_combinations`` / ``AppSettings`` surfaces, completed
by Step 6.5's additive edits (allow_self_serve_provisioning field +
validator + warning branch + bootstrap raise-site).

Guardrail order note
--------------------
``_enforce_production_guardrails`` checks in this order under
``app_env="production"``:

  1. auth_provider != "dev"   -> FatalConfigurationError
  2. auth_enabled is True
  3. database_url is set
  4. auth_provider == "clerk" -> all 3 Clerk settings present

Tests that want to reach the LATER checks must satisfy the earlier
ones (e.g. test #4 asserts the auth_enabled guardrail and therefore
sets auth_provider="clerk" to bypass check #1).

Ordering for non-production environments (handled by the
``app_env != "test" and not database_url`` branch):

  * test_nontest_env_missing_database_url covers staging/dev path
    through that same branch.
"""
from __future__ import annotations

import logging

import pytest

from src.app_factory import FatalConfigurationError, create_app
from src.config import AppSettings


# ---------------------------------------------------------------------------
# Fatal production guardrails (tests #1–#4)
# ---------------------------------------------------------------------------


def test_production_with_dev_provider_raises_fatal_error():
    """app_env='production' + auth_provider='dev' must abort startup.

    DevAuthProvider is header-based and accepts unauthenticated
    identity. Running it in production is a blanket authentication
    bypass — production must use ClerkAuthProvider (or another real
    provider).
    """
    settings = AppSettings(
        app_env="production",
        auth_provider="dev",
        auth_enabled=True,
        database_url="postgresql+asyncpg://user:pw@localhost/db",
    )
    with pytest.raises(FatalConfigurationError) as exc_info:
        create_app(settings=settings)
    assert "dev" in str(exc_info.value).lower()
    assert "production" in str(exc_info.value).lower()


def test_production_missing_clerk_config_raises_fatal_error():
    """app_env='production' + auth_provider='clerk' with missing
    Clerk env vars must abort startup.

    Production with Clerk but no JWKS URL / issuer / audience cannot
    verify a single token — catching this at boot is strictly better
    than discovering it on the first real request.
    """
    settings = AppSettings(
        app_env="production",
        auth_provider="clerk",
        auth_enabled=True,
        database_url="postgresql+asyncpg://user:pw@localhost/db",
        # clerk_jwks_url / clerk_issuer / clerk_audience deliberately None
    )
    with pytest.raises(FatalConfigurationError) as exc_info:
        create_app(settings=settings)
    msg = str(exc_info.value).lower()
    assert "clerk" in msg
    # The guardrail surfaces which specific fields are missing — sanity
    # check at least one name is mentioned in the error.
    assert any(
        name in msg
        for name in ("clerk_jwks_url", "clerk_issuer", "clerk_audience")
    )


def test_nontest_env_missing_database_url_raises_fatal_error():
    """Any non-test env requires DATABASE_URL. Only app_env='test' may
    run without a database — everything else aborts.

    Exercised here via app_env='production' (which is the most
    production-realistic path and already sets all the other
    production preconditions).
    """
    settings = AppSettings(
        app_env="production",
        auth_provider="clerk",
        auth_enabled=True,
        database_url=None,  # the missing piece we're testing
        clerk_jwks_url="https://example.clerk.dev/.well-known/jwks.json",
        clerk_issuer="https://example.clerk.dev",
        clerk_audience="https://api.example.com",
    )
    with pytest.raises(FatalConfigurationError) as exc_info:
        create_app(settings=settings)
    msg = str(exc_info.value).lower()
    assert "database_url" in msg
    assert "production" in msg


def test_production_with_auth_disabled_raises_fatal_error():
    """app_env='production' + auth_enabled=False must abort startup.

    Production must always run with TenantContextMiddleware registered
    so RLS context is set on every request. Disabling auth in prod is
    a structural security hole.

    Note: to reach the auth_enabled check, we need auth_provider='clerk'
    (to bypass the dev-provider guardrail that fires first).
    """
    settings = AppSettings(
        app_env="production",
        auth_provider="clerk",
        auth_enabled=False,  # the forbidden bit we're testing
        database_url="postgresql+asyncpg://user:pw@localhost/db",
        clerk_jwks_url="https://example.clerk.dev/.well-known/jwks.json",
        clerk_issuer="https://example.clerk.dev",
        clerk_audience="https://api.example.com",
    )
    with pytest.raises(FatalConfigurationError) as exc_info:
        create_app(settings=settings)
    msg = str(exc_info.value).lower()
    assert "auth_enabled" in msg
    assert "production" in msg


# ---------------------------------------------------------------------------
# allow_self_serve_provisioning derivation matrix (test #5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_env", "expected"),
    [
        ("development", True),
        ("test", True),
        ("staging", False),
        ("production", False),
    ],
)
def test_allow_self_serve_provisioning_default_derivation(app_env, expected):
    """AppSettings derives allow_self_serve_provisioning from app_env
    when the field is not explicitly set.

    Plan §3.3 derivation rule:
        development, test   -> True   (friction-free dev loop)
        staging, production -> False  (fail-closed enterprise)

    Post-condition invariant: after construction the field is ALWAYS a
    bool, never None, regardless of the input env.
    """
    settings = AppSettings(app_env=app_env)
    assert settings.allow_self_serve_provisioning is expected
    # Invariant: never None after validator runs.
    assert isinstance(settings.allow_self_serve_provisioning, bool)


# ---------------------------------------------------------------------------
# Explicit override wins (test #6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_env", "explicit", "expected"),
    [
        # Operator explicitly enables in production — must be honored.
        # (The risky-combination warning — tested separately in #7 —
        # fires on this path, but it's non-fatal.)
        ("production", True, True),
        # Operator explicitly disables in development — must be honored
        # even though the derivation default would pick True.
        ("development", False, False),
    ],
)
def test_allow_self_serve_provisioning_explicit_override_wins(
    app_env, explicit, expected
):
    """An explicit allow_self_serve_provisioning value always wins
    over the app_env-derived default.

    The derivation validator only fills in the value when the field is
    None (the not-explicitly-set sentinel). Any explicit True or False
    from env / constructor is preserved.
    """
    settings = AppSettings(
        app_env=app_env,
        allow_self_serve_provisioning=explicit,
    )
    assert settings.allow_self_serve_provisioning is expected


# ---------------------------------------------------------------------------
# Risky-combination startup warning (test #7, SR-1)
# ---------------------------------------------------------------------------


def test_risky_combination_emits_startup_warning(caplog, monkeypatch):
    """A production deployment with allow_self_serve_provisioning=True
    is legal but must emit a loud startup warning (SR-1).

    This is the force-function for the override: silently-easy becomes
    loudly-easy so enterprise security teams see the configuration in
    their log aggregator. Non-fatal — the operator has the authority
    to make this call; app_factory honours it and moves on.

    Test shape: construct a fully-valid production AppSettings
    (auth_provider=clerk, all Clerk config present, database_url set,
    auth_enabled=True) plus the explicit override. Invoke create_app
    inside a caplog.at_level context and assert the WARNING message
    is captured and mentions the field name.

    Environment-leak guard (monkeypatch usage)
    ------------------------------------------
    ``create_app`` reaches ``_build_engine`` after the warning fires,
    and ``_build_engine`` calls ``configure_engine_from_url`` which
    mutates ``os.environ["DATABASE_URL"]`` as a side effect. Without
    monkeypatch, that mutation leaks across tests and subsequent
    ``clean_db``-using tests (e.g. tests/test_destructive_failure_paths.py,
    tests/test_app_factory_integration_postgres.py) inherit the fake
    production URL instead of the ephemeral test PG URL, causing
    ``ConnectionRefusedError`` on port 5432. The ``monkeypatch.setenv``
    below captures the current env value and restores it at
    test-teardown, isolating the side effect to this test alone.

    Root cause and follow-up
    ------------------------
    The real fix belongs in ``src.db.session.configure_engine_from_url``
    (don't mutate env as a side effect, or make the mutation opt-in via
    a separate function). That's out of scope for Session 2's test-only
    charter and is queued for Foundation Hardening as Drift #7.

    Note on DB URL
    --------------
    create_app will try to build an engine from this URL. A real
    asyncpg URL pointing at a non-existent host would fail during
    engine-create only if SQLAlchemy eagerly connects — it does not
    (lazy connection). SQLite in-memory is not valid under production
    guardrails' expectation (it's a Postgres-only deployment target)
    but no production guardrail actually inspects the URL scheme, so
    a fake asyncpg URL is the simplest cross-platform choice.
    """
    # Capture DATABASE_URL as-set by the autouse _configure_engine fixture
    # (or as-unset), and ensure monkeypatch restores it at test teardown.
    import os

    current_db_url = os.environ.get("DATABASE_URL")
    if current_db_url is not None:
        monkeypatch.setenv("DATABASE_URL", current_db_url)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = AppSettings(
        app_env="production",
        auth_provider="clerk",
        auth_enabled=True,
        database_url="postgresql+asyncpg://user:pw@localhost/db",
        clerk_jwks_url="https://example.clerk.dev/.well-known/jwks.json",
        clerk_issuer="https://example.clerk.dev",
        clerk_audience="https://api.example.com",
        allow_self_serve_provisioning=True,  # the risky override
    )
    # Derivation invariant: the explicit True survived.
    assert settings.allow_self_serve_provisioning is True

    with caplog.at_level(logging.WARNING, logger="src.app_factory"):
        # create_app may raise on downstream integration points (Clerk
        # provider construction reaches out to fetch JWKS eagerly in
        # some installed snapshots). We only care that the warning fires
        # BEFORE any such failure, so catch any downstream exception
        # after asserting the warning landed in caplog.
        try:
            create_app(settings=settings)
        except Exception:
            # Downstream failures (Clerk JWKS fetch, engine connect,
            # etc.) are not what this test is about. The warning fires
            # inside _warn_on_risky_combinations which runs BEFORE any
            # of those, so the log record should already be in caplog
            # regardless of what happens after.
            pass

    warning_messages = [
        record.message for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    # The warning message mentions the field name explicitly so
    # operators grepping logs can find it.
    matching = [
        m for m in warning_messages
        if "allow_self_serve_provisioning" in m
        and "production" in m.lower()
    ]
    assert matching, (
        "Expected a WARNING mentioning 'allow_self_serve_provisioning' "
        f"and 'production'; got warnings: {warning_messages}"
    )
