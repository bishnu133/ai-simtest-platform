"""JWKS cache (Turn 3 plan §6.2 — ClerkAuthProvider infrastructure).

Responsibility: fetch + cache the JSON Web Key Set from a remote JWKS
URL, serving it to the JWT verifier. Implements three correctness
properties beyond a plain dict cache:

  1. TTL — refreshes the keyset periodically (default 1 hour). Keeps
     keys fresh without a per-request fetch.
  2. Forced refresh on unknown kid — if the token's `kid` (key id) is
     not in the cached keyset, refetch once. Clerk (and most OIDC
     providers) rotate keys without bumping the cache TTL, and the
     only signal the client has is a signed-with-unknown-kid token.
  3. Serialized concurrent refresh — two concurrent cache misses
     must trigger only ONE fetch. Without a lock, a thundering herd
     of requests on a fresh worker all fire JWKS GETs simultaneously.

The cache is asyncio-native (asyncio.Lock, not threading.Lock) because
FastAPI runs on asyncio. Every method is `async`.

Forced-refresh rate limit: at most one forced refresh per kid per
minute. Without this, a single malformed token (unknown kid repeatedly
sent by a retry client) could DoS our JWKS endpoint. Plain TTL-based
refreshes are not rate-limited because they run at most once per TTL
period by definition.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

import httpx

__all__ = ["JWKSCache", "JWKSFetcher"]


# Type alias — a callable that takes a URL and returns a parsed JWKS dict.
# Tests inject a mock; production uses _default_fetcher below.
JWKSFetcher = Callable[[str], Awaitable[dict[str, Any]]]


async def _default_fetcher(url: str) -> dict[str, Any]:
    """Default JWKS fetcher — standard httpx GET with a short timeout."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class JWKSCache:
    """TTL-cached + forced-refresh-capable JWKS store.

    Usage::

        cache = JWKSCache(url="https://example.clerk.com/.well-known/jwks.json")
        key = await cache.get_key(kid="abc123")
        # Uses cached keyset if fresh; fetches if TTL expired or kid unknown.
    """

    def __init__(
        self,
        url: str,
        ttl_seconds: int = 3600,
        fetcher: JWKSFetcher | None = None,
        clock: Callable[[], float] = time.monotonic,
        forced_refresh_cooldown: int = 60,
    ) -> None:
        """
        url: the JWKS endpoint URL
        ttl_seconds: how long cached keys are considered fresh (default 1h)
        fetcher: async callable that fetches + parses JWKS (default: httpx)
        clock: monotonic time source (injectable for tests)
        forced_refresh_cooldown: min seconds between forced refreshes for
          the same unknown kid (default 60s)
        """
        self._url = url
        self._ttl = ttl_seconds
        self._fetcher = fetcher or _default_fetcher
        self._clock = clock
        self._forced_cooldown = forced_refresh_cooldown

        # Cache state — all mutations happen under `_lock`.
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None
        self._last_forced_refresh: dict[str, float] = {}
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        return self._url

    def _is_fresh(self) -> bool:
        """True if the cache has been fetched and TTL has not expired."""
        if self._fetched_at is None:
            return False
        return (self._clock() - self._fetched_at) < self._ttl

    async def _fetch_and_replace(self) -> None:
        """Fetch the JWKS, parse it, replace the cached keyset. Called
        only from inside `_lock`.

        Each JWK in the response `keys` array is indexed by its `kid`.
        JWKs without a `kid` (rare) are skipped — we can't index them."""
        jwks = await self._fetcher(self._url)
        keys = jwks.get("keys", [])
        new_index: dict[str, dict[str, Any]] = {}
        for jwk in keys:
            kid = jwk.get("kid")
            if kid:
                new_index[kid] = jwk
        self._keys_by_kid = new_index
        self._fetched_at = self._clock()

    async def get_key(self, kid: str) -> dict[str, Any]:
        """Return the JWK for the given kid.

        Refresh strategy:
          1. If cache is stale (TTL expired or never fetched): refresh
             once under lock. Any other concurrent callers block until
             the fetch completes and then see the fresh cache.
          2. If kid is in the refreshed cache: return it.
          3. If kid is NOT in the cache (key rotation case): one
             forced refresh per kid per `forced_refresh_cooldown`
             seconds. Second and subsequent callers within the cooldown
             see the stale cache without triggering a fetch.

        Raises KeyError if the kid is still unknown after a legitimate
        refresh attempt — callers should translate this into
        AuthCredentialInvalid at the verify() boundary."""
        # Fast path: fresh cache, kid known
        if self._is_fresh() and kid in self._keys_by_kid:
            return self._keys_by_kid[kid]

        async with self._lock:
            # Re-check under lock — another coroutine may have refreshed
            # while we were waiting (the serialized-concurrent-refresh
            # correctness property).
            if self._is_fresh() and kid in self._keys_by_kid:
                return self._keys_by_kid[kid]

            # Stale cache → plain TTL refresh
            if not self._is_fresh():
                await self._fetch_and_replace()
                if kid in self._keys_by_kid:
                    return self._keys_by_kid[kid]

            # Cache is fresh but kid unknown → forced refresh with
            # cooldown per-kid.
            last_forced = self._last_forced_refresh.get(kid)
            now = self._clock()
            if (
                last_forced is None
                or (now - last_forced) >= self._forced_cooldown
            ):
                self._last_forced_refresh[kid] = now
                await self._fetch_and_replace()

            if kid in self._keys_by_kid:
                return self._keys_by_kid[kid]

            raise KeyError(
                f"kid '{kid}' not found in JWKS at {self._url}; "
                f"key may have been rotated and cooldown has not elapsed."
            )
