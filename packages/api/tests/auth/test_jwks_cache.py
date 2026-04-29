"""JWKSCache tests (v0.4 §6.4) — 4 tests.

  1. Cache hit within TTL — no re-fetch
  2. Cache expiry — triggers re-fetch
  3. Forced refresh on unknown kid
  4. Concurrent refreshes serialize to one fetch

All tests inject a fake fetcher + clock. No real network, no real time.
The concurrency test uses asyncio.gather to launch multiple coroutines
simultaneously; without the lock they would all fire the fetcher.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.auth.jwks_cache import JWKSCache


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeFetcher:
    """Counts fetches and returns a configurable JWKS payload."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self.jwks = jwks
        self.call_count = 0

    async def __call__(self, url: str) -> dict[str, Any]:
        self.call_count += 1
        # Simulate a non-trivial IO call so that concurrent callers
        # genuinely overlap. Without this, asyncio might execute the
        # fetches strictly serially and the concurrency test wouldn't
        # exercise the lock path.
        await asyncio.sleep(0.01)
        return self.jwks


class _ManualClock:
    """Monotonic clock whose value the test advances manually."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def __call__(self) -> float:
        return self._t


def _jwk(kid: str) -> dict[str, Any]:
    """A minimal JWK shape for tests. Only `kid` is used by the cache
    index; other fields don't need to be cryptographically valid here."""
    return {
        "kid": kid,
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "n": "fake_modulus",
        "e": "AQAB",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_cache_hit_within_ttl() -> None:
    """Second get_key within TTL must not trigger a fetch."""
    fetcher = _FakeFetcher({"keys": [_jwk("abc")]})
    clock = _ManualClock()
    cache = JWKSCache(
        url="https://example.com/jwks",
        ttl_seconds=3600,
        fetcher=fetcher,
        clock=clock,
    )

    # First call — populates cache
    key1 = await cache.get_key("abc")
    assert key1["kid"] == "abc"
    assert fetcher.call_count == 1

    # Advance well inside TTL
    clock.advance(100)

    # Second call — cache hit, no new fetch
    key2 = await cache.get_key("abc")
    assert key2["kid"] == "abc"
    assert fetcher.call_count == 1, (
        f"Expected 1 fetch inside TTL; got {fetcher.call_count}"
    )


async def test_cache_expiry_refreshes() -> None:
    """After TTL expires, the next get_key must trigger a fetch."""
    fetcher = _FakeFetcher({"keys": [_jwk("abc")]})
    clock = _ManualClock()
    cache = JWKSCache(
        url="https://example.com/jwks",
        ttl_seconds=3600,
        fetcher=fetcher,
        clock=clock,
    )

    # First call — populates cache
    await cache.get_key("abc")
    assert fetcher.call_count == 1

    # Advance past TTL
    clock.advance(3601)

    # Next call — TTL expired, must refetch
    await cache.get_key("abc")
    assert fetcher.call_count == 2, (
        f"Expected refetch after TTL; got {fetcher.call_count} total fetches"
    )


async def test_forced_refresh_on_unknown_kid() -> None:
    """An unknown kid within TTL triggers a forced refresh, bounded by
    the per-kid cooldown.

    Shape:
      1. Populate cache with kid=abc
      2. Inside TTL, ask for kid=xyz — forced refresh fires; backend
         still returns only kid=abc; cache lookup raises KeyError
      3. Immediately ask for kid=xyz again — cooldown active, NO new
         fetch; still KeyError
      4. Advance past cooldown; backend now returns kid=xyz; ask again
         — forced refresh fires, returns kid=xyz
    """
    fetcher = _FakeFetcher({"keys": [_jwk("abc")]})
    clock = _ManualClock()
    cache = JWKSCache(
        url="https://example.com/jwks",
        ttl_seconds=3600,
        fetcher=fetcher,
        clock=clock,
        forced_refresh_cooldown=60,
    )

    # Populate cache
    await cache.get_key("abc")
    assert fetcher.call_count == 1

    # Ask for unknown kid → forced refresh (count=2), still not found
    with pytest.raises(KeyError):
        await cache.get_key("xyz")
    assert fetcher.call_count == 2, (
        "Expected forced refresh on first unknown-kid request"
    )

    # Immediately ask again — cooldown active, no new fetch
    with pytest.raises(KeyError):
        await cache.get_key("xyz")
    assert fetcher.call_count == 2, (
        f"Expected cooldown to suppress refetch; got {fetcher.call_count}"
    )

    # Advance past cooldown + rotate the backend to include xyz
    clock.advance(61)
    fetcher.jwks = {"keys": [_jwk("abc"), _jwk("xyz")]}

    # Now the refetch fires and succeeds
    key = await cache.get_key("xyz")
    assert key["kid"] == "xyz"
    assert fetcher.call_count == 3


async def test_concurrent_refresh_serialized() -> None:
    """Multiple concurrent cache misses → exactly ONE fetch.

    Thundering-herd protection: 10 coroutines launched in parallel on
    an empty cache must produce exactly one backend call, not 10.
    This is the point of the asyncio.Lock inside JWKSCache.
    """
    fetcher = _FakeFetcher({"keys": [_jwk("abc")]})
    clock = _ManualClock()
    cache = JWKSCache(
        url="https://example.com/jwks",
        ttl_seconds=3600,
        fetcher=fetcher,
        clock=clock,
    )

    # Launch 10 concurrent lookups against an empty cache
    results = await asyncio.gather(
        *(cache.get_key("abc") for _ in range(10))
    )

    # Every caller got the key
    assert len(results) == 10
    assert all(r["kid"] == "abc" for r in results)

    # But only ONE fetch fired
    assert fetcher.call_count == 1, (
        f"Expected serialized refresh (1 fetch) under concurrency; "
        f"got {fetcher.call_count}. The asyncio.Lock in JWKSCache "
        f"is failing to prevent thundering-herd re-fetches."
    )
