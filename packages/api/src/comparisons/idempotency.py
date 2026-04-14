"""Idempotency store for POST /v1/comparisons (v1.2.2 §11.3)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

IDEMPOTENCY_TTL_SECONDS = 86400  # 24h


def canonical_hash(workspace_id: str, left_run_id: str, right_run_id: str) -> str:
    """Stable hash of the request body fields. Per v1.2.2 §11.3."""
    payload = json.dumps(
        {
            "workspace_id": workspace_id,
            "left_run_id": left_run_id,
            "right_run_id": right_run_id,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class _Entry:
    body_hash: str
    comparison_id: str
    expires_at: float


class InMemoryIdempotencyStore:
    def __init__(self, ttl_seconds: int = IDEMPOTENCY_TTL_SECONDS):
        self._store: dict[tuple[str, str], _Entry] = {}
        self._ttl = ttl_seconds

    def _key(self, workspace_id: str, idempotency_key: str):
        return (workspace_id, idempotency_key)

    def lookup(self, workspace_id: str, idempotency_key: str):
        """Return (entry, expired) or (None, False) if absent."""
        entry = self._store.get(self._key(workspace_id, idempotency_key))
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._store[self._key(workspace_id, idempotency_key)]
            return None
        return entry

    def remember(
        self,
        workspace_id: str,
        idempotency_key: str,
        body_hash: str,
        comparison_id: str,
    ):
        self._store[self._key(workspace_id, idempotency_key)] = _Entry(
            body_hash=body_hash,
            comparison_id=comparison_id,
            expires_at=time.monotonic() + self._ttl,
        )
