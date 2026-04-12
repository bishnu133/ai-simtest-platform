"""Payload router — decides whether a payload goes to PG (inline) or R2 (object).

Routing rules (precedence order):
  1. Explicit override via force_tier argument
  2. Per-asset-type forced tiers (datasets always go to object store)
  3. Size-based: > threshold bytes → object store, else inline
  4. Default: inline

The router does NOT perform the actual storage — it returns a decision
that services act on. This keeps routing policy testable in isolation.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from src.common.models import AssetType
from src.storage.models import StorageTier


DEFAULT_THRESHOLD_BYTES = 64 * 1024  # 64 KB


# Asset types that ALWAYS go to object storage, regardless of size.
# Datasets can be 10MB+ JSONL; conversation transcripts can be huge.
_FORCE_OBJECT_TIER: set[str] = {
    AssetType.DATASET.value,
}

# Asset types that ALWAYS go inline (small config-like).
_FORCE_INLINE_TIER: set[str] = {
    AssetType.BOT_PROFILE.value,
    AssetType.SCORECARD.value,
}


@dataclass(frozen=True)
class RoutingDecision:
    """Result of the payload router."""

    tier: StorageTier
    size_bytes: int
    reason: str


class PayloadRouter:
    """Stateless policy engine for payload storage routing."""

    def __init__(self, threshold_bytes: int | None = None):
        if threshold_bytes is None:
            env = os.getenv("PAYLOAD_R2_THRESHOLD_BYTES")
            threshold_bytes = int(env) if env else DEFAULT_THRESHOLD_BYTES
        if threshold_bytes < 0:
            raise ValueError("threshold_bytes must be non-negative")
        self.threshold_bytes = threshold_bytes

    def decide(
        self,
        *,
        payload: dict | bytes | str,
        resource_kind: str | None = None,
        force_tier: StorageTier | None = None,
    ) -> RoutingDecision:
        """Decide where a payload should be stored.

        Args:
            payload: The content — dict (asset content), bytes, or str
            resource_kind: Asset type name or similar hint ("dataset", "conversation_transcript", ...)
            force_tier: Explicit override (e.g., admin forces object tier)
        """
        size_bytes = self._sizeof(payload)

        if force_tier is not None:
            return RoutingDecision(
                tier=force_tier,
                size_bytes=size_bytes,
                reason=f"force_tier={force_tier.value}",
            )

        if resource_kind:
            if resource_kind in _FORCE_OBJECT_TIER:
                return RoutingDecision(
                    tier=StorageTier.OBJECT,
                    size_bytes=size_bytes,
                    reason=f"resource_kind={resource_kind} always uses object tier",
                )
            if resource_kind in _FORCE_INLINE_TIER:
                return RoutingDecision(
                    tier=StorageTier.INLINE,
                    size_bytes=size_bytes,
                    reason=f"resource_kind={resource_kind} always uses inline tier",
                )

        if size_bytes > self.threshold_bytes:
            return RoutingDecision(
                tier=StorageTier.OBJECT,
                size_bytes=size_bytes,
                reason=f"size {size_bytes} > threshold {self.threshold_bytes}",
            )

        return RoutingDecision(
            tier=StorageTier.INLINE,
            size_bytes=size_bytes,
            reason=f"size {size_bytes} <= threshold {self.threshold_bytes}",
        )

    @staticmethod
    def _sizeof(payload: dict | bytes | str) -> int:
        """Compute the serialized byte size of a payload."""
        if isinstance(payload, bytes):
            return len(payload)
        if isinstance(payload, str):
            return len(payload.encode("utf-8"))
        if isinstance(payload, dict):
            return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        raise TypeError(f"Unsupported payload type: {type(payload).__name__}")
