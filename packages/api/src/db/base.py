"""SQLAlchemy 2.x declarative base for all ORM models.

A single `Base` so Alembic autogenerate (if used later) sees every model
through one MetaData. The naming convention ensures all constraints and
indexes get deterministic names, which keeps migrations reviewable.
"""
from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


# Deterministic constraint / index names. SQLAlchemy uses these when generating
# DDL, so migration diffs stay stable across environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all AI SimTest ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
