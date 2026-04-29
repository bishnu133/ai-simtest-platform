"""Session-or-factory helper (Turn 3 plan §5.0 repo extension).

Problem: Turn 2 Postgres repositories opened their own sessions via
`raw_admin_session()` and called `session.commit()` inline. Turn 3
bootstrap needs to pass a pre-opened session into those same repos so
all the bootstrap work runs in one transaction (plan §5.6.1 retry
contract). In that mode, the repo MUST NOT commit — the outer
`async with session.begin():` block owns the commit/rollback.

This helper bridges the two patterns WITHOUT duplicating every
repo method. It yields a tuple (session, owns_session) where:

  session      — the AsyncSession to use (caller's or fresh)
  owns_session — True if the helper opened it (repo should commit
                 or rollback); False if the caller supplied it
                 (repo must NOT commit or rollback — that belongs
                 to the caller's transaction)

Usage in repo methods::

    async def create(
        self,
        *,
        name: str,
        session: AsyncSession | None = None,
    ) -> SomeRecord:
        async with use_session_or_admin(session) as (s, owns):
            orm = SomeORM(...)
            s.add(orm)
            try:
                await s.flush()
            except IntegrityError:
                if owns:
                    await s.rollback()
                raise SomeDomainException(...)
            if owns:
                await s.commit()
                await s.refresh(orm)
            return some_to_domain(orm)

When the repo opened its own session (owns=True), normal Turn 2
semantics apply: commit on success, rollback on error. When the
caller supplied a session (owns=False), the repo limits itself to
flush() so errors still surface but no transaction boundary is
asserted.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import raw_admin_session

__all__ = ["use_session_or_admin"]


@asynccontextmanager
async def use_session_or_admin(
    session: AsyncSession | None,
) -> AsyncIterator[Tuple[AsyncSession, bool]]:
    """Yield (session, owns_session).

    - session is None → open raw_admin_session(); yield (s, True).
      The repo method should commit/rollback the session itself; the
      helper's outer context manager will also commit on clean exit
      (no harm: committing an already-committed transaction is a no-op
      in SQLAlchemy).
    - session is set  → yield (s, False). The repo method MUST NOT
      commit or rollback. The caller owns the transaction.
    """
    if session is not None:
        yield session, False
        return

    async with raw_admin_session() as owned_session:
        yield owned_session, True
