"""Turn 4 composition root — production application factory.

This module replaces ``src.main.create_app`` as the canonical entry
point for production wiring. ``src/main.py`` becomes a thin shim that
delegates here (see Step 6 / v0.2 §4).

Public API
----------
``create_app(*, settings: AppSettings | None = None) -> FastAPI``
  The composition root. Constructs the per-app object graph (engine,
  sessionmaker, auth provider, services, middleware, routers) and
  returns a ready-to-serve ``FastAPI`` instance. If ``settings`` is
  None, an ``AppSettings`` is constructed from environment / .env.

Guardrails
----------
Two layers, applied in order at the top of ``create_app``:

  1. ``_enforce_production_guardrails(settings)`` — fatal on dangerous
     prod-environment combinations (raises ``FatalConfigurationError``).
  2. ``_warn_on_risky_combinations(settings)`` — non-fatal, logs a
     warning for unusual but non-prod combinations.

Wiring graph
------------
See amendment §3.9. In summary::

  create_app(settings)
    ├── enforce_production_guardrails(settings)
    ├── warn_on_risky_combinations(settings)
    ├── engine        = _build_engine(settings)
    ├── sessionmaker  = _build_sessionmaker(engine)
    ├── provider      = _build_auth_provider(settings)
    ├── app           = FastAPI(...)
    ├── add CorrelationIdMiddleware (outermost)
    ├── if auth_enabled and sessionmaker is not None:
    │       add TenantContextMiddleware(provider, sessionmaker, exempt_paths)
    ├── add APIError exception handler
    ├── set_dashboard_invalidator(dashboard_cache.invalidate)
    ├── include 3 routers (conversations, results, comparisons) at /v1
    ├── _bind_services(app, settings)         # service-layer dep overrides
    └── return app
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI

from src.config import AppSettings


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class FatalConfigurationError(RuntimeError):
    """Raised when an AppSettings combination is unsafe to ship.

    Distinct from generic ``ValueError`` so monitoring / health-check
    code can branch on it.
    """


def _enforce_production_guardrails(settings: AppSettings) -> None:
    """Hard guardrails — raise ``FatalConfigurationError`` on violation.

    These represent combinations that are dangerous to run in
    production. Any of these conditions abort ``create_app`` before
    a request can reach the auth pipeline.
    """
    if settings.app_env == "production":
        if settings.auth_provider == "dev":
            raise FatalConfigurationError(
                "auth_provider='dev' is forbidden in app_env='production'. "
                "DevAuthProvider is header-based and accepts unauthenticated "
                "identity. Use auth_provider='clerk' (or another real "
                "provider) in production."
            )
        if not settings.auth_enabled:
            raise FatalConfigurationError(
                "auth_enabled=False is forbidden in app_env='production'. "
                "Production must always run with TenantContextMiddleware "
                "registered so RLS context is set on every request."
            )
        if not settings.database_url:
            raise FatalConfigurationError(
                "database_url is required in app_env='production'. "
                "Cannot construct an engine without a URL."
            )
        if settings.auth_provider == "clerk":
            missing = [
                name
                for name, val in (
                    ("clerk_jwks_url", settings.clerk_jwks_url),
                    ("clerk_issuer", settings.clerk_issuer),
                    ("clerk_audience", settings.clerk_audience),
                )
                if not val
            ]
            if missing:
                raise FatalConfigurationError(
                    f"auth_provider='clerk' in app_env='production' requires "
                    f"all Clerk settings to be set; missing: {missing}"
                )

    # Turn 2.5 plan v0.2.1 §3.4 RC-6 cross-field guardrail. Applies in
    # all app_envs, not just production: a misconfigured test should
    # fail too. Fail-closed because silently falling back to in-memory
    # when the operator asked for Postgres would be the same class of
    # bug as Drift #7.
    if (
            settings.use_postgres_runs
            or settings.use_postgres_dashboard_artifacts
            or settings.use_postgres_comparisons
            or settings.use_postgres_idempotency
            or settings.use_postgres_conversation_summaries
            or settings.use_postgres_audit_events
    ) and not settings.database_url:
        raise FatalConfigurationError(
            "use_postgres_runs / use_postgres_dashboard_artifacts / "
            "use_postgres_comparisons / use_postgres_idempotency / "
            "use_postgres_conversation_summaries / use_postgres_audit_events "
            "require database_url to be set. Cannot construct a Postgres "
            "repository without a database URL."
        )

    # Turn 2.6 plan v0.2.1 §6.4 R-8 coupling guardrail: in
    # staging/production, comparison and idempotency persistence must be
    # coupled. Diverging modes (Postgres comparisons + in-memory
    # idempotency, or vice versa) lose the durability guarantee that
    # makes idempotency keys actually idempotent across restarts. In
    # dev/test, mixed mode is allowed so operators can iterate.
    #
    # Conversation summaries are intentionally NOT coupled here: they
    # have no shared durability invariant with comparisons or
    # idempotency. Coupling them would over-constrain operators who
    # legitimately want PG-backed conversations + in-memory comparisons
    # in dev/test.
    if settings.app_env in ("staging", "production"):
        if settings.use_postgres_comparisons != settings.use_postgres_idempotency:
            raise FatalConfigurationError(
                f"In app_env='{settings.app_env}', use_postgres_comparisons "
                f"({settings.use_postgres_comparisons}) and "
                f"use_postgres_idempotency "
                f"({settings.use_postgres_idempotency}) must match. "
                f"Mixed mode is allowed only in app_env in "
                f"('development', 'test')."
            )


def _warn_on_risky_combinations(settings: AppSettings) -> None:
    """Soft guardrails — log a warning, do not abort.

    Combinations that are unusual but legal. Surfaces them in operator
    logs so misconfigurations in dev/staging are visible and so
    deliberate-but-risky production overrides are not silent.
    """
    # --- Turn 4 §3.5 SR-1: risky-combination warning -----------------
    # An explicit allow_self_serve_provisioning=True in staging or
    # production is legal (operator override) but should be loudly
    # logged so enterprise security teams see it in structured logs.
    # Non-fatal by design — the operator has the authority to make this
    # call; ceremony without a force-function becomes noise.
    if (
        settings.app_env in ("staging", "production")
        and settings.allow_self_serve_provisioning is True
    ):
        logger.warning(
            "Configuration notice: allow_self_serve_provisioning=True is "
            "ENABLED in app_env=%s. This permits automatic tenant + "
            "workspace creation on first successful external auth. "
            "Confirm this matches the intended operational posture. To "
            "disable, unset ALLOW_SELF_SERVE_PROVISIONING or set it to "
            "false.",
            settings.app_env,
        )

    if settings.app_env != "production":
        if settings.auth_enabled and not settings.database_url:
            logger.warning(
                "AppSettings: auth_enabled=True with no database_url "
                "(app_env=%s). TenantContextMiddleware will NOT be "
                "registered because the bootstrap sessionmaker cannot "
                "be built.",
                settings.app_env,
            )
        if (
            settings.auth_provider == "clerk"
            and settings.app_env == "test"
            and not all(
                (
                    settings.clerk_jwks_url,
                    settings.clerk_issuer,
                    settings.clerk_audience,
                )
            )
        ):
            logger.warning(
                "AppSettings: auth_provider='clerk' in app_env='test' "
                "without all Clerk env vars set; provider construction "
                "will likely fail."
            )


# ---------------------------------------------------------------------------
# Sub-builders — small, isolated pieces of the wiring graph
# ---------------------------------------------------------------------------


def _build_engine(settings: AppSettings) -> "AsyncEngine | None":
    """Construct (or reuse) the shared async engine.

    Returns None when ``settings.database_url`` is None and
    ``app_env="test"`` — this is the only combo that's legal because
    production guardrails forbid a None URL there.

    Implementation note: ``src.db.session`` exposes module-level
    ``get_engine()`` / ``get_sessionmaker()`` plus a
    ``configure_engine_from_url(url)`` override. The factory uses the
    override hook so the existing tenant_scoped_session machinery
    keeps working.

    Future-4 idempotency contract (2026-05-08, refined v0.4.1)
    ---------------------------------------------------------
    ``configure_engine_from_url`` nulls module-level
    ``_engine`` / ``_sessionmaker`` WITHOUT awaiting an async
    dispose. Calling it mid-test — after a prior session has just
    committed rows — has been observed to break visibility for
    sessions built from the rebuilt engine, under the suite-mode
    interaction with NullPool + asyncpg connection lifecycle. That
    broke ``test_factory_built_app_serves_one_authenticated_request_end_to_end_postgres``.

    The contract this function now enforces: **if a global engine
    is already configured, reuse it.** The caller responsible for
    initial configuration is either:
      - production: ``create_app`` is invoked exactly once at boot,
        so the first call hits the configure path; subsequent calls
        (if any) reuse.
      - tests: the conftest ``_configure_engine`` autouse fixture +
        per-function ``clean_db`` fixture (which calls
        ``await reset_engine()`` then ``get_sessionmaker()``) ensure
        ``_engine`` is a fresh, test-owned engine when the test body
        runs.

    URL-equality is intentionally NOT used for the gate: pydantic
    AppSettings validation can canonicalize ``settings.database_url``
    into a form that doesn't string-equal the raw env var the
    conftest set. Engine-presence is unambiguous; URL strings
    aren't. See ``tests/test_app_factory_engine_idempotency.py``.
    """
    if not settings.database_url:
        # Test-only path. Production guardrails would have aborted by now.
        return None

    # Future-4: if the global engine is already configured, reuse it.
    # See the docstring above for why string-comparing URLs is wrong.
    from src.db import session as _db_session
    if _db_session._engine is not None:
        return _db_session._engine

    from src.db.session import configure_engine_from_url, get_engine
    configure_engine_from_url(str(settings.database_url))
    return get_engine()


def _build_sessionmaker(
    engine: "AsyncEngine | None",
) -> "async_sessionmaker[AsyncSession] | None":
    """Return the shared async session factory, or None when engine is None."""
    if engine is None:
        return None
    from src.db.session import get_sessionmaker

    return get_sessionmaker()


def _build_auth_provider(settings: AppSettings):
    """Construct the auth provider per ``settings.auth_provider``.

    Production guardrails have already rejected ``dev`` in production
    by the time this is called, so the only branch decisions here are
    on the provider name.
    """
    if settings.auth_provider == "dev":
        from src.auth.dev_provider import DevAuthProvider

        # Map app_env to the provider's environment whitelist.
        # DevAuthProvider accepts {"development", "test", "local"}; we
        # pass the app_env through directly. Production guardrails
        # ensure we never reach here under app_env="production".
        env_for_provider = (
            settings.app_env if settings.app_env != "staging" else "development"
        )
        return DevAuthProvider(environment=env_for_provider)

    if settings.auth_provider == "clerk":
        from src.auth.clerk_provider import ClerkAuthProvider

        # Clerk constructor is keyword-only (jwks_url, issuer, audience,
        # environment, jwks_cache). Kwarg names mirror the canonical
        # spelling.
        kwargs: dict = {
            "jwks_url": settings.clerk_jwks_url,
            "issuer": settings.clerk_issuer,
            "audience": settings.clerk_audience,
            "environment": settings.app_env,
        }
        try:
            return ClerkAuthProvider(**kwargs)
        except TypeError:
            # Fallback: not all installed snapshots accept `environment`.
            kwargs.pop("environment", None)
            return ClerkAuthProvider(**kwargs)

    # Pydantic Literal validation prevents reaching this branch.
    raise FatalConfigurationError(
        f"Unknown auth_provider: {settings.auth_provider!r}"
    )


def _build_comparison_provider(settings: AppSettings):
    """Construct the ComparisonProvider used by ComparisonService.

    Per amendment §4.4 / Step 0 finding: ``src.comparisons.provider``
    ships ``get_comparison_provider()`` which returns a
    ``FailClosedComparisonProvider``. Tests that need a different
    provider re-bind via ``app.dependency_overrides`` after
    ``create_app`` returns — same pattern they use for service overrides.
    """
    from src.comparisons.provider import get_comparison_provider

    return get_comparison_provider()


# ---------------------------------------------------------------------------
# Service binding — replaces v0.2 _bind_repositories per amendment §3.8
# ---------------------------------------------------------------------------


def _bind_services(app: FastAPI, settings: AppSettings) -> None:
    """Bind Week 6a router service stubs to fully-constructed services.

    See amendment §3.8 for the full rationale. Brief recap:

    * The live Week 6a wiring exposes its dependency seam at the SERVICE
      layer (``_get_comparison_service``, ``_get_conversation_service``,
      ``_get_dashboard_service``). Each stub raises ``RuntimeError`` if
      not overridden, so calling ``create_app()`` without this helper
      would leave the routers uncallable.
    * The Protocol seam (RunRepository / ComparisonRepository /
      DashboardArtifactRepository) is preserved one layer inside this
      function: each ``InMemory*Repository()`` call is the only line
      that must change to swap in a Postgres-backed implementation in
      Turn 2.5. No router or service code changes when that swap
      happens.
    * RunService is constructed ONCE and shared by both
      ComparisonService and DashboardService so a run created via the
      comparison flow is visible to the dashboard and vice versa.
      Tested via the ``cmp_svc._runs is dash_svc._runs`` invariant
      (test #7 in amendment §7.3).
    """
    # --- Imports kept local to keep app_factory import-cheap ---
    from src.comparisons.idempotency import (
        InMemoryIdempotencyStore,
        PostgresIdempotencyRepository,
    )
    from src.comparisons.repository import (
        InMemoryComparisonRepository,
        PostgresComparisonRepository,
    )
    from src.comparisons.router import _get_comparison_service
    from src.comparisons.service import ComparisonService
    from src.conversations.repository import (
        InMemoryConversationSummaryRepository,
        PostgresConversationSummaryRepository,
    )
    from src.conversations.router import _get_conversation_service
    from src.conversations.service import ConversationService
    from src.results.cache import dashboard_cache
    from src.results.repository import InMemoryDashboardArtifactRepository
    from src.results.router import _get_dashboard_service
    from src.results.service import DashboardService
    from src.runs.repository import InMemoryRunRepository, PostgresRunRepository
    from src.runs.service import RunService
    from src.storage.factory import get_storage_adapter

    # --- Construct the per-app object graph ---
    # Turn 2.5 plan v0.2.1 §3.4 D-Settings + D-Wiring: feature-switch
    # wiring. Default in-memory; Postgres impls wired when settings flag
    # the switch. The cross-field guardrail in
    # `_enforce_production_guardrails` already rejected
    # `use_postgres_*=True` with `database_url=None` upstream.
    if settings.use_postgres_runs:
        run_repo = PostgresRunRepository()
    else:
        run_repo = InMemoryRunRepository()
    run_service = RunService(run_repo)

    if settings.use_postgres_comparisons:
        cmp_repo = PostgresComparisonRepository()
    else:
        cmp_repo = InMemoryComparisonRepository()
    if settings.use_postgres_idempotency:
        idempotency_store = PostgresIdempotencyRepository()
    else:
        idempotency_store = InMemoryIdempotencyStore()
    comparison_provider = _build_comparison_provider(settings)
    comparison_service = ComparisonService(
        repo=cmp_repo,
        run_service=run_service,
        provider=comparison_provider,
        idempotency=idempotency_store,
    )

    # use_postgres_dashboard_artifacts wiring lands in Step 3 with the
    # PostgresDashboardArtifactRepository. The settings field is already
    # in place but currently has no Postgres alternative to switch to.
    if settings.use_postgres_dashboard_artifacts:
        from src.results.repository import PostgresDashboardArtifactRepository
        art_repo = PostgresDashboardArtifactRepository()
    else:
        # AM-5 Path A (Turn 2.6 plan v0.2.1 §4 Step 6): inject the run
        # repo so in-memory upsert_artifacts validates run existence,
        # mirroring PostgresDashboardArtifactRepository's MF-3 behaviour.
        # Postgres dashboard impl validates at the DB layer; we only
        # inject in the in-memory branch.
        art_repo = InMemoryDashboardArtifactRepository(run_existence=run_repo)
    dashboard_service = DashboardService(
        run_service=run_service,
        artifact_repo=art_repo,
        cache=dashboard_cache,
    )

    storage_adapter = get_storage_adapter()
    if settings.use_postgres_conversation_summaries:
        conv_summary_repo = PostgresConversationSummaryRepository()
    else:
        conv_summary_repo = InMemoryConversationSummaryRepository()
    conversation_service = ConversationService(
        storage=storage_adapter,
        summary_repo=conv_summary_repo,
    )

    # Slice 6 (Plan v0.2 §B.5): bind the audit_logger module singleton to
    # PostgresAuditEventRepository when use_postgres_audit_events=True.
    # The cross-field guardrail in _enforce_production_guardrails already
    # rejected this combination with database_url=None. Module singleton
    # is mutated in-place via bind_repository (Slice 6 Q1=B2) —
    # re-instantiation would leave cached references in 9 production +
    # 16 test files stale.
    if settings.use_postgres_audit_events:
        from src.audit.logger import audit_logger
        from src.audit.repository import PostgresAuditEventRepository
        audit_logger.bind_repository(PostgresAuditEventRepository())

    # --- Stash on app.state for tests / introspection ---
    app.state.run_service = run_service
    app.state.comparison_service = comparison_service
    app.state.dashboard_service = dashboard_service
    app.state.conversation_service = conversation_service
    app.state.conv_summary_repo = conv_summary_repo

    # DIAGNOSTIC-ONLY (RC-7): routers, services, and dependencies receive
    # repos via constructor injection (e.g. RunService(run_repo)) or via
    # app.dependency_overrides. Production code paths must NOT consume
    # app.state.run_repo or app.state.dashboard_artifact_repo. These
    # handles exist only for tests (e.g. wiring tests W1-W7) and for
    # diagnostic introspection in later sprints.
    app.state.run_repo = run_repo
    app.state.cmp_repo = cmp_repo
    app.state.idempotency_store = idempotency_store
    app.state.dashboard_artifact_repo = art_repo

    # --- Apply dependency_overrides bindings ---
    app.dependency_overrides[_get_comparison_service] = lambda: comparison_service
    app.dependency_overrides[_get_conversation_service] = lambda: conversation_service
    app.dependency_overrides[_get_dashboard_service] = lambda: dashboard_service


# ---------------------------------------------------------------------------
# The composition root
# ---------------------------------------------------------------------------


def create_app(*, settings: AppSettings | None = None) -> FastAPI:
    """Production composition root.

    Parameters
    ----------
    settings:
        An ``AppSettings`` instance. When ``None``, settings are loaded
        from environment / .env (the production-entry-point pattern).
        Tests pass an explicit instance.
    """
    if settings is None:
        settings = AppSettings()

    # ---- Guardrails (fatal first, then warning) ----
    _enforce_production_guardrails(settings)
    _warn_on_risky_combinations(settings)

    # ---- Sub-builders ----
    engine = _build_engine(settings)
    sessionmaker = _build_sessionmaker(engine)
    provider = _build_auth_provider(settings)

    # ---- App + middleware ----
    app = FastAPI(
        title="AI SimTest Control Plane API",
        version="0.6.0a1",
        description="Week 6a foundation — runs, results, comparisons.",
    )

    # Stash settings on app.state for introspection / tests.
    app.state.settings = settings

    # CorrelationId middleware lives in src.main; importing here keeps
    # the dependency one-way (app_factory -> main, not main -> factory
    # at module load). The shim in src.main re-exports create_app so
    # there's no circular import at import time.
    from src.main import CorrelationIdMiddleware

    # Starlette runs middleware in reverse-add order. Add correlation
    # FIRST so it ends up OUTERMOST. Tenant context is added second
    # (when applicable) so it sits INNER and runs after correlation
    # has set request.state.correlation_id.
    app.add_middleware(CorrelationIdMiddleware)

    if settings.auth_enabled and sessionmaker is not None:
        from src.auth.middleware import TenantContextMiddleware

        app.add_middleware(
            TenantContextMiddleware,
            provider=provider,
            sessionmaker=sessionmaker,
            exempt_paths=settings.exempt_paths,
        )
    elif settings.auth_enabled and sessionmaker is None:
        # Legal only in app_env="test" with database_url=None.
        # Production guardrails forbid this combination. The warning
        # was already logged in _warn_on_risky_combinations.
        logger.warning(
            "auth_enabled=True but sessionmaker is None — "
            "TenantContextMiddleware NOT registered. "
            "Tests that need auth must construct their own settings "
            "with a SQLite URL."
        )

    # ---- Exception handler ----
    from src.api.errors import APIError, api_error_handler

    app.add_exception_handler(APIError, api_error_handler)

    # ---- Hook wiring (unchanged from current main.py) ----
    from src.results.cache import dashboard_cache
    from src.runs.service import set_dashboard_invalidator

    set_dashboard_invalidator(dashboard_cache.invalidate)

    # ---- Router mounting (3 routers — see amendment §1.3) ----
    from src.comparisons.router import router as comparisons_router
    from src.conversations.router import router as conversations_router
    from src.results.router import router as results_router

    app.include_router(conversations_router, prefix="/v1")
    app.include_router(results_router, prefix="/v1")
    app.include_router(comparisons_router, prefix="/v1")

    # ---- Service bindings (replaces v0.2 _bind_repositories) ----
    _bind_services(app, settings)

    return app
