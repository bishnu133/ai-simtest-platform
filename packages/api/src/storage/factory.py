"""Storage adapter factory.

Environment-driven selection of the storage backend. Services call
get_storage_adapter() and get whatever is configured for the current
environment.

Configuration:
  STORAGE_BACKEND=local   (default)
  STORAGE_BACKEND=r2

Local backend envs:
  LOCAL_STORAGE_ROOT        (default: /tmp/simtest-storage)
  LOCAL_STORAGE_BUCKET      (default: simtest-local)

R2 backend envs:
  R2_BUCKET                 (required)
  R2_ACCOUNT_ID             (required unless R2_ENDPOINT_URL is set)
  R2_ACCESS_KEY_ID          (required)
  R2_SECRET_ACCESS_KEY      (required)
  R2_ENDPOINT_URL           (optional, derived from account_id if not set)
"""
from __future__ import annotations

import logging
import os
import threading

from src.storage.base import StorageAdapter
from src.storage.local import LocalFilesystemAdapter
from src.storage.models import StorageConfigError

logger = logging.getLogger(__name__)

_adapter: StorageAdapter | None = None
_lock = threading.Lock()


def get_storage_adapter(force_reload: bool = False) -> StorageAdapter:
    """Return the configured storage adapter (singleton).

    Thread-safe. First call reads env and constructs the adapter;
    subsequent calls return the cached instance.
    """
    global _adapter
    if _adapter is not None and not force_reload:
        return _adapter

    with _lock:
        if _adapter is not None and not force_reload:
            return _adapter
        _adapter = _build_adapter()
        return _adapter


def reset_storage_adapter() -> None:
    """Clear the cached adapter. Test-only."""
    global _adapter
    with _lock:
        _adapter = None


def set_storage_adapter(adapter: StorageAdapter) -> None:
    """Inject a pre-built adapter. Test-only."""
    global _adapter
    with _lock:
        _adapter = adapter


def _build_adapter() -> StorageAdapter:
    backend = os.getenv("STORAGE_BACKEND", "local").lower()

    if backend == "local":
        root = os.getenv("LOCAL_STORAGE_ROOT", "/tmp/simtest-storage")
        bucket = os.getenv("LOCAL_STORAGE_BUCKET", "simtest-local")
        logger.info("Storage backend: local (root=%s, bucket=%s)", root, bucket)
        return LocalFilesystemAdapter(root=root, bucket=bucket)

    if backend == "r2":
        from src.storage.r2 import CloudflareR2Adapter

        bucket = os.getenv("R2_BUCKET")
        if not bucket:
            raise StorageConfigError("STORAGE_BACKEND=r2 requires R2_BUCKET")

        adapter = CloudflareR2Adapter(
            bucket=bucket,
            account_id=os.getenv("R2_ACCOUNT_ID"),
            access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
            secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            endpoint_url=os.getenv("R2_ENDPOINT_URL"),
            region=os.getenv("R2_REGION", "auto"),
        )
        logger.info("Storage backend: r2 (bucket=%s)", bucket)
        return adapter

    raise StorageConfigError(
        f"Unknown STORAGE_BACKEND={backend!r}. Must be 'local' or 'r2'."
    )
