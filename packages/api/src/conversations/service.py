"""Conversation storage service.

Implements the hybrid storage pattern for conversation data:
  - Summary → repository (InMemory by default, Postgres via Turn 2.6 Step 5)
  - Full transcript → object storage (R2 in production, Local in dev/test)

The service owns the tenant-scoped storage key convention:
  {tenant_id}/{workspace_id}/runs/{run_id}/conversations/{conversation_id}.json

All public methods require a TenantContext. Cross-tenant access is
structurally impossible because every storage operation validates the
key prefix against the tenant_id.

Turn 2.6 Step 4 changes:
  * The `_summaries` dict and `_lock` move out of ConversationService
    into ``InMemoryConversationSummaryRepository`` (default impl).
  * ``ConversationService.__init__`` accepts an optional ``summary_repo``
    parameter typed against the ``ConversationSummaryRepository``
    Protocol. Defaults to a fresh InMemory impl when not supplied so all
    existing call sites keep working unchanged.
  * ``_reset()`` becomes a getattr capability check per R-7. The
    Protocol does NOT include _reset (test-only helper).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from src.audit._compat import to_tenant_audit_event_lenient
from src.audit.logger import AuditActions, audit_logger
from src.common.models import TenantContext, utcnow
from src.conversations.models import (
    ConversationSummary,
    ConversationTranscript,
    ConversationVerdict,
)
from src.conversations.repository import (
    ConversationSummaryRepository,
    InMemoryConversationSummaryRepository,
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

    Stores summaries via the injected ``ConversationSummaryRepository``
    (InMemory by default) and transcripts in the injected
    ``StorageAdapter``. Every mutation writes an audit event.
    """

    _KEY_TEMPLATE = (
        "{tenant_id}/{workspace_id}/runs/{run_id}/conversations/{conversation_id}.json"
    )

    def __init__(
        self,
        storage: StorageAdapter,
        summary_repo: ConversationSummaryRepository | None = None,
    ):
        self._storage = storage
        self._summary_repo = summary_repo or InMemoryConversationSummaryRepository()

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
        """Persist a conversation: summary via repo, transcript via storage."""
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

        await self._summary_repo.upsert(summary)

        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event_lenient(
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
        )
        return summary

    # ---------------- read ----------------

    async def get_summary(
        self, ctx: TenantContext, conversation_id: str
    ) -> ConversationSummary:
        return await self._summary_repo.get(ctx, conversation_id)

    async def list_summaries(
        self,
        ctx: TenantContext,
        run_id: str | None = None,
        verdict: ConversationVerdict | None = None,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        """List conversation summaries with optional filters."""
        return await self._summary_repo.list(
            ctx, run_id=run_id, verdict=verdict, limit=limit
        )

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

        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event_lenient(
                ctx,
                AuditActions.CONVERSATION_FETCHED,
                resource_type="conversation",
                resource_id=conversation_id,
                metadata={"run_id": summary.run_id, "size_bytes": obj.ref.size_bytes},
            )
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
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event_lenient(
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
        deleted_summaries = await self._summary_repo.delete_for_run(ctx, run_id)

        # Fan out object-storage deletes for transcripts (best-effort)
        for summary in deleted_summaries:
            if summary.transcript_ref is not None:
                try:
                    await self._storage.delete(
                        tenant_id=ctx.tenant_id,
                        key=summary.transcript_ref.key,
                    )
                except StorageError:
                    # Best-effort: log and continue; summary is already gone
                    pass

        deleted = len(deleted_summaries)
        if deleted:
            await audit_logger.aemit_tenant_event_safe(
                to_tenant_audit_event_lenient(
                    ctx,
                    AuditActions.CONVERSATION_DELETED,
                    resource_type="conversation",
                    resource_id=run_id,
                    metadata={"run_id": run_id, "deleted_count": deleted},
                )
            )
        return deleted

    # ---------------- test helpers ----------------

    def _reset(self) -> None:
        """Wipe the summary store. Test-only.

        Production repos do NOT support reset. We use a getattr capability
        check (Turn 2.6 plan v0.2.1 §6.3 / R-7) so:
          - InMemory impl (which has _reset()) → works
          - Postgres impl (which does NOT have _reset()) → RuntimeError, fail-loud

        Per R-7 review: the Protocol stays clean; this helper is invoked
        only in tests, and the RuntimeError is the correct production
        signal that a misconfigured test path called _reset on a
        Postgres-backed repo.
        """
        reset = getattr(self._summary_repo, "_reset", None)
        if reset is None:
            raise RuntimeError(
                f"{type(self._summary_repo).__name__} does not support "
                f"_reset(); this helper is InMemory-only and must not be "
                f"called in production paths."
            )
        reset()
