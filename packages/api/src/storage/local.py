"""Local filesystem storage adapter.

Used in development and tests. Stores files under a root directory with
tenant-scoped subpaths. Signed URLs are generated as file:// URLs with
an HMAC token that encodes the expiration — this lets tests exercise the
signed-URL code path without needing a real HTTP server.

Thread/async safety: file I/O runs via asyncio.to_thread so it doesn't
block the event loop.
"""
from __future__ import annotations

import asyncio
import hmac
import hashlib
import os
import shutil
from datetime import timedelta
from pathlib import Path

from src.common.models import utcnow
from src.storage.base import StorageAdapter, compute_sha256
from src.storage.models import (
    ObjectNotFound,
    ObjectRef,
    SignedUrl,
    StorageConfigError,
    StoredObject,
)


class LocalFilesystemAdapter(StorageAdapter):
    """Filesystem-backed storage for dev and tests."""

    backend_name = "local"

    def __init__(self, root: str | Path, bucket: str = "simtest-local", signing_secret: str = "dev-secret-change-me"):
        self.root = Path(root).resolve()
        self.bucket = bucket
        self._signing_secret = signing_secret
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageConfigError(f"Cannot create storage root {self.root}: {exc}") from exc

    # ---- path helpers ----

    def _full_path(self, key: str) -> Path:
        """Map a storage key to an absolute filesystem path, guarding traversal."""
        # Path traversal is already blocked by _check_tenant, but belt-and-braces
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root)):
            raise StorageConfigError(f"Resolved path {candidate} escapes storage root {self.root}")
        return candidate

    # ---- core operations ----

    async def put(
        self,
        tenant_id: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectRef:
        self._check_tenant(key, tenant_id)
        content_hash = compute_sha256(data)
        path = self._full_path(key)

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write atomically via tmp + rename
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            # Sidecar file stores content_type (filesystem doesn't track it)
            meta_path = path.with_suffix(path.suffix + ".meta")
            meta_path.write_text(content_type)

        await asyncio.to_thread(_write)

        return ObjectRef(
            backend="local",
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            content_hash=content_hash,
            content_type=content_type,
        )

    async def get(self, tenant_id: str, key: str) -> StoredObject:
        self._check_tenant(key, tenant_id)
        path = self._full_path(key)

        def _read() -> tuple[bytes, str]:
            if not path.exists():
                raise ObjectNotFound(key, "local")
            data = path.read_bytes()
            meta_path = path.with_suffix(path.suffix + ".meta")
            content_type = (
                meta_path.read_text() if meta_path.exists() else "application/octet-stream"
            )
            return data, content_type

        data, content_type = await asyncio.to_thread(_read)
        ref = ObjectRef(
            backend="local",
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            content_hash=compute_sha256(data),
            content_type=content_type,
        )
        return StoredObject(ref=ref, data=data)

    async def delete(self, tenant_id: str, key: str) -> bool:
        self._check_tenant(key, tenant_id)
        path = self._full_path(key)

        def _delete() -> bool:
            if not path.exists():
                return False
            path.unlink()
            meta_path = path.with_suffix(path.suffix + ".meta")
            if meta_path.exists():
                meta_path.unlink()
            return True

        return await asyncio.to_thread(_delete)

    async def exists(self, tenant_id: str, key: str) -> bool:
        self._check_tenant(key, tenant_id)
        path = self._full_path(key)
        return await asyncio.to_thread(path.exists)

    async def list_keys(self, tenant_id: str, prefix: str) -> list[str]:
        self._check_tenant(prefix, tenant_id)
        base = self._full_path(prefix)

        def _list() -> list[str]:
            if not base.exists():
                return []
            results: list[str] = []
            if base.is_file():
                # Prefix points at a single file
                rel = base.relative_to(self.root)
                return [str(rel)]
            for p in base.rglob("*"):
                if p.is_file() and not p.name.endswith(".meta"):
                    rel = p.relative_to(self.root)
                    results.append(str(rel))
            return sorted(results)

        return await asyncio.to_thread(_list)

    async def sign_url(
        self,
        tenant_id: str,
        key: str,
        method: str = "GET",
        expires_in: timedelta = timedelta(minutes=15),
        content_type: str | None = None,
    ) -> SignedUrl:
        """Generate a pseudo-signed URL for local filesystem.

        The URL is a file:// reference with an HMAC token that encodes the
        expiration timestamp. Clients can verify it with the same secret.
        This is NOT secure for production — it exists so that local/test
        environments exercise the same code path as R2.
        """
        self._check_tenant(key, tenant_id)
        if method not in ("GET", "PUT"):
            raise ValueError(f"Unsupported method for signed URL: {method}")

        expires_at = utcnow() + expires_in
        expires_epoch = int(expires_at.timestamp())
        payload = f"{method}:{key}:{expires_epoch}".encode()
        token = hmac.new(
            self._signing_secret.encode(), payload, hashlib.sha256
        ).hexdigest()[:32]

        url = f"file://{self._full_path(key)}?exp={expires_epoch}&sig={token}"
        headers: dict[str, str] = {}
        if method == "PUT" and content_type:
            headers["Content-Type"] = content_type

        return SignedUrl(
            url=url, method=method, expires_at=expires_at, headers=headers
        )

    # ---- test-only helpers ----

    def _reset(self) -> None:
        """Wipe the storage root. Test-only."""
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
