"""Dashboard cache with state-aware TTL (v1.2.2 §M4).

Active runs (queued/running): TTL 60s, invalidated on every state transition.
Terminal runs (completed/failed/cancelled): TTL 24h — data is immutable,
hot-path re-opens are common.

Primary invalidation is event-driven via the explicit invalidator hook
wired into RunStateTransition. TTL is secondary protection.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from src.results.models import DashboardSummary
from src.runs.models import RunStatus

DASHBOARD_CACHE_TTL_ACTIVE_SECONDS = 60
DASHBOARD_CACHE_TTL_TERMINAL_SECONDS = 86400


@dataclass
class _Entry:
    value: DashboardSummary
    expires_at: float


class DashboardCache:
    def __init__(
        self,
        active_ttl: int = DASHBOARD_CACHE_TTL_ACTIVE_SECONDS,
        terminal_ttl: int = DASHBOARD_CACHE_TTL_TERMINAL_SECONDS,
    ) -> None:
        self._store: dict[str, _Entry] = {}
        self._active_ttl = active_ttl
        self._terminal_ttl = terminal_ttl

    def _ttl_for(self, status: RunStatus) -> int:
        return self._terminal_ttl if status.is_terminal else self._active_ttl

    def get(self, run_id: str) -> DashboardSummary | None:
        entry = self._store.get(run_id)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._store[run_id]
            return None
        return entry.value

    def set(self, run_id: str, value: DashboardSummary) -> None:
        ttl = self._ttl_for(value.run_status)
        self._store[run_id] = _Entry(value=value, expires_at=time.monotonic() + ttl)

    def invalidate(self, run_id: str) -> None:
        self._store.pop(run_id, None)

    def clear(self) -> None:
        self._store.clear()


# Module-level singleton wired by main.create_app() into runs.set_dashboard_invalidator
dashboard_cache = DashboardCache()
