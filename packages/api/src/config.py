"""Turn 4 §3.3 — AppSettings (pydantic-settings).

The single source of truth for runtime configuration consumed by
``src/app_factory.py``. The class is constructible three ways:

  1. From environment variables / .env (production entry points)
  2. With explicit kwargs (tests pass these inline; no .env reading)
  3. Via the model_validate hook (e.g. for round-trip tests)

The factory NEVER reads env vars directly outside of this module.
Tests that want a clean configuration construct an ``AppSettings``
explicitly and pass it through ``create_app(settings=...)``.

Guardrail design (Turn 4 §3.4–3.5)
----------------------------------
Two guardrail layers, both enforced inside ``app_factory.create_app``,
not here:

  - ``_enforce_production_guardrails(settings)`` raises
    ``FatalConfigurationError`` if a combination is dangerous in
    production (e.g. ``app_env="production"`` with ``auth_provider="dev"``
    or ``auth_enabled=False`` or no ``database_url``).

  - ``_warn_on_risky_combinations(settings)`` logs a warning for
    combinations that are unusual but not fatal (e.g. test env with
    auth enabled and no DB).

Field-level validators here only enforce *value-level* legality
(strings non-empty, allowed choices, URL shapes). They do not enforce
cross-field combinations — that is the factory's job.
"""
from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Allowed app environments. ``test`` is the only env that may legally
# run with no database_url and auth_provider="dev".
AppEnv = Literal["development", "test", "staging", "production"]

# Allowed auth providers. ``dev`` accepts header-based identity for
# local development; ``clerk`` is the production provider.
AuthProvider = Literal["dev", "clerk"]


# Default exempt paths — middleware short-circuits these. Health and
# docs endpoints must always pass without auth so probes / clients can
# reach them. Tests may override this list via explicit settings.
DEFAULT_EXEMPT_PATHS: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class AppSettings(BaseSettings):
    """Runtime configuration for the Control Plane API.

    See module docstring for the construction patterns and the
    guardrail layering (field validators here vs. cross-field
    guardrails in ``app_factory``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Tests construct AppSettings(...) inline with explicit kwargs;
        # those kwargs override env. Anything not passed falls back to
        # env, then to field defaults below.
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Environment + auth flags
    # ------------------------------------------------------------------
    app_env: AppEnv = Field(
        default="development",
        description=(
            "Deployment environment. Drives guardrail strictness in "
            "app_factory. Production is the strictest."
        ),
    )
    auth_enabled: bool = Field(
        default=False,
        description=(
            "When True, app_factory registers TenantContextMiddleware. "
            "When False, the middleware is skipped entirely (tests / "
            "Turn 2 backwards compat)."
        ),
    )
    auth_provider: AuthProvider = Field(
        default="dev",
        description=(
            "Which AuthProvider implementation to construct. 'dev' "
            "is header-based and refuses to run under app_env=production."
        ),
    )

    # ------------------------------------------------------------------
    # Provisioning policy (Turn 4 §3.3 / §3.10)
    # ------------------------------------------------------------------
    #
    # Controls whether bootstrap may create a tenant row on first
    # successful external auth for an unknown org_id.
    #
    #   None  ->  "not explicitly set; derive from app_env via the
    #             _derive_provisioning_default validator below"
    #   True  ->  bootstrap auto-creates tenant + default workspace
    #             (friction-free dev loop)
    #   False ->  bootstrap raises TenantStateInvalid (409) for any
    #             unknown org_id (fail-closed enterprise posture)
    #
    # An explicit True in staging/production is legal but risky — the
    # app_factory warning log surfaces it so enterprise security teams
    # can confirm it matches the intended operational posture.
    allow_self_serve_provisioning: bool | None = Field(
        default=None,
        description=(
            "When True, bootstrap creates tenant + workspace rows on "
            "first successful external auth for an unknown org_id. When "
            "False, bootstrap raises TenantStateInvalid (409). When None "
            "(default), derive from app_env: True for development/test, "
            "False for staging/production."
        ),
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    database_url: str | None = Field(
        default=None,
        description=(
            "SQLAlchemy async URL. May be None ONLY in app_env='test'. "
            "Production guardrail rejects None; dev/staging guardrail "
            "warns but does not fail."
        ),
    )

    # ------------------------------------------------------------------
    # Persistence feature switches (Turn 2.5 plan v0.2.1 §3.4 D-Settings)
    # ------------------------------------------------------------------
    # Default False so Turn 4 wiring (in-memory repos) keeps passing
    # unchanged. Turn 2.6 may flip the defaults to True after all Cat A/B
    # repos ship. Cross-field guardrail in app_factory rejects True with
    # database_url=None per RC-6.
    use_postgres_runs: bool = Field(
        default=False,
        description=(
            "When True, app_factory wires PostgresRunRepository instead "
            "of InMemoryRunRepository. Requires database_url. Default "
            "False for backwards compatibility with Turn 4 wiring; Turn "
            "2.6 may flip to True after all Cat A/B repos ship."
        ),
    )
    use_postgres_dashboard_artifacts: bool = Field(
        default=False,
        description=(
            "When True, app_factory wires PostgresDashboardArtifactRepository "
            "instead of InMemoryDashboardArtifactRepository. Requires "
            "database_url. Default False per use_postgres_runs rationale."
        ),
    )
    use_postgres_comparisons: bool = Field(
        default=False,
        description=(
            "When True, app_factory wires PostgresComparisonRepository "
            "instead of InMemoryComparisonRepository. Requires database_url. "
            "Default False for backwards compatibility. In staging/production, "
            "MUST match use_postgres_idempotency (R-8 coupling, lands in "
            "Turn 2.6 Step 3)."
        ),
    )
    use_postgres_idempotency: bool = Field(
        default=False,
        description=(
            "When True, app_factory wires PostgresIdempotencyRepository "
            "instead of InMemoryIdempotencyStore. Requires database_url. "
            "Default False for backwards compatibility. In staging/production, "
            "MUST match use_postgres_comparisons (R-8 coupling)."
        ),
    )
    use_postgres_conversation_summaries: bool = Field(
        default=False,
        description=(
            "When True, app_factory wires PostgresConversationSummaryRepository "
            "instead of InMemoryConversationSummaryRepository. Requires "
            "database_url. Default False for backwards compatibility. "
            "Intentionally NOT coupled to comparisons/idempotency: "
            "conversation summaries have no shared durability invariant."
        ),
    )

    # Slice 6: audit_events durability switch. Mirrors the existing 5
    # use_postgres_* flags. When True, app_factory._bind_services binds
    # PostgresAuditEventRepository into the audit_logger module singleton
    # via bind_repository (Q1=B2). Default False keeps tests on the
    # InMemoryAuditEventRepository default from Slice 5.
    # Requires database_url to be set — enforced by the cross-field
    # guardrail in app_factory._enforce_production_guardrails.
    use_engine_comparison_provider: bool = Field(
        default=False,
        description=(
            "When True, ComparisonService uses the engine-backed "
            "EngineComparisonProvider instead of the fail-closed default. "
            "Requires the ai_simtest_engine package (R-4); forbidden in "
            "staging/production until a persistent run-result store exists "
            "(R-6, Engine-Integration Slice 2). Default False."
        ),
    )
    use_postgres_audit_events: bool = Field(
        default=False,
        description=(
            "Bind PostgresAuditEventRepository into the audit_logger "
            "module singleton at app_factory startup. Requires "
            "database_url. Default False per use_postgres_runs rationale."
        ),
    )

    # ------------------------------------------------------------------
    # Clerk provider configuration (only consumed when auth_provider='clerk')
    # ------------------------------------------------------------------
    clerk_jwks_url: str | None = Field(
        default=None,
        description="JWKS endpoint URL for Clerk-issued tokens.",
    )
    clerk_issuer: str | None = Field(
        default=None,
        description="Expected `iss` claim on Clerk tokens.",
    )
    clerk_audience: str | None = Field(
        default=None,
        description="Expected `aud` claim on Clerk tokens.",
    )

    # ------------------------------------------------------------------
    # Middleware behaviour
    # ------------------------------------------------------------------
    exempt_paths: tuple[str, ...] = Field(
        default=DEFAULT_EXEMPT_PATHS,
        description=(
            "Path prefixes that bypass TenantContextMiddleware "
            "(health, docs)."
        ),
    )

    # ------------------------------------------------------------------
    # Field-level validators — value legality only.
    # Cross-field guardrails live in src/app_factory.py.
    # ------------------------------------------------------------------

    @field_validator("database_url")
    @classmethod
    def _validate_database_url_shape(cls, v: str | None) -> str | None:
        """Empty string is treated the same as None (env-var convention).

        We do not validate the URL parses here — SQLAlchemy will fail
        loudly at engine construction time and that error is more
        informative than anything we'd raise here.
        """
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    @field_validator("clerk_jwks_url", "clerk_issuer", "clerk_audience")
    @classmethod
    def _strip_optional_strings(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    @field_validator("exempt_paths", mode="before")
    @classmethod
    def _coerce_exempt_paths(cls, v):
        """Allow exempt_paths to be passed as list, tuple, or comma-string.

        Env vars come in as strings; tests typically pass tuples.
        Normalize to a tuple of stripped non-empty strings.
        """
        if v is None:
            return DEFAULT_EXEMPT_PATHS
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",")]
            return tuple(p for p in parts if p)
        if isinstance(v, (list, tuple)):
            return tuple(str(p).strip() for p in v if str(p).strip())
        # Fallback — let pydantic raise its own type error.
        return v

    # ------------------------------------------------------------------
    # Cross-field model validator (Turn 4 §3.3)
    #
    # Runs AFTER all field-level validators, so field values are fully
    # populated when we read them here. Derives
    # ``allow_self_serve_provisioning`` from ``app_env`` whenever the
    # field was left at its None default — an explicit True or False
    # from env / constructor always wins.
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _derive_provisioning_default(self) -> Self:
        """Derive ``allow_self_serve_provisioning`` from ``app_env`` when
        not explicitly set.

        Derivation rule (plan §3.3):
            development, test       -> True   (friction-free dev loop)
            staging, production     -> False  (fail-closed enterprise)

        An explicit env value or constructor kwarg (True or False) is
        never overwritten. The risky-combination case (explicit True in
        staging/production) is not blocked here; it is flagged at
        startup by ``app_factory._warn_on_risky_combinations``.

        Post-condition invariant: after AppSettings construction,
        ``allow_self_serve_provisioning`` is ALWAYS a bool, never None.
        Downstream consumers (bootstrap, middleware) may assume bool
        without null-checks.
        """
        if self.allow_self_serve_provisioning is None:
            self.allow_self_serve_provisioning = self.app_env in (
                "development",
                "test",
            )
        return self
