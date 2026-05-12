"""Repository write metadata (Turn 2.7 Drift 4, plan v0.2.1 §2.4).

`WriteContext` carries metadata about a write operation that is execution-
time, not domain-time: who performed the write, the correlation id of
the request, the source system, and similar fields.

Domain models (e.g., ComparisonRecord, RunRecord) intentionally do NOT
carry these fields. They describe the resource being persisted, not the
write itself.

Repository contract (post-Turn-2.7 Drift 4):
    repo.create(record, *, write_ctx: WriteContext | None = None, session=...)

`write_ctx=None` (the default) yields `WriteContext.system()` semantics
at the persistence layer — preserves backward compatibility for callers
that haven't been threaded yet (smoke scripts, fixtures, in-memory uses).

Why a dedicated module (and not just adding `actor` directly to repo
signatures, the v0.1 design Bishnu pushed back on):

  * Forward-compatibility. Future fields (correlation_id, source,
    audit_reason, idempotency_key) get added once here, not threaded
    through every repository signature.
  * Layering hygiene. The mapper layer (src.db.mappers.*) sits below
    the service layer in the dependency graph; it must not import
    TenantContext (which lives in src.common.models). WriteContext is a
    thin value object the mapper CAN safely import without dragging in
    the service layer's full surface.
  * Audit/audit_logger convergence (Future-1). audit_logger.write today
    takes (ctx, action, ...) parameters. A future refactor converges
    audit_logger and repository writes onto a single WriteContext —
    this module is the seed.

Plan reference: turn_2_7_plan_v0_2_1.md §2.4
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.common.models import ActorRef

if TYPE_CHECKING:
    from src.common.models import TenantContext


@dataclass(frozen=True)
class WriteContext:
    """Repository-write metadata.

    Frozen because it should not be mutated mid-transaction; build a
    new one if a downstream layer needs to add fields (e.g.,
    audit_reason at the audit_logger boundary).

    Fields (Turn 2.7 Drift 4 — minimal viable surface):
        actor: the ActorRef performing the write. Defaults to the
            system actor if not provided, preserving back-compat for
            callers not yet threaded through.

    Reserved for Turn 2.8+ (Future-1 — audit/actor convergence):
        correlation_id: str | None
            Request correlation id for distributed-trace stitching.
        source: str | None
            Originating subsystem (e.g., "api.v1", "celery.runs", "cli").
        audit_reason: str | None
            Human-readable rationale, written into audit_events.

    These reserved fields are deliberately NOT in the v1 dataclass body.
    Adding them now without a use-case would invite premature coupling
    with audit_logger. They're documented here so the convergence path
    is obvious.
    """

    actor: ActorRef = field(default_factory=ActorRef.system)

    @classmethod
    def system(cls) -> "WriteContext":
        """Standard system-write context for internal operations.

        Equivalent to ``WriteContext()`` (the actor field defaults to
        ``ActorRef.system()``), but the named factory makes the
        intent explicit at call sites:
            await repo.create(record, write_ctx=WriteContext.system())

        Used by smoke scripts, fixtures, and any non-context-having
        caller that nonetheless wants to be explicit about the actor
        rather than relying on the None-fallback in the mapper.
        """
        return cls(actor=ActorRef.system())

    @classmethod
    def from_tenant_context(cls, ctx: "TenantContext") -> "WriteContext":
        """Build a WriteContext from a TenantContext — the common case
        at the service layer.

        Duck-typed on ``ctx.actor`` so this method works for any caller
        that exposes an `actor: ActorRef` attribute, not just instances
        of TenantContext. This avoids a runtime import of TenantContext
        (which would leak the service layer's domain model into this
        otherwise-low-level module).
        """
        return cls(actor=ctx.actor)
