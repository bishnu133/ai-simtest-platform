"""Asset Registry service.

Handles the full lifecycle of versioned, governable evaluation resources:
  create → update → version → approve → deprecate → clone

Hybrid storage contract:
  - Small content (< threshold): stored inline in PG (inline_content)
  - Large content (>= threshold or forced): uploaded to R2, referenced by payload_ref
  - content_hash is computed BEFORE storage decision — same hash regardless of tier

Concurrency: the store is protected by an asyncio.Lock for the MVP (matches S1
in-process pattern). Production swaps this for PG row locks via SELECT FOR UPDATE.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from typing import Any

from src.audit._compat import to_tenant_audit_event
from src.audit.logger import AuditActions, audit_logger
from src.common.models import (
    ActorRef,
    AssetRef,
    AssetStatus,
    AssetType,
    CursorPage,
    TenantContext,
    utcnow,
)
from src.storage.base import StorageAdapter, compute_sha256
from src.storage.models import ObjectRef, StorageTier
from src.storage.payload_router import PayloadRouter, RoutingDecision

from src.assets.models import (
    AssetApprovalRequest,
    AssetCreateRequest,
    AssetListFilter,
    AssetRecord,
    AssetUpdateRequest,
    AssetVersionRequest,
)


# ---------------------------------------------------------------------------
# Service exceptions — mapped to HTTP status codes at the router layer
# ---------------------------------------------------------------------------


class AssetServiceError(Exception):
    """Base class for all asset service errors."""


class AssetNotFound(AssetServiceError):
    """Raised when an asset ID or slug doesn't exist in the tenant's workspace."""


class AssetAlreadyExists(AssetServiceError):
    """Raised when creating an asset with a slug that's already in use."""


class AssetImmutableError(AssetServiceError):
    """Raised when attempting to modify an approved or deprecated asset in-place."""


class AssetStateError(AssetServiceError):
    """Raised when a state transition is invalid (e.g., approving a deprecated asset)."""


class AssetIntegrityError(AssetServiceError):
    """Raised when a large-payload asset's hash doesn't match storage."""


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def _compute_content_hash(content: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON. Stable across Python versions."""
    serialized = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cursor encoding
# ---------------------------------------------------------------------------


def _encode_cursor(created_at_iso: str, asset_id: str) -> str:
    raw = f"{created_at_iso}|{asset_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at_iso, asset_id = raw.split("|", 1)
        return created_at_iso, asset_id
    except Exception as exc:
        raise AssetServiceError(f"Invalid cursor: {cursor!r}") from exc


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AssetService:
    """Tenant-aware asset registry service.

    Stores assets in an in-memory keyed store (indexed by asset_id and (slug, version)).
    All state-changing operations write audit events. Large payloads are
    offloaded to the injected StorageAdapter.
    """

    # Storage key convention: {tenant_id}/{workspace_id}/assets/{asset_id}/v{version}.json
    _KEY_TEMPLATE = "{tenant_id}/{workspace_id}/assets/{asset_id}/v{version}.json"

    def __init__(
        self,
        storage: StorageAdapter,
        router: PayloadRouter | None = None,
    ):
        self._storage = storage
        self._router = router or PayloadRouter()
        # Primary store: (tenant_id, workspace_id, asset_id, version) -> AssetRecord
        self._records: dict[tuple[str, str, str, int], AssetRecord] = {}
        # Slug index: (tenant_id, workspace_id, slug) -> asset_id
        self._slug_index: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    # -------- helpers --------

    def _storage_key(self, ctx: TenantContext, asset_id: str, version: int) -> str:
        return self._KEY_TEMPLATE.format(
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            asset_id=asset_id,
            version=version,
        )

    def _slug_key(self, ctx: TenantContext, slug: str) -> tuple[str, str, str]:
        return (ctx.tenant_id, ctx.workspace_id, slug)

    def _record_key(self, ctx: TenantContext, asset_id: str, version: int) -> tuple:
        return (ctx.tenant_id, ctx.workspace_id, asset_id, version)

    def _latest_version(self, ctx: TenantContext, asset_id: str) -> int:
        versions = [
            v
            for (t, w, aid, v) in self._records.keys()
            if t == ctx.tenant_id and w == ctx.workspace_id and aid == asset_id
        ]
        if not versions:
            raise AssetNotFound(f"Asset {asset_id} not found")
        return max(versions)

    async def _persist_content(
        self,
        ctx: TenantContext,
        asset_id: str,
        version: int,
        content: dict[str, Any],
        asset_type: AssetType,
        force_tier: StorageTier | None = None,
    ) -> tuple[StorageTier, dict | None, ObjectRef | None, str]:
        """Route content to PG or R2 based on policy. Returns (tier, inline, payload_ref, hash)."""
        content_hash = _compute_content_hash(content)
        decision: RoutingDecision = self._router.decide(
            payload=content,
            resource_kind=asset_type.value,
            force_tier=force_tier,
        )

        if decision.tier == StorageTier.INLINE:
            return StorageTier.INLINE, content, None, content_hash

        # Object tier: upload to storage
        key = self._storage_key(ctx, asset_id, version)
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ref = await self._storage.put(
            tenant_id=ctx.tenant_id,
            key=key,
            data=serialized,
            content_type="application/json",
        )
        # Sanity: storage's hash MUST match our computed hash
        if ref.content_hash != content_hash:
            raise AssetIntegrityError(
                f"Hash mismatch after put: computed={content_hash[:12]}, storage={ref.content_hash[:12]}"
            )
        return StorageTier.OBJECT, None, ref, content_hash

    async def _load_content(self, ctx: TenantContext, record: AssetRecord) -> dict[str, Any]:
        """Return the content dict regardless of storage tier. Verifies hash on fetch."""
        if record.storage_tier == StorageTier.INLINE:
            return record.inline_content or {}

        assert record.payload_ref is not None, "Object-tier record missing payload_ref"
        obj = await self._storage.get(tenant_id=ctx.tenant_id, key=record.payload_ref.key)
        if obj.ref.content_hash != record.content_hash:
            raise AssetIntegrityError(
                f"Hash mismatch on load: record={record.content_hash[:12]}, "
                f"storage={obj.ref.content_hash[:12]}"
            )
        return json.loads(obj.data.decode("utf-8"))

    # -------- create --------

    async def create_asset(
        self, ctx: TenantContext, req: AssetCreateRequest
    ) -> AssetRecord:
        async with self._lock:
            slug_key = self._slug_key(ctx, req.slug)
            if slug_key in self._slug_index:
                raise AssetAlreadyExists(
                    f"Asset with slug {req.slug!r} already exists in this workspace"
                )

            asset_id = str(uuid.uuid4())
            version = 1

            tier, inline, payload_ref, content_hash = await self._persist_content(
                ctx, asset_id, version, req.content, req.asset_type
            )

            record = AssetRecord(
                id=asset_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                asset_type=req.asset_type,
                name=req.name,
                slug=req.slug,
                description=req.description,
                version=version,
                status=AssetStatus.DRAFT,
                content_hash=content_hash,
                storage_tier=tier,
                inline_content=inline,
                payload_ref=payload_ref,
                created_by=ctx.actor,
                changelog=req.changelog,
                tags=req.tags,
            )

            self._records[self._record_key(ctx, asset_id, version)] = record
            self._slug_index[slug_key] = asset_id

            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event(
                    ctx,
                    AuditActions.ASSET_CREATED,
                    resource_type="asset",
                    resource_id=asset_id,
                    metadata={
                        "asset_type": req.asset_type.value,
                        "slug": req.slug,
                        "version": version,
                        "storage_tier": tier.value,
                        "content_hash": content_hash,
                    },
                )
            )
            return record

    # -------- read --------

    async def get_asset(
        self, ctx: TenantContext, asset_id: str, version: int | None = None
    ) -> AssetRecord:
        if version is None:
            version = self._latest_version(ctx, asset_id)
        key = self._record_key(ctx, asset_id, version)
        record = self._records.get(key)
        if record is None:
            raise AssetNotFound(f"Asset {asset_id} version {version} not found")
        return record

    async def get_asset_by_slug(
        self, ctx: TenantContext, slug: str, version: int | None = None
    ) -> AssetRecord:
        asset_id = self._slug_index.get(self._slug_key(ctx, slug))
        if asset_id is None:
            raise AssetNotFound(f"Asset with slug {slug!r} not found")
        return await self.get_asset(ctx, asset_id, version)

    async def get_asset_content(
        self, ctx: TenantContext, asset_id: str, version: int | None = None
    ) -> dict[str, Any]:
        record = await self.get_asset(ctx, asset_id, version)
        return await self._load_content(ctx, record)

    async def list_versions(
        self, ctx: TenantContext, asset_id: str
    ) -> list[AssetRecord]:
        records = [
            r
            for (t, w, aid, _v), r in self._records.items()
            if t == ctx.tenant_id and w == ctx.workspace_id and aid == asset_id
        ]
        if not records:
            raise AssetNotFound(f"Asset {asset_id} not found")
        return sorted(records, key=lambda r: r.version)

    async def list_assets(
        self, ctx: TenantContext, filt: AssetListFilter
    ) -> CursorPage:
        # For list, return only the latest version of each asset_id matching filters
        latest_per_asset: dict[str, AssetRecord] = {}
        for (t, w, aid, _v), record in self._records.items():
            if t != ctx.tenant_id or w != ctx.workspace_id:
                continue
            if filt.asset_type and record.asset_type != filt.asset_type:
                continue
            if filt.status and record.status != filt.status:
                continue
            if filt.slug and record.slug != filt.slug:
                continue
            if filt.tag_key and filt.tag_key not in record.tags:
                continue
            if filt.tag_value and filt.tag_value not in record.tags.values():
                continue
            existing = latest_per_asset.get(aid)
            if existing is None or record.version > existing.version:
                latest_per_asset[aid] = record

        # Sort by created_at desc, then asset_id for stable cursor
        sorted_records = sorted(
            latest_per_asset.values(),
            key=lambda r: (r.created_at.isoformat(), r.id),
            reverse=True,
        )

        # Cursor filter (records older than cursor point)
        if filt.cursor:
            cursor_iso, cursor_id = _decode_cursor(filt.cursor)
            sorted_records = [
                r
                for r in sorted_records
                if (r.created_at.isoformat(), r.id) < (cursor_iso, cursor_id)
            ]

        page = sorted_records[: filt.limit]
        has_more = len(sorted_records) > filt.limit
        next_cursor: str | None = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(last.created_at.isoformat(), last.id)

        return CursorPage(items=page, next_cursor=next_cursor, has_more=has_more)

    # -------- update draft --------

    async def update_draft(
        self, ctx: TenantContext, asset_id: str, req: AssetUpdateRequest
    ) -> AssetRecord:
        async with self._lock:
            version = self._latest_version(ctx, asset_id)
            record = self._records[self._record_key(ctx, asset_id, version)]
            if record.status != AssetStatus.DRAFT:
                raise AssetImmutableError(
                    f"Cannot update asset {asset_id} in status {record.status.value}. "
                    "Create a new version instead."
                )

            updated = record.model_copy(update={})

            if req.name is not None:
                updated.name = req.name
            if req.description is not None:
                updated.description = req.description
            if req.tags is not None:
                updated.tags = req.tags
            if req.changelog is not None:
                updated.changelog = req.changelog

            if req.content is not None:
                tier, inline, payload_ref, content_hash = await self._persist_content(
                    ctx, asset_id, version, req.content, updated.asset_type
                )
                updated.storage_tier = tier
                updated.inline_content = inline
                updated.payload_ref = payload_ref
                updated.content_hash = content_hash

            updated.updated_at = utcnow()
            self._records[self._record_key(ctx, asset_id, version)] = updated

            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event(
                    ctx,
                    AuditActions.ASSET_UPDATED,
                    resource_type="asset",
                    resource_id=asset_id,
                    metadata={"version": version, "fields": _changed_fields(req)},
                )
            )
            return updated

    # -------- new version --------

    async def create_version(
        self, ctx: TenantContext, asset_id: str, req: AssetVersionRequest
    ) -> AssetRecord:
        async with self._lock:
            current_version = self._latest_version(ctx, asset_id)
            current = self._records[self._record_key(ctx, asset_id, current_version)]

            new_version = current_version + 1
            tier, inline, payload_ref, content_hash = await self._persist_content(
                ctx, asset_id, new_version, req.content, current.asset_type
            )

            new_record = AssetRecord(
                id=asset_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                asset_type=current.asset_type,
                name=current.name,
                slug=current.slug,
                description=current.description,
                version=new_version,
                status=AssetStatus.DRAFT,
                content_hash=content_hash,
                storage_tier=tier,
                inline_content=inline,
                payload_ref=payload_ref,
                created_by=ctx.actor,
                changelog=req.changelog,
                parent_version=current_version,
                tags=dict(current.tags),
            )
            self._records[self._record_key(ctx, asset_id, new_version)] = new_record

            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event(
                    ctx,
                    AuditActions.ASSET_VERSION_CREATED,
                    resource_type="asset",
                    resource_id=asset_id,
                    metadata={
                        "version": new_version,
                        "parent_version": current_version,
                        "changelog": req.changelog,
                        "content_hash": content_hash,
                        "storage_tier": tier.value,
                    },
                )
            )
            return new_record

    # -------- approve / deprecate --------

    async def approve(
        self, ctx: TenantContext, asset_id: str, req: AssetApprovalRequest
    ) -> AssetRecord:
        async with self._lock:
            version = self._latest_version(ctx, asset_id)
            record = self._records[self._record_key(ctx, asset_id, version)]

            if record.status == AssetStatus.APPROVED:
                raise AssetStateError(f"Asset {asset_id} v{version} is already approved")
            if record.status == AssetStatus.DEPRECATED:
                raise AssetStateError(
                    f"Cannot approve deprecated asset {asset_id} v{version}. "
                    "Create a new version first."
                )

            record.status = AssetStatus.APPROVED
            record.approved_by = ctx.actor
            record.approved_at = utcnow()
            record.updated_at = utcnow()
            if req.changelog_note:
                record.changelog = f"{record.changelog}\n\nApproval note: {req.changelog_note}"

            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event(
                    ctx,
                    AuditActions.ASSET_APPROVED,
                    resource_type="asset",
                    resource_id=asset_id,
                    metadata={"version": version, "approver": ctx.actor.actor_id},
                )
            )
            return record

    async def deprecate(self, ctx: TenantContext, asset_id: str) -> AssetRecord:
        async with self._lock:
            version = self._latest_version(ctx, asset_id)
            record = self._records[self._record_key(ctx, asset_id, version)]

            if record.status == AssetStatus.DEPRECATED:
                raise AssetStateError(f"Asset {asset_id} v{version} is already deprecated")

            record.status = AssetStatus.DEPRECATED
            record.updated_at = utcnow()

            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event(
                    ctx,
                    AuditActions.ASSET_DEPRECATED,
                    resource_type="asset",
                    resource_id=asset_id,
                    metadata={"version": version},
                )
            )
            return record

    # -------- clone --------

    async def clone(
        self,
        ctx: TenantContext,
        asset_id: str,
        new_slug: str,
        new_name: str | None = None,
    ) -> AssetRecord:
        async with self._lock:
            source_version = self._latest_version(ctx, asset_id)
            source = self._records[self._record_key(ctx, asset_id, source_version)]

            slug_key = self._slug_key(ctx, new_slug)
            if slug_key in self._slug_index:
                raise AssetAlreadyExists(
                    f"Cannot clone: slug {new_slug!r} already in use"
                )

            source_content = await self._load_content(ctx, source)
            new_asset_id = str(uuid.uuid4())

            tier, inline, payload_ref, content_hash = await self._persist_content(
                ctx, new_asset_id, 1, source_content, source.asset_type
            )

            cloned = AssetRecord(
                id=new_asset_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                asset_type=source.asset_type,
                name=new_name or f"{source.name} (clone)",
                slug=new_slug,
                description=source.description,
                version=1,
                status=AssetStatus.DRAFT,
                content_hash=content_hash,
                storage_tier=tier,
                inline_content=inline,
                payload_ref=payload_ref,
                created_by=ctx.actor,
                changelog=f"Cloned from {source.slug} v{source.version}",
                cloned_from=asset_id,
                tags=dict(source.tags),
            )
            self._records[self._record_key(ctx, new_asset_id, 1)] = cloned
            self._slug_index[slug_key] = new_asset_id

            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event(
                    ctx,
                    AuditActions.ASSET_CLONED,
                    resource_type="asset",
                    resource_id=new_asset_id,
                    metadata={
                        "source_asset_id": asset_id,
                        "source_version": source_version,
                        "new_slug": new_slug,
                    },
                )
            )
            return cloned

    # -------- AssetRef resolution --------

    async def resolve_ref(
        self, ctx: TenantContext, asset_id: str, version: int | None = None
    ) -> AssetRef:
        """Resolve an asset to an immutable AssetRef for RunContract injection."""
        record = await self.get_asset(ctx, asset_id, version)
        return AssetRef(
            asset_id=record.id,
            asset_type=record.asset_type,
            version=record.version,
            content_hash=record.content_hash,
        )

    # -------- test helpers --------

    def _reset(self) -> None:
        """Wipe the store. Test-only."""
        self._records.clear()
        self._slug_index.clear()


def _changed_fields(req: AssetUpdateRequest) -> list[str]:
    """Return the list of fields actually set on an update request."""
    fields: list[str] = []
    if req.name is not None:
        fields.append("name")
    if req.description is not None:
        fields.append("description")
    if req.content is not None:
        fields.append("content")
    if req.tags is not None:
        fields.append("tags")
    if req.changelog is not None:
        fields.append("changelog")
    return fields
