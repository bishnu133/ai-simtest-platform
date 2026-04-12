"""Suite 2: LocalFilesystemAdapter — put/get/delete/exists/list/sign_url."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from src.storage.models import ObjectNotFound, TenantIsolationError


pytestmark = pytest.mark.asyncio


class TestLocalAdapter:
    async def test_put_and_get_round_trip(self, local_adapter):
        data = b"hello world"
        key = "tenant_a/workspace_main/assets/asset_1.json"

        ref = await local_adapter.put("tenant_a", key, data, content_type="application/json")

        assert ref.backend == "local"
        assert ref.key == key
        assert ref.size_bytes == len(data)
        assert ref.content_type == "application/json"
        assert len(ref.content_hash) == 64

        fetched = await local_adapter.get("tenant_a", key)
        assert fetched.data == data
        assert fetched.ref.content_hash == ref.content_hash

    async def test_put_rejects_cross_tenant_key(self, local_adapter):
        with pytest.raises(TenantIsolationError):
            await local_adapter.put(
                "tenant_a", "tenant_b/workspace_main/file.json", b"leaked"
            )

    async def test_get_missing_raises_object_not_found(self, local_adapter):
        with pytest.raises(ObjectNotFound):
            await local_adapter.get("tenant_a", "tenant_a/workspace_main/missing.json")

    async def test_delete_returns_true_when_exists(self, local_adapter):
        key = "tenant_a/workspace_main/assets/to_delete.json"
        await local_adapter.put("tenant_a", key, b"data")
        assert await local_adapter.delete("tenant_a", key) is True
        assert await local_adapter.exists("tenant_a", key) is False

    async def test_delete_returns_false_when_missing(self, local_adapter):
        assert (
            await local_adapter.delete(
                "tenant_a", "tenant_a/workspace_main/not_there.json"
            )
            is False
        )

    async def test_list_keys_returns_tenant_scoped_only(self, local_adapter):
        await local_adapter.put("tenant_a", "tenant_a/ws1/a.json", b"1")
        await local_adapter.put("tenant_a", "tenant_a/ws1/b.json", b"2")
        await local_adapter.put("tenant_a", "tenant_a/ws2/c.json", b"3")

        ws1_keys = await local_adapter.list_keys("tenant_a", "tenant_a/ws1")
        assert len(ws1_keys) == 2
        assert all("ws1" in k for k in ws1_keys)

    async def test_sign_url_generates_file_url_with_hmac(self, local_adapter):
        key = "tenant_a/workspace_main/asset.json"
        await local_adapter.put("tenant_a", key, b"content")

        signed = await local_adapter.sign_url(
            "tenant_a", key, method="GET", expires_in=timedelta(minutes=15)
        )
        assert signed.url.startswith("file://")
        assert "sig=" in signed.url
        assert "exp=" in signed.url
        assert signed.method == "GET"

    async def test_atomic_write_leaves_no_tmp_files_on_success(
        self, local_adapter, tmp_path
    ):
        await local_adapter.put(
            "tenant_a", "tenant_a/workspace_main/atomic.json", b"atomic"
        )
        storage_root = Path(local_adapter.root)
        tmp_files = list(storage_root.rglob("*.tmp"))
        assert tmp_files == []
