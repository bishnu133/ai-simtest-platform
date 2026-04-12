"""Abstract base class for storage backends.

All storage adapters implement this interface. Services depend on the
abstract type, never the concrete backend. This gives us:

  - Local adapter for dev/test (no network, no credentials)
  - R2 adapter for prod (S3-compatible)
  - Future: Azure Blob, GCS — just add a new subclass

Tenant isolation rule: every key MUST start with the tenant_id prefix.
Adapters enforce this at the lowest level to prevent cross-tenant leaks.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import timedelta

from src.storage.models import (
    ObjectRef,
    SignedUrl,
    StoredObject,
    TenantIsolationError,
)


def compute_sha256(data: bytes) -> str:
    """Canonical hash function used throughout the storage layer."""
    return hashlib.sha256(data).hexdigest()


def _validate_tenant_scoped_key(key: str, tenant_id: str) -> None:
    """Guard: keys MUST begin with the tenant_id to enforce isolation at the storage layer.

    This is defense-in-depth — even if a service layer bug tries to fetch
    the wrong tenant's object, the adapter itself blocks it.
    """
    if not key:
        raise TenantIsolationError("Storage key cannot be empty")
    if not key.startswith(f"{tenant_id}/"):
        raise TenantIsolationError(
            f"Key {key!r} is not scoped to tenant {tenant_id!r}. "
            "All keys must begin with '{tenant_id}/'."
        )
    # Path traversal guard
    if ".." in key.split("/"):
        raise TenantIsolationError(f"Key {key!r} contains path traversal segment")


class StorageAdapter(ABC):
    """Abstract storage backend. All methods are tenant-scoped."""

    backend_name: str  # Set by subclasses
    bucket: str  # Set by subclasses

    @abstractmethod
    async def put(
        self,
        tenant_id: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectRef:
        """Store bytes at the given tenant-scoped key. Returns ObjectRef with hash."""

    @abstractmethod
    async def get(self, tenant_id: str, key: str) -> StoredObject:
        """Fetch object by key. Raises ObjectNotFound if missing."""

    @abstractmethod
    async def delete(self, tenant_id: str, key: str) -> bool:
        """Delete object. Returns True if deleted, False if key didn't exist."""

    @abstractmethod
    async def exists(self, tenant_id: str, key: str) -> bool:
        """Check whether a key exists without fetching contents."""

    @abstractmethod
    async def list_keys(self, tenant_id: str, prefix: str) -> list[str]:
        """List all keys with the given prefix. Prefix MUST be tenant-scoped."""

    @abstractmethod
    async def sign_url(
        self,
        tenant_id: str,
        key: str,
        method: str = "GET",
        expires_in: timedelta = timedelta(minutes=15),
        content_type: str | None = None,
    ) -> SignedUrl:
        """Generate a short-lived signed URL for direct client access."""

    def _check_tenant(self, key: str, tenant_id: str) -> None:
        """Shared tenant isolation guard. Subclasses call this before I/O."""
        _validate_tenant_scoped_key(key, tenant_id)
