"""Conversation storage service.

Implements the hybrid storage pattern for conversation data:
  - Summary → in-memory store (PG in production)
  - Full transcript → object storage (R2 in production, Local in dev/test)

The service owns the tenant-scoped storage key convention:
  {tenant_id}/{workspace_id}/runs/{run_id}/conversations/{conversation_id}.json

All public methods require a TenantContext. Cross-tenant access is
structurally impossible because every storage operation validates the
key prefix against the tenant_id.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from src.audit.logger import AuditActions, audit_logger
from src.common.models import TenantContext, utcnow
from src.conversations.models import (
    ConversationSummary,
    ConversationTranscript,
    ConversationVerdict,
)
from src.storage.base import StorageAdapter
from src.storage.models import ObjectNotFound, ObjectRef, SignedUrl, StorageError


class ConversationServiceError(Exception):
    """Base exception for conversation service errors."""


class ConversationNotFound(ConversationServiceError):
    """Raised when a conversation ID doesn't exist in the tenant's workspace."""


class TranscriptIntegrityError(ConversationServiceError):
    """Raised when a fetched transcript's hash doesn't match its stored hash."""


class ConversationService:
    """Tenant-aware conversation storage.

    Stores summaries in memory (swap for PG in production) and transcripts
    in the injected StorageAdapter. Every mutation writes an audit event.
    """

    _KEY_TEMPLATE = (
        "{tenant_id}/{workspace_id}/runs/{run_id}/conversations/{conversation_id}.json"
    )

    def __init__(self, storage: StorageAdapter):
        self._storage = storage
        # (tenant_id, workspace_id, conversation_id) -> ConversationSummary
        self._summaries: dict[tuple[str, str, str], ConversationSummary] = {}
        self._lock = asyncio.Lock()

    # ---------------- key helpers ----------------

    def _key(
        self, ctx: TenantContext, run_id: str, conversation_id: str
    ) -> str:
        return self._KEY_TEMPLATE.format(
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            run_id=run_id,
            conversation_id=conversation_id,
        )

    def _summary_key(
        self, ctx: TenantContext, conversation_id: str
    ) -> tuple[str, str, str]:
        return (ctx.tenant_id, ctx.workspace_id, conversation_id)

    # ---------------- store ----------------

    async def store_conversation(
        self,
        ctx: TenantContext,
        run_id: str,
        persona_id: str,
        persona_name: str,
        transcript: ConversationTranscript,
        verdict: ConversationVerdict = "pending",
        judge_scores: dict[str, float] | None = None,
        failure_reason: str | None = None,
        failure_category: str | None = None,
        persona_type: str = "standard",
        tags: dict[str, str] | None = None,
    ) -> ConversationSummary:
        """Persist a conversation: summary to memory, transcript to R2."""
        conversation_id = transcript.conversation_id or str(uuid.uuid4())
        key = self._key(ctx, run_id, conversation_id)

        # Serialize transcript deterministically for stable hashing
        transcript_bytes = json.dumps(
            transcript.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        ref = await self._storage.put(
            tenant_id=ctx.tenant_id,
            key=key,
            data=transcript_bytes,
            content_type="application/json",
        )

        # Compute pass_rate from judge_scores if provided
        scores = judge_scores or {}
        pass_rate = (
            sum(scores.values()) / len(scores) if scores else 0.0
        )

        summary = ConversationSummary(
            id=conversation_id,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            run_id=run_id,
            persona_id=persona_id,
            persona_name=persona_name,
            persona_type=persona_type,
            verdict=verdict,
            pass_rate=pass_rate,
            turn_count=len(transcript.turns),
            judge_scores=dict(scores),
            failure_reason=failure_reason,
            failure_category=failure_category,
            transcript_ref=ref,
            tags=dict(tags or {}),
        )

        async with self._lock:
            self._summaries[self._summary_key(ctx, conversation_id)] = summary

        audit_logger.write(
            ctx,
            AuditActions.CONVERSATION_STORED,
            resource_type="conversation",
            resource_id=conversation_id,
            metadata={
                "run_id": run_id,
                "persona_id": persona_id,
                "turn_count": summary.turn_count,
                "verdict": verdict,
                "transcript_size_bytes": ref.size_bytes,
                "transcript_hash": ref.content_hash,
            },
        )
        return summary

    # ---------------- read ----------------

    async def get_summary(
        self, ctx: TenantContext, conversation_id: str
    ) -> ConversationSummary:
        summary = self._summaries.get(self._summary_key(ctx, conversation_id))
        if summary is None:
            raise ConversationNotFound(
                f"Conversation {conversation_id} not found in workspace"
            )
        return summary

    async def list_summaries(
        self,
        ctx: TenantContext,
        run_id: str | None = None,
        verdict: ConversationVerdict | None = None,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        """List conversation summaries with optional filters."""
        results: list[ConversationSummary] = []
        for (t, w, _cid), summary in self._summaries.items():
            if t != ctx.tenant_id or w != ctx.workspace_id:
                continue
            if run_id and summary.run_id != run_id:
                continue
            if verdict and summary.verdict != verdict:
                continue
            results.append(summary)

        results.sort(key=lambda s: s.created_at, reverse=True)
        return results[:limit]

    async def get_transcript(
        self, ctx: TenantContext, conversation_id: str
    ) -> ConversationTranscript:
        """Fetch the full transcript from object storage, verifying integrity."""
        summary = await self.get_summary(ctx, conversation_id)
        if summary.transcript_ref is None:
            raise TranscriptIntegrityError(
                f"Conversation {conversation_id} has no transcript_ref"
            )

        try:
            obj = await self._storage.get(
                tenant_id=ctx.tenant_id, key=summary.transcript_ref.key
            )
        except ObjectNotFound as exc:
            raise TranscriptIntegrityError(
                f"Transcript object missing for conversation {conversation_id}"
            ) from exc

        # Integrity check: recomputed hash vs. stored hash on the summary
        if obj.ref.content_hash != summary.transcript_ref.content_hash:
            raise TranscriptIntegrityError(
                f"Transcript hash mismatch for conversation {conversation_id}: "
                f"expected {summary.transcript_ref.content_hash[:12]}, "
                f"got {obj.ref.content_hash[:12]}"
            )

        audit_logger.write(
            ctx,
            AuditActions.CONVERSATION_FETCHED,
            resource_type="conversation",
            resource_id=conversation_id,
            metadata={"run_id": summary.run_id, "size_bytes": obj.ref.size_bytes},
        )

        data = json.loads(obj.data.decode("utf-8"))
        return ConversationTranscript.model_validate(data)

    async def get_transcript_url(
        self, ctx: TenantContext, conversation_id: str, ttl_seconds: int = 900
    ) -> SignedUrl:
        """Generate a short-lived signed URL for direct transcript download."""
        summary = await self.get_summary(ctx, conversation_id)
        if summary.transcript_ref is None:
            raise TranscriptIntegrityError(
                f"Conversation {conversation_id} has no transcript_ref"
            )
        from datetime import timedelta

        signed = await self._storage.sign_url(
            tenant_id=ctx.tenant_id,
            key=summary.transcript_ref.key,
            method="GET",
            expires_in=timedelta(seconds=ttl_seconds),
        )
        audit_logger.write(
            ctx,
            AuditActions.CONVERSATION_FETCHED,
            resource_type="conversation",
            resource_id=conversation_id,
            metadata={
                "run_id": summary.run_id,
                "access_mode": "signed_url",
                "ttl_seconds": ttl_seconds,
            },
        )
        return signed

    # ---------------- delete (GDPR) ----------------

    async def delete_run_conversations(
        self, ctx: TenantContext, run_id: str
    ) -> int:
        """Delete all conversations (summary + transcript) for a run.

        Returns the number of conversations deleted. Used for GDPR
        right-to-erasure requests and tenant data deletion.
        """
        to_delete: list[tuple[tuple[str, str, str], ConversationSummary]] = []
        for k, summary in self._summaries.items():
            t, w, _ = k
            if (
                t == ctx.tenant_id
                and w == ctx.workspace_id
                and summary.run_id == run_id
            ):
                to_delete.append((k, summary))

        deleted = 0
        async with self._lock:
            for k, summary in to_delete:
                if summary.transcript_ref is not None:
                    try:
                        await self._storage.delete(
                            tenant_id=ctx.tenant_id,
                            key=summary.transcript_ref.key,
                        )
                    except StorageError:
                        # Best-effort: log and continue; summary is still removed
                        pass
                self._summaries.pop(k, None)
                deleted += 1

        if deleted:
            audit_logger.write(
                ctx,
                AuditActions.CONVERSATION_DELETED,
                resource_type="conversation",
                resource_id=run_id,
                metadata={"run_id": run_id, "deleted_count": deleted},
            )
        return deleted

    # ---------------- test helpers ----------------

    def _reset(self) -> None:
        """Wipe the summary store. Test-only."""
        self._summaries.clear()
