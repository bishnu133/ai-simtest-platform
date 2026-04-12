"""Storage layer data models and exceptions.

This module defines the contract between services and storage backends.
Services depend only on these types, never on backend-specific details.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import utcnow


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StorageError(Exception):
    """Base exception for all storage-layer errors."""


class ObjectNotFound(StorageError):
    """Raised when a requested object key does not exist."""

    def __init__(self, key: str, backend: str):
        self.key = key
        self.backend = backend
        super().__init__(f"Object not found: {key} (backend={backend})")


class StorageConfigError(StorageError):
    """Raised when storage is misconfigured (missing creds, bad endpoint, etc.)."""


class StorageIntegrityError(StorageError):
    """Raised when a fetched object's hash does not match its stored hash."""

    def __init__(self, key: str, expected: str, actual: str):
        self.key = key
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Integrity check failed for {key}: expected {expected[:12]}..., got {actual[:12]}..."
        )


class TenantIsolationError(StorageError):
    """Raised when an operation would cross tenant boundaries."""


# ---------------------------------------------------------------------------
# Storage tier — used by payload router to decide PG vs object store
# ---------------------------------------------------------------------------


class StorageTier(str, Enum):
    """Where a payload is stored."""

    INLINE = "inline"  # Stored in PG as JSONB
    OBJECT = "object"  # Stored in R2/Local, referenced by key


# ---------------------------------------------------------------------------
# ObjectRef — handle to a stored object
# ---------------------------------------------------------------------------


class ObjectRef(BaseModel):
    """Reference to an object stored in a storage backend.

    This is what gets persisted in the database alongside metadata —
    the ObjectRef tells us which backend, which key, and the hash for
    integrity verification.
    """

    model_config = ConfigDict(frozen=True)

    backend: Literal["local", "r2"]
    bucket: str  # For local, this is the root dir name
    key: str  # Tenant-scoped key: {tenant_id}/{workspace_id}/{resource}/{id}
    size_bytes: int = Field(ge=0)
    content_hash: str  # SHA-256 hex of the object contents
    content_type: str = "application/octet-stream"
    stored_at: datetime = Field(default_factory=utcnow)

    @property
    def qualified_key(self) -> str:
        """Full key including bucket, for logging."""
        return f"{self.backend}://{self.bucket}/{self.key}"


class StoredObject(BaseModel):
    """Object data plus its reference. Returned from get() operations."""

    ref: ObjectRef
    data: bytes


class SignedUrl(BaseModel):
    """Short-lived signed URL for direct client upload/download."""

    model_config = ConfigDict(frozen=True)

    url: str
    method: Literal["GET", "PUT"]
    expires_at: datetime
    headers: dict[str, str] = Field(default_factory=dict)
