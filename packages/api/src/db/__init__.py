"""Postgres persistence layer (Postgres + Auth Wiring Sprint Turn 1).

This package provides:
  - `src.db.base.Base` — the SQLAlchemy 2.x DeclarativeBase for all ORM models
  - `src.db.models` — 12 ORM models matching the plan §7.0 table inventory
  - `src.db.session` — tenant-scoped async session factory with SET LOCAL wiring (§5)
  - `src.db.mappers` — domain ↔ ORM translation layer

Turn 1 delivers schema + session + mappers only. Repository implementations land
in Turn 2. Auth wiring lands in Turn 3. App factory lands in Turn 4.
"""
