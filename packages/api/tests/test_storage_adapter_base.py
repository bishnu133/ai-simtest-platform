"""Suite 1: StorageAdapter abstract contract and tenant-isolation guard."""
from __future__ import annotations

import pytest

from src.storage.base import _validate_tenant_scoped_key, compute_sha256
from src.storage.models import (
    ObjectNotFound,
    StorageError,
    StorageIntegrityError,
    TenantIsolationError,
)


class TestStorageAdapterContract:
    def test_compute_sha256_deterministic(self):
        """Same input → same hash, every time."""
        data = b"the quick brown fox"
        h1 = compute_sha256(data)
        h2 = compute_sha256(data)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex length

    def test_compute_sha256_differs_for_different_input(self):
        assert compute_sha256(b"foo") != compute_sha256(b"bar")

    def test_tenant_key_validator_rejects_empty_key(self):
        with pytest.raises(TenantIsolationError, match="cannot be empty"):
            _validate_tenant_scoped_key("", "tenant_a")

    def test_tenant_key_validator_rejects_unprefixed_key(self):
        with pytest.raises(TenantIsolationError, match="not scoped to tenant"):
            _validate_tenant_scoped_key("some/other/path", "tenant_a")

    def test_tenant_key_validator_rejects_path_traversal(self):
        with pytest.raises(TenantIsolationError, match="path traversal"):
            _validate_tenant_scoped_key("tenant_a/../tenant_b/file.json", "tenant_a")

    def test_tenant_key_validator_accepts_valid_key(self):
        # Should not raise
        _validate_tenant_scoped_key("tenant_a/workspace_main/assets/asset_1.json", "tenant_a")

    def test_exception_hierarchy(self):
        """All storage exceptions derive from StorageError."""
        assert issubclass(ObjectNotFound, StorageError)
        assert issubclass(StorageIntegrityError, StorageError)
        assert issubclass(TenantIsolationError, StorageError)
