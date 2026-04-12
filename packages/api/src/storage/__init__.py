"""Hybrid storage layer.

Services import from this package; backend selection is handled by the
factory. The StorageAdapter ABC is the only contract services depend on.
"""
from src.storage.base import StorageAdapter, compute_sha256
from src.storage.factory import (
    get_storage_adapter,
    reset_storage_adapter,
    set_storage_adapter,
)
from src.storage.local import LocalFilesystemAdapter
from src.storage.models import (
    ObjectNotFound,
    ObjectRef,
    SignedUrl,
    StorageConfigError,
    StorageError,
    StorageIntegrityError,
    StorageTier,
    StoredObject,
    TenantIsolationError,
)
from src.storage.payload_router import (
    DEFAULT_THRESHOLD_BYTES,
    PayloadRouter,
    RoutingDecision,
)

__all__ = [
    # Base
    "StorageAdapter",
    "compute_sha256",
    # Adapters
    "LocalFilesystemAdapter",
    # Factory
    "get_storage_adapter",
    "reset_storage_adapter",
    "set_storage_adapter",
    # Models
    "ObjectNotFound",
    "ObjectRef",
    "SignedUrl",
    "StorageConfigError",
    "StorageError",
    "StorageIntegrityError",
    "StorageTier",
    "StoredObject",
    "TenantIsolationError",
    # Routing
    "DEFAULT_THRESHOLD_BYTES",
    "PayloadRouter",
    "RoutingDecision",
]
