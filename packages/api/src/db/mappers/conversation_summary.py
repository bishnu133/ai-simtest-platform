"""ConversationSummary domain ↔ ORM mapper.

Flattens `ConversationSummary.transcript_ref` (ObjectRef) into separate
(transcript_bucket, transcript_key) columns so SQL queries can filter on
transcript presence without JSONB traversal. Judge scores are a dict of
{judge_name: float} stored as JSONB.
"""
from __future__ import annotations

from typing import cast

from src.conversations.models import ConversationSummary as ConversationSummaryDomain
from src.conversations.models import ConversationVerdict
from src.db.models import ConversationSummary as ConversationSummaryOrm
from src.storage.models import ObjectRef


def conversation_summary_to_domain(
    row: ConversationSummaryOrm,
) -> ConversationSummaryDomain:
    transcript_ref: ObjectRef | None = None
    if row.transcript_bucket and row.transcript_key:
        transcript_ref = ObjectRef(
            backend=row.transcript_backend or "local",  # type: ignore[arg-type]
            bucket=row.transcript_bucket,
            key=row.transcript_key,
            size_bytes=0,  # ObjectRef requires size_bytes; not tracked for transcripts in 6a
            content_hash="",  # same — not tracked
        )

    return ConversationSummaryDomain(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        workspace_id=str(row.workspace_id),
        run_id=str(row.run_id),
        persona_id=row.persona_id,
        persona_name=row.persona_name,
        persona_type=row.persona_type,
        verdict=cast(ConversationVerdict, row.verdict),
        pass_rate=row.pass_rate,
        turn_count=row.turn_count,
        judge_scores=dict(row.judge_scores or {}),
        failure_reason=row.failure_reason,
        failure_category=row.failure_category,
        transcript_ref=transcript_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
        tags=dict(row.tags or {}),
    )


def conversation_summary_to_orm(
    record: ConversationSummaryDomain,
) -> ConversationSummaryOrm:
    transcript_backend = (
        record.transcript_ref.backend if record.transcript_ref else None
    )
    transcript_bucket = (
        record.transcript_ref.bucket if record.transcript_ref else None
    )
    transcript_key = record.transcript_ref.key if record.transcript_ref else None

    return ConversationSummaryOrm(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        run_id=record.run_id,
        persona_id=record.persona_id,
        persona_name=record.persona_name,
        persona_type=record.persona_type,
        verdict=record.verdict,
        pass_rate=record.pass_rate,
        turn_count=record.turn_count,
        judge_scores=dict(record.judge_scores),
        failure_reason=record.failure_reason,
        failure_category=record.failure_category,
        transcript_backend=transcript_backend,
        transcript_bucket=transcript_bucket,
        transcript_key=transcript_key,
        tags=dict(record.tags),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
