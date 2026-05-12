"""Alembic environment — async mode, reads DATABASE_URL from the environment.

Turn 1 only needs online mode (against a real Postgres). Offline mode is
left as a stub because it isn't exercised by the sprint.
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.db.base import Base

# Ensure every model module is imported so Base.metadata is fully populated.
# (Without this, autogenerate would miss tables declared in models.py.)
import src.db.models  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("AI_SIMTEST_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it before running alembic "
            "(postgresql+asyncpg://user:pass@host:5432/db)."
        )
    return url


def run_migrations_offline() -> None:
    """Offline mode — not exercised in Turn 1, stub for completeness."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _include_object(object, name, type_, reflected, compare_to):  # noqa: ARG001
    """Autogenerate-comparison filter — exclude objects managed outside
    the SQLAlchemy declarative layer.

    Currently exclusion list:

    * `audit_events_*` partition CHILD tables and any indexes on them.

      The `audit_events` parent table is declared in `src/db/models.py`
      (class `AuditEvent`). Its monthly partition children
      (e.g. `audit_events_2026_05`) and the catch-all `audit_events_default`
      are created by raw SQL in migration 0001 because SQLAlchemy's
      declarative layer does not model `PARTITION BY` natively
      (see src/db/models.py:22-26 "Note on `audit_events`").

      Without this filter, `alembic check` and `alembic revision --autogenerate`
      would propose to drop these reflected partitions on every run because
      they aren't in `Base.metadata` — a false positive that buries real
      drift signal.

    Drift 1.5 (Turn 2.7, plan v0.2.1): added after Drift 1's
    `test_alembic_check_is_clean_after_0004` test surfaced the partition-
    table false positive. The filter is intentionally narrow — only
    `audit_events` partitions are excluded; everything else is compared
    normally so the canary still catches real model/migration drift.
    """
    if type_ == "table" and (
        name.startswith("audit_events_2") or name == "audit_events_default"
    ):
        return False
    if type_ == "index" and name is not None and (
        name.startswith("audit_events_2") or name.startswith("audit_events_default")
    ):
        return False
    return True


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Preserve deterministic comparison order for autogen diffs.
        compare_type=True,
        compare_server_default=True,
        # Drift 1.5: exclude audit_events partition children (raw-SQL
        # managed in migration 0001) from autogen comparison.
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Online mode — build an async engine from DATABASE_URL and run."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
