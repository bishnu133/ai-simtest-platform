"""Slice 5: PG integration tests for the AuditLogger async binding.

Two sacred tests (locked at Plan v0.2):
  1. test_aemit_tenant_event_persists_row_via_postgres_repository_end_to_end
  2. test_aemit_pretenant_event_persists_row_via_security_definer_end_to_end

These prove the AuditLogger → AuditEventRepository binding works against
real Postgres. Repository-level PG correctness (RLS, FORCE RLS bypass via
SECURITY DEFINER, action allowlist, etc.) is already covered by the 11
sacred tests in tests/db/test_postgres_audit_event_repository.py. These
integration tests assert only the LOGGER binding — not deeper PG semantics.

Schema-verify trail:
  - Plan v0.2.1: tenants required columns are (id, name, slug); plan_id /
    settings / created_at / updated_at all have server defaults. status
    is NOT a column on tenants. Verified against src/db/models.py:Tenant.
  - Plan v0.2.1: workspaces has no slug column (Slice 4 D2 / commit 6b90b68).
  - Plan v0.2.1: seed pattern mirrors _ensure_test_tenants_and_workspaces in
    tests/db/test_postgres_audit_event_repository.py — raw_admin_session
    bypasses RLS for seed-time privileged INSERTs.
  - Plan v0.2.2: tenant_scoped_session(tid) takes a STRING tenant id, not
    a UUID object (see src/db/session.py:191 type-validation guard).
    AuditContext.tenant_id stays a UUID (Slice 1 domain type contract);
    only the tenant_scoped_session argument is pre-stringified.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from src.audit.context import (
    AuditContext,
    PretenantAuditEvent,
    TenantAuditEvent,
)
from src.audit.logger import AuditLogger
from src.audit.repository import PostgresAuditEventRepository
from src.db.session import (
    raw_admin_session,
    tenant_scoped_session,
)


# Fixed test tenant/workspace IDs to keep the seed idempotent across runs.
_TENANT_ID_STR = "11111111-1111-1111-1111-555555555501"
_WORKSPACE_ID_STR = "22222222-2222-2222-2222-555555555501"
_TENANT_UUID = UUID(_TENANT_ID_STR)
_WORKSPACE_UUID = UUID(_WORKSPACE_ID_STR)


async def _seed_tenant_and_workspace() -> None:
    """Idempotent seed for Slice 5 integration tenant + default workspace.

    Mirrors the existing pattern in
    tests/db/test_postgres_audit_event_repository.py::_ensure_test_tenants_and_workspaces
    — uses raw_admin_session for privileged seed-time INSERTs that bypass
    RLS, with ON CONFLICT (id) DO NOTHING for idempotency.

    Schema verified against src/db/models.py:Tenant (Plan v0.2.1):
      - tenants required columns: id, name, slug. plan_id / settings /
        created_at / updated_at all have server_default values. There is
        no status column on tenants.
      - workspaces required columns: id, tenant_id, name. is_default
        defaults to false; no slug column (Slice 4 D2 / commit 6b90b68).
    """
    async with raw_admin_session() as s:
        await s.execute(
            sa.text(
                """
                INSERT INTO tenants (id, name, slug)
                VALUES (:tid, 'Slice5 Async Tenant', 'slice5-async-tenant')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"tid": _TENANT_ID_STR},
        )
        await s.execute(
            sa.text(
                """
                INSERT INTO workspaces (id, tenant_id, name, is_default)
                VALUES (:wid, :tid, 'Slice5 Async Workspace', true)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"wid": _WORKSPACE_ID_STR, "tid": _TENANT_ID_STR},
        )


# ---------------------------------------------------------------------------
# 2 sacred PG integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aemit_tenant_event_persists_row_via_postgres_repository_end_to_end(
    clean_db,
    migrated_db,
):
    """AuditLogger + PostgresAuditEventRepository → real row in audit_events.

    Verifies the Slice 5 binding works under tenant_scoped_session with
    app_user RLS enforcement (Slice 0 migrations 0005-0008).
    """
    await _seed_tenant_and_workspace()

    repository = PostgresAuditEventRepository()
    audit_logger_under_test = AuditLogger(repository=repository)

    correlation = f"cor_slice5_pg_t_{uuid4().hex[:8]}"
    event = TenantAuditEvent(
        action="asset.created",
        context=AuditContext(
            tenant_id=_TENANT_UUID,
            actor_id="user_slice5_pg",
            actor_type="human",
            workspace_id=_WORKSPACE_UUID,
            correlation_id=correlation,
        ),
        resource_type="asset",
        resource_id="asset_slice5_pg_t",
        details={"slice": 5, "path": "logger_async_pg"},
    )

    # tenant_scoped_session takes a string tenant id (Plan v0.2.2 — see
    # src/db/session.py:191 type guard).
    async with tenant_scoped_session(_TENANT_ID_STR) as session:
        new_id = await audit_logger_under_test.aemit_tenant_event(
            event, session=session
        )

    assert isinstance(new_id, UUID)

    # Verify the row landed in audit_events under tenant-scoped visibility.
    async with tenant_scoped_session(_TENANT_ID_STR) as verify_session:
        row_id = await verify_session.scalar(
            sa.text("SELECT id FROM audit_events WHERE id = :id"),
            {"id": str(new_id)},
        )
    assert row_id == new_id


@pytest.mark.asyncio
async def test_aemit_pretenant_event_persists_row_via_security_definer_end_to_end(
    clean_db,
    migrated_db,
):
    """AuditLogger + PostgresAuditEventRepository → pretenant row inserted via
    the SECURITY DEFINER function audit_pretenant_insert (Slice 0).

    Pretenant rows have tenant_id IS NULL and are invisible to ordinary
    tenant_scoped_session readers. Verification uses raw_admin_session()
    to confirm the row landed.
    """
    repository = PostgresAuditEventRepository()
    audit_logger_under_test = AuditLogger(repository=repository)

    correlation = f"cor_slice5_pg_pre_{uuid4().hex[:8]}"
    event = PretenantAuditEvent(
        action="auth.rejected",
        actor_id="anonymous_slice5",
        actor_type="system",
        details={"reason": "missing_bearer_token", "ip": "127.0.0.1"},
        correlation_id=correlation,
    )

    new_id = await audit_logger_under_test.aemit_pretenant_event(event)

    assert isinstance(new_id, UUID)

    # Pretenant rows are NULL tenant_id — verify via admin (non-RLS) session.
    async with raw_admin_session() as verify_session:
        row = (
            await verify_session.execute(
                sa.text(
                    "SELECT id, tenant_id FROM audit_events WHERE id = :id"
                ),
                {"id": str(new_id)},
            )
        ).one()
    assert row.id == new_id
    assert row.tenant_id is None
