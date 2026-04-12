"""Suite 6: Large-payload asset flow — dataset routed to object storage."""
from __future__ import annotations

import json

import pytest

from src.assets.models import AssetCreateRequest
from src.assets.service import AssetService
from src.common.models import AssetType
from src.storage.models import StorageTier
from src.storage.payload_router import PayloadRouter


pytestmark = pytest.mark.asyncio


@pytest.fixture
def svc(local_adapter):
    return AssetService(
        storage=local_adapter,
        router=PayloadRouter(threshold_bytes=64),  # low threshold to force object tier
    )


class TestAssetLargePayload:
    async def test_dataset_asset_routes_to_object_storage(
        self, svc, ctx_tenant_a, local_adapter
    ):
        """Dataset type always goes to object storage regardless of size."""
        req = AssetCreateRequest(
            asset_type=AssetType.DATASET,
            name="Golden Regression Set",
            slug="golden-regression",
            content={"rows": [{"q": "hi", "a": "hello"}]},
        )
        record = await svc.create_asset(ctx_tenant_a, req)

        assert record.storage_tier == StorageTier.OBJECT
        assert record.payload_ref is not None
        assert record.inline_content is None
        assert record.payload_ref.backend == "local"
        # Key must be tenant-scoped
        assert record.payload_ref.key.startswith(f"{ctx_tenant_a.tenant_id}/")

    async def test_large_judge_pack_routes_to_object_tier(self, svc, ctx_tenant_a):
        """A judge pack that exceeds threshold goes to object tier."""
        big_content = {"rules": ["rule_" + "x" * 100 for _ in range(20)]}
        req = AssetCreateRequest(
            asset_type=AssetType.JUDGE_PACK,
            name="Big Judge Pack",
            slug="big-judge-pack",
            content=big_content,
        )
        record = await svc.create_asset(ctx_tenant_a, req)
        assert record.storage_tier == StorageTier.OBJECT
        assert record.payload_ref is not None

    async def test_load_content_verifies_hash(self, svc, ctx_tenant_a):
        """get_asset_content fetches from R2 and verifies hash integrity."""
        req = AssetCreateRequest(
            asset_type=AssetType.DATASET,
            name="Tiny Dataset",
            slug="tiny-dataset",
            content={"data": "roundtrip"},
        )
        created = await svc.create_asset(ctx_tenant_a, req)
        content = await svc.get_asset_content(ctx_tenant_a, created.id)
        assert content == {"data": "roundtrip"}

    async def test_cross_tenant_asset_fetch_is_blocked(
        self, svc, ctx_tenant_a, ctx_tenant_b
    ):
        """Asset created in tenant A cannot be fetched from tenant B."""
        from src.assets.service import AssetNotFound

        req = AssetCreateRequest(
            asset_type=AssetType.DATASET,
            name="Tenant A Data",
            slug="tenant-a-data",
            content={"secret": "a"},
        )
        created = await svc.create_asset(ctx_tenant_a, req)

        with pytest.raises(AssetNotFound):
            await svc.get_asset(ctx_tenant_b, created.id)
