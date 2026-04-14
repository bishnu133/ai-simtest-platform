"""Asset domain ↔ ORM mapper.

Translates between `src.assets.models.AssetRecord` (the shipped Week 6a
domain shape) and `src.db.models.Asset` (the Turn 1a/1b ORM shape).

Storage tier handling: AssetRecord carries either `inline_content` (dict)
or `payload_ref` (ObjectRef). The ORM carries `storage_tier` + either
`inline_content` or `(payload_bucket, payload_key, payload_size_bytes)`.
The mapper translates between the two shapes without forcing the caller
to know which tier is in use.

Actor handling: AssetRecord's `created_by` and `approved_by` are
`ActorRef` Pydantic models. The ORM flattens these into three columns
each (actor_id, actor_type, display) to enable direct SQL filtering on
who created or approved an asset without a JSONB traversal.
"""
from __future__ import annotations

from src.assets.models import AssetRecord
from src.common.models import ActorRef, AssetStatus, AssetType
from src.db.models import Asset
from src.storage.models import ObjectRef, StorageTier


def asset_to_domain(row: Asset) -> AssetRecord:
    # Reconstruct the ActorRef for created_by (always present).
    created_by = ActorRef(
        actor_id=row.created_by_actor_id,
        actor_type=row.created_by_actor_type,  # type: ignore[arg-type]
        display_name=row.created_by_display,
    )

    # approved_by is optional — only set if approved_by_actor_id is present.
    approved_by: ActorRef | None = None
    if row.approved_by_actor_id is not None:
        approved_by = ActorRef(
            actor_id=row.approved_by_actor_id,
            actor_type=row.approved_by_actor_type or "human",  # type: ignore[arg-type]
            display_name=row.approved_by_display,
        )

    # Reconstruct the ObjectRef if the asset uses object storage.
    payload_ref: ObjectRef | None = None
    if row.storage_tier == "object" and row.payload_bucket and row.payload_key:
        payload_ref = ObjectRef(
            backend=row.payload_backend or "local",  # type: ignore[arg-type]
            bucket=row.payload_bucket,
            key=row.payload_key,
            size_bytes=row.payload_size_bytes or 0,
            content_hash=row.content_hash,
        )

    return AssetRecord(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        workspace_id=str(row.workspace_id),
        asset_type=AssetType(row.asset_type),
        name=row.name,
        slug=row.slug,
        description=row.description,
        version=row.version,
        status=AssetStatus(row.status),
        content_hash=row.content_hash,
        storage_tier=StorageTier(row.storage_tier),
        inline_content=row.inline_content,
        payload_ref=payload_ref,
        created_by=created_by,
        approved_by=approved_by,
        approved_at=row.approved_at,
        changelog=row.changelog,
        cloned_from=str(row.cloned_from) if row.cloned_from is not None else None,
        parent_version=row.parent_version,
        tags=dict(row.tags or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def asset_to_orm(record: AssetRecord) -> Asset:
    # Flatten ObjectRef if present.
    payload_backend = record.payload_ref.backend if record.payload_ref else None
    payload_bucket = record.payload_ref.bucket if record.payload_ref else None
    payload_key = record.payload_ref.key if record.payload_ref else None
    payload_size_bytes = (
        record.payload_ref.size_bytes if record.payload_ref else None
    )

    return Asset(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        asset_type=record.asset_type.value,
        name=record.name,
        slug=record.slug,
        description=record.description,
        version=record.version,
        status=record.status.value,
        content_hash=record.content_hash,
        storage_tier=record.storage_tier.value,
        inline_content=record.inline_content,
        payload_backend=payload_backend,
        payload_bucket=payload_bucket,
        payload_key=payload_key,
        payload_size_bytes=payload_size_bytes,
        # Flatten created_by ActorRef
        created_by_actor_id=record.created_by.actor_id,
        created_by_actor_type=record.created_by.actor_type,
        created_by_display=record.created_by.display_name,
        # Flatten approved_by ActorRef (nullable)
        approved_by_actor_id=(
            record.approved_by.actor_id if record.approved_by else None
        ),
        approved_by_actor_type=(
            record.approved_by.actor_type if record.approved_by else None
        ),
        approved_by_display=(
            record.approved_by.display_name if record.approved_by else None
        ),
        approved_at=record.approved_at,
        changelog=record.changelog,
        cloned_from=record.cloned_from,
        parent_version=record.parent_version,
        tags=dict(record.tags),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
