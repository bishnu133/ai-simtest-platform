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


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Preserve deterministic comparison order for autogen diffs.
        compare_type=True,
        compare_server_default=True,
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
