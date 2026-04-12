"""Suite 4: PayloadRouter — size/type/force routing rules."""
from __future__ import annotations

import pytest

from src.common.models import AssetType
from src.storage.models import StorageTier
from src.storage.payload_router import PayloadRouter


class TestPayloadRouter:
    def test_small_payload_goes_inline(self):
        router = PayloadRouter(threshold_bytes=1024)
        decision = router.decide(payload={"k": "v"}, resource_kind=AssetType.JUDGE_PACK.value)
        assert decision.tier == StorageTier.INLINE
        assert decision.size_bytes < 1024
        assert "size" in decision.reason

    def test_large_payload_goes_to_object(self):
        router = PayloadRouter(threshold_bytes=64)
        big_payload = {"data": "x" * 200}
        decision = router.decide(payload=big_payload, resource_kind=AssetType.JUDGE_PACK.value)
        assert decision.tier == StorageTier.OBJECT
        assert decision.size_bytes > 64
        assert "threshold" in decision.reason

    def test_dataset_always_goes_to_object_regardless_of_size(self):
        router = PayloadRouter(threshold_bytes=1_000_000)  # very high
        tiny_dataset = {"rows": [1, 2, 3]}
        decision = router.decide(
            payload=tiny_dataset, resource_kind=AssetType.DATASET.value
        )
        assert decision.tier == StorageTier.OBJECT
        assert "dataset" in decision.reason

    def test_bot_profile_forced_inline_even_above_threshold(self):
        router = PayloadRouter(threshold_bytes=10)
        big_bot_profile = {"config": "x" * 500}
        decision = router.decide(
            payload=big_bot_profile, resource_kind=AssetType.BOT_PROFILE.value
        )
        assert decision.tier == StorageTier.INLINE
        assert "bot_profile" in decision.reason

    def test_force_tier_overrides_everything(self):
        router = PayloadRouter(threshold_bytes=10)
        decision = router.decide(
            payload={"tiny": True},
            resource_kind=AssetType.BOT_PROFILE.value,
            force_tier=StorageTier.OBJECT,
        )
        assert decision.tier == StorageTier.OBJECT
        assert "force_tier" in decision.reason
