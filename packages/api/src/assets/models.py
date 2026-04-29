"""Asset domain models.

These are Pydantic models used by the service layer and the API. They
mirror what lives in the `assets` table but add methods and computed
properties.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src.common.models import ActorRef, AssetStatus, AssetType, utcnow
from src.secrets.denylist import check_no_secrets
from src.secrets.safe_jsonb import SafeJSONB
from src.storage.models import ObjectRef, StorageTier


def _validate_safe_jsonb_str_values(v: Any) -> Any:
    """SafeJSONB variant for dict[str, str] fields (e.g. tags).

    Same recursive key scan as ``SafeJSONB`` but preserves the narrower
    str-valued type annotation. Per Turn 2 plan v0.6 §5, the scanner only
    inspects keys, so the value type is irrelevant to enforcement.
    """
    if v is None:
        return v
    check_no_secrets(v)
    return v


# A parallel Annotated type for dict[str, str] fields. Keeps the static type
# narrower than SafeJSONB while running the same key-scan validator.
_SafeJSONBStr = Annotated[dict[str, str], AfterValidator(_validate_safe_jsonb_str_values)]


class AssetRecord(BaseModel):
    """Full asset record, including large-payload reference if any."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Identity
    id: str
    tenant_id: str
    workspace_id: str

    # Type & naming
    asset_type: AssetType
    name: str
    slug: str
    description: str = ""

    # Versioning
    version: int = 1
    status: AssetStatus = AssetStatus.DRAFT
    content_hash: str
    storage_tier: StorageTier = StorageTier.INLINE

    # Content: exactly one of these is set based on storage_tier
    inline_content: dict[str, Any] | None = None
    payload_ref: ObjectRef | None = None

    # Governance
    created_by: ActorRef
    approved_by: ActorRef | None = None
    approved_at: datetime | None = None
    changelog: str = ""

    # Lineage
    cloned_from: str | None = None
    parent_version: int | None = None

    # Metadata
    tags: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def is_large_payload(self) -> bool:
        return self.storage_tier == StorageTier.OBJECT

    @property
    def qualified_id(self) -> str:
        return f"{self.asset_type.value}:{self.slug}@v{self.version}"


class AssetCreateRequest(BaseModel):
    """Request body for creating a new asset (v1 draft)."""

    asset_type: AssetType
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str = ""
    content: SafeJSONB = Field(default_factory=dict)
    tags: _SafeJSONBStr = Field(default_factory=dict)
    changelog: str = "Initial version"


class AssetUpdateRequest(BaseModel):
    """Request body for updating a draft asset (not approved)."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    content: SafeJSONB | None = None
    tags: _SafeJSONBStr | None = None
    changelog: str | None = None


class AssetVersionRequest(BaseModel):
    """Request body for creating a new version of an existing asset."""

    content: SafeJSONB
    changelog: str = Field(min_length=1, max_length=2000)


class AssetApprovalRequest(BaseModel):
    """Request body for approving a draft asset."""

    changelog_note: str = ""


class AssetListFilter(BaseModel):
    """Filter parameters for list_assets."""

    asset_type: AssetType | None = None
    status: AssetStatus | None = None
    slug: str | None = None
    tag_key: str | None = None
    tag_value: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
