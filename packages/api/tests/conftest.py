"""Shared pytest fixtures for Week 5 test suites."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio


# Ensure `src.*` is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.audit.logger import audit_logger  # noqa: E402
from src.common.models import ActorRef, TenantContext  # noqa: E402
from src.storage.local import LocalFilesystemAdapter  # noqa: E402


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
