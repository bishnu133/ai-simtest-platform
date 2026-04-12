"""HTTP-layer response schemas for the assets API.

These are intentionally separate from `src.assets.models` (domain models).
Decoupling lets us evolve the HTTP contract without touching internal
service logic, and prevents domain model fields from leaking into the
public API surface accidentally.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.common.models import AssetStatus, AssetType
from src.storage.models import StorageTier


class AssetResponse(BaseModel):
    """Public representation of an asset record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    workspace_id: str
    asset_type: AssetType
    name: str
    slug: str
    description: str
    version: int
    status: AssetStatus
    content_hash: str
    storage_tier: StorageTier
    inline_content: dict[str, Any] | None = None
    payload_ref_key: str | None = None  # Key only, not the full ObjectRef
    changelog: str
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    parent_version: int | None = None
    cloned_from: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: Any) -> "AssetResponse":
        return cls(
            id=record.id,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            asset_type=record.asset_type,
            name=record.name,
            slug=record.slug,
            description=record.description,
            version=record.version,
            status=record.status,
            content_hash=record.content_hash,
            storage_tier=record.storage_tier,
            inline_content=record.inline_content,
            payload_ref_key=record.payload_ref.key if record.payload_ref else None,
            changelog=record.changelog,
            created_at=record.created_at,
            updated_at=record.updated_at,
            approved_at=record.approved_at,
            parent_version=record.parent_version,
            cloned_from=record.cloned_from,
            tags=record.tags,
        )


class AssetListResponse(BaseModel):
    """Paginated list of assets."""

    items: list[AssetResponse]
    next_cursor: str | None = None
    has_more: bool = False


class AssetVersionListResponse(BaseModel):
    """Version history for a single asset."""

    asset_id: str
    versions: list[AssetResponse]


class AssetCloneRequest(BaseModel):
    """Clone an existing asset into a new asset."""

    new_slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    new_name: str | None = None


class SignedUrlResponse(BaseModel):
    """Signed URL for direct upload or download."""

    url: str
    method: str
    expires_at: datetime
    headers: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Typed error envelope — used for 4xx and 5xx responses."""

    error_code: str
    message: str
    correlation_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
