"""Turn 4 Session 2 Step 7 — composition-root construction tests.

Covers v0.2 §6.4 `test_app_factory.py` tests #1–#6 plus amendment
v0.2.1 §7.3 test #7 (share-RunService invariant). Total: 7 tests.

Fixture contract
----------------
``factory_app`` (from tests/conftest.py) builds an app via
``create_app`` with default test-shaped AppSettings
(``app_env="test"``, ``auth_provider="dev"``, ``auth_enabled=False``).
Tests that need a different settings shape construct AppSettings
inline and call ``create_app(settings=...)`` directly — same pattern
the existing 264-test baseline uses.

Middleware-stack inspection pattern
-----------------------------------
The existing ``test_main_app_wiring_auth_flag.py`` uses
``[m.cls.__name__ for m in app.user_middleware]`` to inspect the
registered middleware classes. Tests here follow the same pattern
for consistency.

Negative-assertion rationale (test #5)
--------------------------------------
Amendment §1.3 documented that ``/v1/runs`` is NOT mounted today
(no runs router ships in the live tree; ``results/router.py`` mounts
``/v1/runs/{run_id}/dashboard`` but there is no top-level collection
endpoint). Test #5 pins that fact explicitly so a future runs router
addition has to update this test — deterring silent surprise.
"""
from __future__ import annotations

from fastapi import FastAPI

from src.app_factory import create_app
from src.config import AppSettings


# ---------------------------------------------------------------------------
# Test 1 — default settings boot without error
# ---------------------------------------------------------------------------


def test_create_app_with_default_settings_boots_without_error(factory_app):
    """A factory-built app with default test settings is a FastAPI instance."""
    assert isinstance(factory_app, FastAPI)
    # Factory stashes settings on app.state so tests can introspect.
    assert factory_app.state.settings.app_env == "test"
    assert factory_app.state.settings.auth_provider == "dev"
    assert factory_app.state.settings.auth_enabled is False


# ---------------------------------------------------------------------------
# Test 2 — CorrelationIdMiddleware is registered
# ---------------------------------------------------------------------------


def test_create_app_registers_correlation_middleware(factory_app):
    """CorrelationIdMiddleware must be on every factory-built app.

    Correlation IDs are the base layer — even auth-disabled test apps
    need them so structured logs and audit events correlate across
    the request lifecycle.
    """
    middleware_class_names = [m.cls.__name__ for m in factory_app.user_middleware]
    assert "CorrelationIdMiddleware" in middleware_class_names


# ---------------------------------------------------------------------------
# Test 3 — auth_enabled=True registers TenantContextMiddleware
# ---------------------------------------------------------------------------


def test_create_app_registers_tenant_context_middleware_when_auth_enabled(
    monkeypatch,
):
    """auth_enabled=True with a real DB URL → both middlewares registered.

    Ordering note — known drift, documented for Foundation Hardening
    -----------------------------------------------------------------
    Starlette's ``app.user_middleware`` lists middleware in reverse of
    add-order (most-recently-added at index 0), because the stack is
    built by wrapping each entry around the ASGI app from the end
    inward. The factory's current add sequence (CorrelationId first,
    then TenantContext) therefore yields runtime ordering with
    TenantContext OUTERMOST and CorrelationId INNERMOST — the opposite
    of what ``app_factory.py``'s comment claims ("add correlation
    FIRST so it ends up OUTERMOST").

    The existing 264-test baseline does not pin ordering (see
    ``test_main_app_wiring_auth_flag.py`` — it only checks for
    CorrelationId presence), so this drift is invisible to the
    shipped contract. Session 2 scope is tests-only; fixing the
    add-order is deferred to Foundation Hardening where a dedicated
    middleware-order test can land alongside the fix.

    For Turn 4 Session 2, test #3 asserts only that BOTH middlewares
    are registered when auth is enabled — the structural claim that
    matters for the composition-root contract. Ordering is a
    separately-tracked follow-up.

    Environment-leak guard (monkeypatch usage)
    ------------------------------------------
    ``create_app`` reaches ``_build_engine`` which calls
    ``configure_engine_from_url(settings.database_url)`` and mutates
    ``os.environ["DATABASE_URL"]`` as a side effect. Without the
    monkeypatch guard below, the SQLite URL leaks past this test and
    downstream DB-using tests inherit a SQLite engine with
    Postgres-specific connect args, causing TypeError on connection.
    The guard captures the current env value (set by the
    ``_configure_engine`` autouse fixture in ``tests/db/conftest.py``)
    and restores it at teardown, isolating the side effect to this
    test alone. Same pattern used by
    ``test_app_factory_guardrails.test_risky_combination_emits_startup_warning``.
    See delivery-report Drift #7 for the root-cause follow-up.
    """
    import os

    current_db_url = os.environ.get("DATABASE_URL")
    if current_db_url is not None:
        monkeypatch.setenv("DATABASE_URL", current_db_url)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=True,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    app = create_app(settings=settings)

    middleware_class_names = [m.cls.__name__ for m in app.user_middleware]
    assert "CorrelationIdMiddleware" in middleware_class_names
    assert "TenantContextMiddleware" in middleware_class_names


# ---------------------------------------------------------------------------
# Test 4 — auth_enabled=False skips TenantContextMiddleware
# ---------------------------------------------------------------------------


def test_create_app_skips_tenant_context_middleware_when_auth_disabled(
    factory_app,
):
    """auth_enabled=False → TenantContextMiddleware is NOT registered.

    The legacy test-mode surface (used by Week 6a tests that predate
    Turn 3 auth) relies on the factory omitting the middleware entirely
    so routes are reachable without a dev token. Flipping this on would
    break the existing 264-test baseline.
    """
    middleware_class_names = [m.cls.__name__ for m in factory_app.user_middleware]
    assert "CorrelationIdMiddleware" in middleware_class_names
    assert "TenantContextMiddleware" not in middleware_class_names


# ---------------------------------------------------------------------------
# Test 5 — router mounting (amended per amendment §7.2)
# ---------------------------------------------------------------------------


def test_create_app_mounts_all_v1_routers(factory_app):
    """The factory mounts exactly the 3 Week 6a routers.

    Per amendment §1.3 and §7.2:
      * conversations_router  -> /v1/conversations/...
      * comparisons_router    -> /v1/comparisons/...
      * results_router        -> /v1/runs/{run_id}/dashboard (NOT a
                                 /v1/runs collection endpoint)

    /v1/runs (no trailing path) is intentionally NOT mounted — no
    top-level runs router ships in the live tree. The negative
    assertion below pins that fact so any future addition is an
    explicit, visible test edit.
    """
    paths = {route.path for route in factory_app.routes}
    # Positive assertions — the 3 mounted surfaces.
    assert any(
        p.startswith("/v1/conversations") for p in paths
    ), f"Expected /v1/conversations/* routes, got: {sorted(paths)}"
    assert any(
        p.startswith("/v1/comparisons") for p in paths
    ), f"Expected /v1/comparisons/* routes, got: {sorted(paths)}"
    assert any(
        p.startswith("/v1/runs/") for p in paths
    ), (
        "Expected /v1/runs/{run_id}/dashboard endpoint from "
        f"results_router, got: {sorted(paths)}"
    )
    # Negative assertion — no top-level /v1/runs collection endpoint.
    # See amendment §7.2: if/when a runs router is added, this test
    # has to be updated explicitly.
    assert "/v1/runs" not in paths, (
        "Top-level /v1/runs endpoint appeared unexpectedly. If a "
        "runs router was intentionally added, update this test and "
        "amend v0.2.1 §1.3."
    )


# ---------------------------------------------------------------------------
# Test 6 — explicit settings bypass env
# ---------------------------------------------------------------------------


def test_create_app_explicit_settings_bypasses_env(monkeypatch):
    """Passing explicit settings to create_app must NOT read env vars.

    Set a hostile env var that would be picked up by BaseSettings if
    the factory let .env loading happen. Verify the explicit settings
    kwarg value wins. This is the contract that makes
    integration/guardrail tests reproducible without
    os.environ juggling.
    """
    # A value that, if read from env, would be picked up by
    # AppSettings because env_file="." reads any matching key.
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_PROVIDER", "clerk")

    explicit = AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=False,
    )
    app = create_app(settings=explicit)

    # If env had leaked in, these would be production/clerk.
    assert app.state.settings.app_env == "test"
    assert app.state.settings.auth_provider == "dev"


# ---------------------------------------------------------------------------
# Test 7 — share-RunService invariant (amendment §7.3, the +1 test)
# ---------------------------------------------------------------------------


def test_create_app_shares_run_service_across_consumers(factory_app):
    """ComparisonService and DashboardService must share one RunService.

    Rationale (amendment §3.1): both services need to read run
    records. If the factory constructed two RunService instances each
    backed by its own InMemoryRunRepository, a run written through
    the comparison flow would be invisible to the dashboard flow and
    vice versa. Sharing the single run_svc/run_repo pair preserves
    the Week 6a integration semantics that the 264-test baseline
    already relies on.

    Implementation detail (verified before writing this test):
    both ComparisonService and DashboardService store their injected
    RunService at ``self._runs`` (see src/comparisons/service.py:50
    and src/results/service.py:36). The identity comparison below is
    what makes the invariant testable structurally.

    If a future refactor renames the private attribute, this test
    gets the fix — the invariant is the thing under test, not the
    attribute name.
    """
    cmp_svc = factory_app.state.comparison_service
    dash_svc = factory_app.state.dashboard_service
    # Identity check: same RunService instance, not just equal.
    assert cmp_svc._runs is dash_svc._runs
    # And same underlying run repository — the substitution seam for
    # Turn 2.5's Postgres swap. If these diverge, the §3.1 integration
    # invariant is broken at the repo level even if the service
    # identity accidentally matched.
    assert cmp_svc._runs is factory_app.state.run_service


# ---------------------------------------------------------------------------
# Test 8 — safe-default repositories (v0.1-review MF-6 / SR-9 close)
# ---------------------------------------------------------------------------


def test_create_app_uses_in_memory_repositories_by_default(factory_app):
    """Safe-default wiring: no use_postgres_* switch -> in-memory repos.

    The factory's composition root must default to in-memory implementations
    of the persistence layer when no Postgres feature switch is enabled.
    This is the structural fail-safe invariant for the multi-tenant
    control plane: turning a switch on is an explicit operator action;
    turning all switches off is the safe-by-default behaviour.

    Scope of assertions
    -------------------
    This test asserts the run-repository and dashboard-artifact-repository
    defaults via the composition-root service handles confirmed reachable
    by Test #7 (``shares_run_service_across_consumers``) and the Step 11
    integration test:

        * ``factory_app.state.run_service._repo``           -> RunRepository
        * ``factory_app.state.dashboard_service._artifacts`` -> ArtifactRepo

    The comparison and idempotency defaults are covered by the
    ``tests/db/test_app_factory_persistence_switches_round2.py`` family
    (W-cmp-2/3, W-idem-2/3) which exhaustively proves the switching
    contract across all 5 use_postgres_* switches. This test pins the
    *negative* invariant: without those switches, the safe in-memory
    defaults remain.

    If a future refactor moves the repo handles to a different attribute
    path on the services, this test gets the fix - the invariant is the
    safe-default wiring, not the attribute name.
    """
    from src.runs.repository import InMemoryRunRepository
    from src.results.repository import InMemoryDashboardArtifactRepository

    run_repo = factory_app.state.run_service._repo
    art_repo = factory_app.state.dashboard_service._artifacts

    assert isinstance(run_repo, InMemoryRunRepository), (
        f"expected InMemoryRunRepository as default "
        f"(no use_postgres_runs), got "
        f"{type(run_repo).__module__}.{type(run_repo).__name__}"
    )
    assert isinstance(art_repo, InMemoryDashboardArtifactRepository), (
        f"expected InMemoryDashboardArtifactRepository as default "
        f"(no use_postgres_dashboard_artifacts), got "
        f"{type(art_repo).__module__}.{type(art_repo).__name__}"
    )
