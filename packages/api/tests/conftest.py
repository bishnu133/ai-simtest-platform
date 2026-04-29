"""Shared pytest fixtures for Week 5 test suites."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Ensure `src.*` is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.audit.logger import audit_logger  # noqa: E402
from src.common.models import ActorRef, TenantContext  # noqa: E402
from src.storage.local import LocalFilesystemAdapter  # noqa: E402

# Re-export DB fixtures so they are visible to sibling suites like tests/auth/.
# These fixtures physically live in tests/db/conftest.py, but auth tests cannot
# see sibling conftests automatically.
from tests.db.conftest import (  # noqa: E402,F401
    _configure_engine,
    admin_session,
    clean_db,
    migrated_db,
    pg_instance,
    pg_url,
)


@pytest.fixture
def local_adapter(tmp_path):
    """Fresh LocalFilesystemAdapter per test, isolated in tmp_path."""
    return LocalFilesystemAdapter(
        root=str(tmp_path / "storage"),
        bucket="test-bucket",
        signing_secret="test-secret",
    )


@pytest.fixture
def actor_alice() -> ActorRef:
    return ActorRef(
        actor_id="user_alice",
        actor_type="human",
        display_name="Alice",
        email="alice@example.com",
    )


@pytest.fixture
def actor_bob() -> ActorRef:
    return ActorRef(
        actor_id="user_bob",
        actor_type="human",
        display_name="Bob",
        email="bob@example.com",
    )


@pytest.fixture
def ctx_tenant_a(actor_alice) -> TenantContext:
    return TenantContext(
        tenant_id="tenant_a",
        workspace_id="workspace_main",
        actor=actor_alice,
        correlation_id="corr-a-1",
    )


@pytest.fixture
def ctx_tenant_b(actor_bob) -> TenantContext:
    return TenantContext(
        tenant_id="tenant_b",
        workspace_id="workspace_main",
        actor=actor_bob,
        correlation_id="corr-b-1",
    )


@pytest.fixture(autouse=True)
def reset_audit_log():
    """Clear the audit log before every test to prevent cross-test pollution."""
    audit_logger.clear()
    yield
    audit_logger.clear()


# ---------------------------------------------------------------------------
# Turn 4 §6.3 — factory-built app fixtures
#
# These are additive (no edit to existing fixtures). Tests that want
# the Turn 4 composition-root path use `factory_app`; tests that want
# the legacy `src.main.create_app()` path keep using whatever they
# already use.
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings():
    """Default AppSettings for factory-built apps in tests.

    app_env="test" disables several guardrails and lets database_url
    be None. auth_provider="dev" picks the DevAuthProvider. auth_enabled
    =False keeps the middleware out of the way for tests that don't
    exercise the auth path.
    """
    from src.config import AppSettings

    return AppSettings(
        app_env="test",
        auth_provider="dev",
        auth_enabled=False,
    )


@pytest.fixture
def factory_app(test_settings):
    """An app built via the Turn 4 composition root, no auth middleware."""
    from src.app_factory import create_app

    return create_app(settings=test_settings)