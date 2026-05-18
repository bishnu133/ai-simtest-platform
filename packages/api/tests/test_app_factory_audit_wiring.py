"""Slice 6: AuditLogger Postgres wiring at composition root.

Four sacred tests (locked at Plan v0.2):
  1. test_audit_logger_repository_defaults_to_inmemory_when_flag_false
  2. test_audit_logger_repository_binds_postgres_when_flag_true_with_database_url
  3. test_use_postgres_audit_events_without_database_url_raises_fatal_configuration_error
  4. test_audit_logger_module_symbol_identity_preserved_across_create_app

All tests are unit-level. No DB roundtrip:
  - PostgresAuditEventRepository() is stateless construction (Slice 4)
  - We assert object TYPE and IDENTITY, not actual persistence
  - Slice 5's tests/db/test_audit_logger_postgres.py already proves
    end-to-end persistence works against real Postgres
"""
from __future__ import annotations

import pytest

from src.app_factory import FatalConfigurationError, create_app
from src.audit.logger import audit_logger
from src.audit.repository import (
    InMemoryAuditEventRepository,
    PostgresAuditEventRepository,
)
from src.config import AppSettings


# Fake Postgres URL — never connected to. PostgresAuditEventRepository()
# is stateless construction; DB contact happens only at first aemit_*.
# SQLAlchemy create_async_engine is lazy, so engine configuration with
# this URL also does not open a connection.
_FAKE_POSTGRES_URL = (
    "postgresql+asyncpg://u:p@nonexistent-host-slice6:5432/slice6_test"
)


@pytest.fixture(autouse=True)
def _isolate_audit_logger_binding():
    """Ensure each test starts and ends with audit_logger bound to a fresh
    InMemoryAuditEventRepository.

    Slice 6 introduces test-order dependency: the audit_logger module
    singleton's _audit_repository attribute is mutated by
    create_app(use_postgres_audit_events=True). This fixture restores
    the InMemory default before AND after every test in this file so
    Slice 6 tests don't leak state into each other or into the broader
    test suite.
    """
    audit_logger.bind_repository(InMemoryAuditEventRepository())
    yield
    audit_logger.bind_repository(InMemoryAuditEventRepository())


def test_audit_logger_repository_defaults_to_inmemory_when_flag_false():
    """S6-Q2 default path: use_postgres_audit_events=False (default) →
    audit_logger stays on InMemoryAuditEventRepository.

    Preserves backward compatibility — every existing test runs without
    triggering the Postgres binding path.
    """
    settings = AppSettings(
        app_env="test",
        auth_enabled=False,
        auth_provider="dev",
        database_url=None,
        use_postgres_audit_events=False,
    )

    create_app(settings=settings)

    assert isinstance(
        audit_logger._audit_repository, InMemoryAuditEventRepository
    )


def test_audit_logger_repository_binds_postgres_when_flag_true_with_database_url():
    """S6-Q1 + S6-Q2 happy path: with use_postgres_audit_events=True and
    a database_url, _bind_services swaps audit_logger to
    PostgresAuditEventRepository.

    No actual DB connection — Slice 4's PostgresAuditEventRepository is
    stateless on construction. We assert object type only.
    """
    settings = AppSettings(
        app_env="test",
        auth_enabled=False,
        auth_provider="dev",
        database_url=_FAKE_POSTGRES_URL,
        use_postgres_audit_events=True,
    )

    create_app(settings=settings)

    assert isinstance(
        audit_logger._audit_repository, PostgresAuditEventRepository
    )


def test_use_postgres_audit_events_without_database_url_raises_fatal_configuration_error():
    """S6-Q2 cross-field guardrail: use_postgres_audit_events=True with
    no database_url is a misconfiguration. The cross-field guardrail in
    _enforce_production_guardrails must raise FatalConfigurationError
    BEFORE _bind_services runs.

    Matches the parallel guardrail for the existing 5 use_postgres_*
    flags (lines 121-133 of src/app_factory.py).
    """
    settings = AppSettings(
        app_env="test",
        auth_enabled=False,
        auth_provider="dev",
        database_url=None,
        use_postgres_audit_events=True,
    )

    with pytest.raises(FatalConfigurationError, match="use_postgres_audit_events"):
        create_app(settings=settings)


def test_audit_logger_module_symbol_identity_preserved_across_create_app():
    """S6-Q1 architectural invariant (C1 from Plan v0.1): the audit_logger
    module symbol identity is preserved across create_app calls.

    Why this matters: 9 production source files and 16 test files do
    `from src.audit.logger import audit_logger`. They cache the singleton
    by reference at import time. If create_app re-instantiated the symbol,
    those cached references would point to a stale object.

    bind_repository (the setter pattern from Q1=B2) mutates the existing
    singleton in-place rather than replacing it — this test locks that
    contract.
    """
    import src.audit.logger as audit_module

    settings_default = AppSettings(
        app_env="test",
        auth_enabled=False,
        auth_provider="dev",
        database_url=None,
        use_postgres_audit_events=False,
    )
    settings_postgres = AppSettings(
        app_env="test",
        auth_enabled=False,
        auth_provider="dev",
        database_url=_FAKE_POSTGRES_URL,
        use_postgres_audit_events=True,
    )

    initial_id = id(audit_module.audit_logger)

    create_app(settings=settings_default)
    after_default_id = id(audit_module.audit_logger)

    create_app(settings=settings_postgres)
    after_postgres_id = id(audit_module.audit_logger)

    # Singleton identity preserved through both create_app calls.
    assert initial_id == after_default_id == after_postgres_id

    # And the repository has been swapped to PostgresAuditEventRepository
    # by the second create_app — confirming bind_repository mutated the
    # existing object in-place rather than replacing it.
    assert isinstance(
        audit_module.audit_logger._audit_repository,
        PostgresAuditEventRepository,
    )
