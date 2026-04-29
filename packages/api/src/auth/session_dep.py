"""Request-scoped AsyncSession FastAPI dependency (Turn 3 §5.0).

The real `get_actor_role` uses this dependency to acquire a session
for membership lookup. Each request opens its own session and closes
it on teardown.

Usage in FastAPI route deps::

    async def get_actor_role(
        ctx: TenantContext = Depends(get_tenant_context),
        session: AsyncSession = Depends(get_request_session),
    ) -> Role:
        repo = PostgresMembershipRepository()
        role = await repo.get_role(..., session=session)
        ...

Why a generator (yield) rather than returning a session: FastAPI's
DI container treats `async def` + `yield` as a context manager —
the code after `yield` runs on teardown, closing the session
regardless of whether the route raised.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_sessionmaker

__all__ = ["get_request_session"]


async def get_request_session() -> AsyncIterator[AsyncSession]:
    """Yield a fresh AsyncSession for the duration of the request.

    No tenant scoping is applied here — callers that need RLS
    enforcement should use `tenant_scoped_session` from `src/db/session.py`
    instead. This dep is for middleware-adjacent, bootstrap, and
    authz lookups that read across tenants or run BEFORE the
    TenantContext is populated.
    """
    sm = get_sessionmaker()
    session = sm()
    try:
        yield session
    finally:
        await session.close()
