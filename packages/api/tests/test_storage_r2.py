"""Suite 3: CloudflareR2Adapter — tested against moto (mocked S3)."""
from __future__ import annotations

from datetime import timedelta

import boto3
import pytest
from moto import mock_aws

from src.storage.models import ObjectNotFound, TenantIsolationError
from src.storage.r2 import CloudflareR2Adapter


pytestmark = pytest.mark.asyncio


@pytest.fixture
def r2_adapter():
    """R2 adapter wired to a moto-mocked S3 backend."""
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret",
        )
        client.create_bucket(Bucket="test-r2-bucket")
        adapter = CloudflareR2Adapter(
            bucket="test-r2-bucket",
            client=client,
        )
        yield adapter


class TestR2Adapter:
    async def test_put_and_get_round_trip(self, r2_adapter):
        key = "tenant_a/workspace_main/assets/big.jsonl"
        data = b'{"row": 1}\n{"row": 2}\n'

        ref = await r2_adapter.put("tenant_a", key, data, content_type="application/jsonl")

        assert ref.backend == "r2"
        assert ref.bucket == "test-r2-bucket"
        assert ref.size_bytes == len(data)
        assert len(ref.content_hash) == 64

        fetched = await r2_adapter.get("tenant_a", key)
        assert fetched.data == data
        assert fetched.ref.content_hash == ref.content_hash

    async def test_get_missing_key_raises_object_not_found(self, r2_adapter):
        with pytest.raises(ObjectNotFound):
            await r2_adapter.get(
                "tenant_a", "tenant_a/workspace_main/does_not_exist.json"
            )

    async def test_exists_returns_false_for_missing(self, r2_adapter):
        assert (
            await r2_adapter.exists("tenant_a", "tenant_a/workspace_main/ghost.json")
            is False
        )

    async def test_delete_returns_false_when_missing(self, r2_adapter):
        assert (
            await r2_adapter.delete("tenant_a", "tenant_a/workspace_main/ghost.json")
            is False
        )

    async def test_tenant_isolation_enforced_on_put(self, r2_adapter):
        with pytest.raises(TenantIsolationError):
            await r2_adapter.put(
                "tenant_a", "tenant_b/workspace_main/leak.json", b"secrets"
            )

    async def test_list_keys_returns_prefix_matches(self, r2_adapter):
        await r2_adapter.put("tenant_a", "tenant_a/ws/a.json", b"1")
        await r2_adapter.put("tenant_a", "tenant_a/ws/b.json", b"2")
        await r2_adapter.put("tenant_a", "tenant_a/other/c.json", b"3")

        ws_keys = await r2_adapter.list_keys("tenant_a", "tenant_a/ws")
        assert len(ws_keys) == 2

    async def test_sign_url_generates_https_url(self, r2_adapter):
        key = "tenant_a/workspace_main/signed.json"
        await r2_adapter.put("tenant_a", key, b"data")
        signed = await r2_adapter.sign_url(
            "tenant_a", key, method="GET", expires_in=timedelta(minutes=15)
        )
        assert signed.url.startswith("https://")
        assert signed.method == "GET"
