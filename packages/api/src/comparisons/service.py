"""Comparison service — synchronous execution with full lifecycle (v1.2.2 §M2, §11.3)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from src.api.errors import APIError
from src.audit._compat import to_tenant_audit_event
from src.audit.logger import AuditActions, audit_logger
from src.common.models import TenantContext, utcnow
from src.common.write_context import WriteContext
from src.comparisons.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    canonical_hash,
)
from src.comparisons.models import (
    ComparisonRecord,
    ComparisonStatus,
    RunProvenance,
)
from src.comparisons.provider import ComparisonProvider, ProviderUnavailable
from src.comparisons.repository import ComparisonRepository, InMemoryComparisonRepository
from src.runs.models import RunStatus
from src.runs.service import RunService

COMPARISON_MAX_RUN_AGE_DAYS = 90


class ComparisonIneligible(APIError):
    code = "comparison_ineligible"
    http_status = 409


class IdempotencyConflict(APIError):
    code = "idempotency_conflict"
    http_status = 409


class IdempotentReplay(Exception):
    """Internal signal: this request matches an existing one — return 200, not 201."""

    def __init__(self, existing: ComparisonRecord):
        self.existing = existing


class ComparisonService:
    def __init__(
        self,
        repo: ComparisonRepository,
        run_service: RunService,
        provider: ComparisonProvider,
        idempotency: IdempotencyStore | None = None,
    ):
        self._repo = repo
        self._runs = run_service
        self._provider = provider
        self._idempotency = idempotency or InMemoryIdempotencyStore()

    async def create_comparison(
        self,
        ctx: TenantContext,
        left_run_id: str,
        right_run_id: str,
        idempotency_key: str | None = None,
    ) -> ComparisonRecord:
        body_hash = canonical_hash(ctx.workspace_id, left_run_id, right_run_id)

        # Idempotency check
        if idempotency_key:
            entry = await self._idempotency.lookup(ctx, idempotency_key)
            if entry is not None:
                if entry.body_hash != body_hash:
                    raise IdempotencyConflict(
                        "Idempotency key reused with different request body",
                        details={
                            "original_request_hash": entry.body_hash,
                            "new_request_hash": body_hash,
                        },
                    )
                # Replay: return existing
                existing = await self._repo.get(ctx, entry.comparison_id)
                raise IdempotentReplay(existing)

        # Eligibility: both runs exist, same tenant, both completed, max age
        left = await self._runs.get_run(ctx, left_run_id)
        right = await self._runs.get_run(ctx, right_run_id)
        for run in (left, right):
            if run.status != RunStatus.COMPLETED:
                raise ComparisonIneligible(
                    f"Run {run.run_id} is not completed (status={run.status.value})",
                    details={"reason": "run_not_completed", "run_id": run.run_id},
                )
            age = utcnow() - run.created_at
            if age > timedelta(days=COMPARISON_MAX_RUN_AGE_DAYS):
                raise ComparisonIneligible(
                    f"Run {run.run_id} exceeds max age of {COMPARISON_MAX_RUN_AGE_DAYS} days",
                    details={"reason": "run_too_old", "run_id": run.run_id},
                )

        # Create record in PENDING
        record = ComparisonRecord(
            # T2.6-D2 fix (Turn 2.7 Drift 2): mint a UUID4 string. The
            # comparisons.id ORM column is UUID(as_uuid=False); the
            # previous form `f"cmp_{uuid.uuid4().hex[:12]}"` was rejected
            # by asyncpg and forced Stage B's smoke script to bypass
            # this entire service path. See plan v0.2.1 §2.4 + §4.2.
            id=str(uuid.uuid4()),
            workspace_id=ctx.workspace_id,
            tenant_id=ctx.tenant_id,
            left_run_id=left_run_id,
            right_run_id=right_run_id,
            status=ComparisonStatus.PENDING,
            left_provenance=RunProvenance(
                run_id=left.run_id,
                engine_version=left.engine_version,
                asset_versions_used=left.metadata.get("asset_versions_used", []),
                run_status=left.status,
            ),
            right_provenance=RunProvenance(
                run_id=right.run_id,
                engine_version=right.engine_version,
                asset_versions_used=right.metadata.get("asset_versions_used", []),
                run_status=right.status,
            ),
        )
        # T2.6-D1 fix (Turn 2.7 Drift 4): build a WriteContext from the
        # caller's TenantContext so the repository can persist the real
        # actor_id instead of the hardcoded "system" default. The same
        # write_ctx is used for both create() calls (PENDING + COMPLETED).
        write_ctx = WriteContext.from_tenant_context(ctx)
        await self._repo.create(record, write_ctx=write_ctx)

        # Synchronous execution: PENDING -> RUNNING -> COMPLETED/FAILED
        record.status = ComparisonStatus.RUNNING
        record.started_at = utcnow()
        try:
            signals, error = await self._provider.compute(record)
            record.regression_signals = signals
            record.status = ComparisonStatus.FAILED if error else ComparisonStatus.COMPLETED
            record.error = error
        except ProviderUnavailable:
            # Don't mark FAILED — bubble up so router returns 503 cleanly
            raise
        except Exception as exc:
            record.status = ComparisonStatus.FAILED
            record.error = str(exc)
        record.completed_at = utcnow()

        # Persist completed state
        # Same write_ctx as the PENDING write — the actor doesn't change
        # between create() calls within a single service invocation.
        await self._repo.create(record, write_ctx=write_ctx)

        # Audit
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event(
                ctx,
                AuditActions.COMPARISON_CREATED,
                resource_type="comparison",
                resource_id=record.id,
                metadata={"left_run_id": left_run_id, "right_run_id": right_run_id},
            )
        )

        # Remember idempotency
        if idempotency_key:
            await self._idempotency.remember(
                ctx, idempotency_key, body_hash, record.id
            )

        return record

    async def get_comparison(self, ctx: TenantContext, comparison_id: str) -> ComparisonRecord:
        record = await self._repo.get(ctx, comparison_id)
        await audit_logger.aemit_tenant_event_safe(
            to_tenant_audit_event(
                ctx,
                AuditActions.COMPARISON_VIEWED,
                resource_type="comparison",
                resource_id=comparison_id,
            )
        )
        return record

    async def list_comparisons(self, ctx: TenantContext) -> list[ComparisonRecord]:
        return await self._repo.list_for_tenant(ctx)
