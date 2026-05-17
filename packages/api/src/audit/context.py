"""Pure-domain audit context types.

FH-Tier-1 Slice 1 (plan v0.3.4 §5; Phase B gate approved with refinements).
Defines:

  - ``AuditContext``: request-scoped threading context for tenant-scoped
    audit events.
  - ``PretenantAuditEvent``: domain event for pre-authentication failure
    cases. Maps 1:1 to the ``audit_pretenant_insert(...)`` SECURITY
    DEFINER function parameter set (Slice 0 / migration 0008 contract).
  - ``TenantAuditEvent``: domain event for tenant-scoped audit rows.
  - ``PRETENANT_ACTION_ALLOWLIST`` / ``ACTOR_TYPE_ALLOWLIST``: sentinels.

Pure-domain invariant (test-enforced):

  This module imports ONLY from the Python standard library:
  ``__future__``, ``dataclasses``, ``typing``, ``uuid``, ``json``.

  It MUST NOT import from:
    * ``src.db.*``, ``src.app_factory*``, any repository module
    * ``src.audit.logger`` (would create a future import cycle)
    * SQLAlchemy, asyncpg, pydantic, FastAPI, or any non-stdlib package

  Adding such imports breaks
  ``test_audit_context_module_has_no_infrastructure_imports``.

Runtime type strictness (enforced in ``__post_init__``):

  ``dataclass`` type hints are documentation, not validation. Slice 1
  explicitly validates:

    * ``AuditContext.tenant_id``      must be a ``UUID`` instance — TypeError if not
    * ``AuditContext.workspace_id``   if not None, must be ``UUID``  — TypeError if not
    * ``PretenantAuditEvent.details`` must be ``dict``               — TypeError if not
    * ``TenantAuditEvent.details``    must be ``dict``               — TypeError if not

  Rationale: v0.3.4 §5 ground rule "Keep ``tenant_id`` strict as UUID;
  do not auto-convert" requires runtime enforcement, not just a type
  hint. ``details`` validation prevents non-JSON-object values
  (string / list / None) from silently round-tripping through
  ``json.dumps`` and landing in the ``jsonb`` column as something other
  than an object.

Defense in depth for the pretenant action allowlist:

  Layer 1: ``PretenantAction = Literal["auth.rejected"]`` (type system)
  Layer 2: ``PretenantAuditEvent.__post_init__`` raises (runtime)
  Layer 3: ``audit_pretenant_insert`` function body (Slice 0 / 0008)
  Layer 4: ``ck_audit_tenant_required_or_pretenant`` CHECK (Slice 0 / 0005)

  Widening (e.g., adding ``'auth.suspended'``) requires lockstep changes
  at all four layers via a coordinated migration. See plan v0.3.4 §5.

Future-1 convergence note:

  ``WriteContext`` (``src/common/write_context.py``) carries ``ActorRef``-
  based write metadata for repository operations. A future refactor
  (Future-1 / Tier-2) converges ``AuditContext`` and ``WriteContext`` so
  both layers share a single value object.

  Slice 1 intentionally keeps ``AuditContext`` with simple
  ``actor_id`` / ``actor_type`` / ``actor_display`` fields rather than
  importing ``ActorRef``, because the audit subsystem maps directly to
  ``audit_events`` table columns and a parallel structure keeps the
  pure-domain rule unambiguous in this slice.

  Convergence will add a ``from_write_context()`` classmethod (or
  equivalent) without changing the ``to_function_args()`` contract.

Plan reference: ``fh_tier1_plan_v0_3_4.md`` §5, §8.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal
from uuid import UUID


# ============================================================================
# Type aliases — closed sets at the type system layer
# ============================================================================

PretenantAction = Literal["auth.rejected"]
"""Closed set of pretenant action values. Widening requires migration."""

ActorType = Literal["human", "service_account", "system", "support"]
"""Closed set of actor_type values. Matches the audit_events table
_ACTOR_TYPE_CHECK constraint and the audit_pretenant_insert function
allowlist."""


# ============================================================================
# Sentinels — single source of truth for pretenant / actor semantics
# ============================================================================

PRETENANT_ACTION_ALLOWLIST: Final[frozenset[str]] = frozenset({"auth.rejected"})
"""Runtime allowlist of actions permitted for pretenant audit events.

Mirrors the audit_pretenant_insert function's IF-block (Slice 0 / 0008)
and the ck_audit_tenant_required_or_pretenant CHECK constraint (0005).
"""

ACTOR_TYPE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {"human", "service_account", "system", "support"}
)
"""Runtime allowlist of actor_type values. Mirrors the ORM-level
_ACTOR_TYPE_CHECK constraint and the audit_pretenant_insert function's
actor_type allowlist."""

PRETENANT_DEFAULT_ACTOR_ID: Final[str] = "anonymous"
PRETENANT_DEFAULT_ACTOR_TYPE: Final[ActorType] = "system"
PRETENANT_DEFAULT_RESOURCE_TYPE: Final[str] = "auth"
PRETENANT_DEFAULT_RESOURCE_ID: Final[str] = "session"


# ============================================================================
# AuditContext — threading context for tenant-scoped events
# ============================================================================

@dataclass(frozen=True, kw_only=True, slots=True)
class AuditContext:
    """Request-scoped context for tenant-scoped audit events.

    Threaded through service / repository / mapper layers per the AM-4
    pattern. Carries actor info, tenant_id, and request-correlation
    metadata that downstream audit event creation needs.

    Forward-compatible with WriteContext convergence (Future-1).

    Pure domain type — no DB, ORM, or infrastructure imports.

    Runtime invariants enforced in ``__post_init__``:

      * ``tenant_id`` is a ``UUID`` instance (no string auto-conversion)
      * ``workspace_id``, if not None, is a ``UUID`` instance
      * ``actor_id`` is a non-empty string
      * ``actor_type`` is in ``ACTOR_TYPE_ALLOWLIST``
    """

    tenant_id: UUID
    actor_id: str
    actor_type: ActorType
    workspace_id: UUID | None = None
    actor_display: str | None = None
    correlation_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    def __post_init__(self) -> None:
        # Type validation first — fail fast on wrong-typed inputs before
        # touching value-level invariants.
        if not isinstance(self.tenant_id, UUID):
            raise TypeError(
                f"AuditContext.tenant_id must be a UUID instance "
                f"(got {type(self.tenant_id).__name__}); convert at the "
                f"API/provider boundary, not here (plan v0.3.4 §5)"
            )
        if self.workspace_id is not None and not isinstance(self.workspace_id, UUID):
            raise TypeError(
                f"AuditContext.workspace_id, when set, must be a UUID "
                f"instance (got {type(self.workspace_id).__name__})"
            )
        # Value-level invariants.
        if not self.actor_id:
            raise ValueError("AuditContext.actor_id must be a non-empty string")
        if self.actor_type not in ACTOR_TYPE_ALLOWLIST:
            raise ValueError(
                f"AuditContext.actor_type {self.actor_type!r} not in allowlist "
                f"{sorted(ACTOR_TYPE_ALLOWLIST)}"
            )

    def with_correlation(self, correlation_id: str) -> AuditContext:
        """Return a copy with ``correlation_id`` set (frozen-safe)."""
        return replace(self, correlation_id=correlation_id)


# ============================================================================
# TenantAuditEvent — tenant-scoped audit row
# ============================================================================

@dataclass(frozen=True, kw_only=True, slots=True)
class TenantAuditEvent:
    """A tenant-scoped audit event ready to persist.

    Maps to the audit_events table under the standard audit_events_insert
    RLS policy (tenant_id required, matched against the session's
    ``current_setting('app.current_tenant_id')``).

    Pretenant-only actions (e.g., ``auth.rejected``) are explicitly
    rejected at construction — use ``PretenantAuditEvent`` for those.

    Pure domain type — no DB or ORM imports.

    Runtime invariants enforced in ``__post_init__``:

      * ``details`` is a ``dict`` (rejects list / str / None / etc.)
      * ``action`` is a non-empty string
      * ``action`` is NOT in ``PRETENANT_ACTION_ALLOWLIST``
      * ``resource_type`` / ``resource_id``, when set, are non-empty
    """

    action: str
    context: AuditContext  # carries tenant_id, actor info, etc.
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    asset_versions: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # Type validation first.
        if not isinstance(self.details, dict):
            raise TypeError(
                f"TenantAuditEvent.details must be a dict "
                f"(got {type(self.details).__name__}); audit details "
                f"must be a JSON object"
            )
        # Value-level invariants.
        if not self.action:
            raise ValueError("TenantAuditEvent.action must be a non-empty string")
        if self.action in PRETENANT_ACTION_ALLOWLIST:
            raise ValueError(
                f"TenantAuditEvent rejected: action {self.action!r} is reserved "
                f"for PretenantAuditEvent (pretenant allowlist: "
                f"{sorted(PRETENANT_ACTION_ALLOWLIST)})"
            )
        if self.resource_type is not None and not self.resource_type:
            raise ValueError(
                "TenantAuditEvent.resource_type, when set, must be non-empty "
                "(use None to leave unset)"
            )
        if self.resource_id is not None and not self.resource_id:
            raise ValueError(
                "TenantAuditEvent.resource_id, when set, must be non-empty "
                "(use None to leave unset)"
            )


# ============================================================================
# PretenantAuditEvent — pre-authentication audit row
# ============================================================================

@dataclass(frozen=True, kw_only=True, slots=True)
class PretenantAuditEvent:
    """An audit event for pre-authentication failure cases.

    Maps 1:1 to the ``audit_pretenant_insert(...)`` SECURITY DEFINER
    function parameter set (v0.3.4 §5 contract). Persists with
    ``tenant_id IS NULL`` via the function's BYPASSRLS path.

    Every field's default matches the corresponding PostgreSQL function
    parameter default, so ``PretenantAuditEvent()`` with no kwargs maps
    to ``SELECT audit_pretenant_insert()`` (all positional args omitted).

    Pure domain type — no DB or ORM imports.

    Runtime invariants enforced in ``__post_init__``:

      * ``details`` is a ``dict`` (rejects list / str / None / etc.)
      * ``action`` is a non-empty string and in ``PRETENANT_ACTION_ALLOWLIST``
      * ``actor_type`` is in ``ACTOR_TYPE_ALLOWLIST``
      * ``actor_id`` / ``resource_type`` / ``resource_id`` are non-empty strings
    """

    action: PretenantAction = "auth.rejected"
    actor_id: str = PRETENANT_DEFAULT_ACTOR_ID
    actor_type: ActorType = PRETENANT_DEFAULT_ACTOR_TYPE
    resource_type: str = PRETENANT_DEFAULT_RESOURCE_TYPE
    resource_id: str = PRETENANT_DEFAULT_RESOURCE_ID
    correlation_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None

    def __post_init__(self) -> None:
        # Type validation first.
        if not isinstance(self.details, dict):
            raise TypeError(
                f"PretenantAuditEvent.details must be a dict "
                f"(got {type(self.details).__name__}); audit details "
                f"must be a JSON object"
            )
        # Value-level invariants.
        if not self.action:
            raise ValueError(
                "PretenantAuditEvent.action must be a non-empty string"
            )
        if self.action not in PRETENANT_ACTION_ALLOWLIST:
            raise ValueError(
                f"PretenantAuditEvent rejected: action {self.action!r} not in "
                f"pretenant allowlist {sorted(PRETENANT_ACTION_ALLOWLIST)} "
                f"(use TenantAuditEvent for tenant-scoped events)"
            )
        if self.actor_type not in ACTOR_TYPE_ALLOWLIST:
            raise ValueError(
                f"PretenantAuditEvent.actor_type {self.actor_type!r} not in "
                f"allowlist {sorted(ACTOR_TYPE_ALLOWLIST)}"
            )
        if not self.actor_id:
            raise ValueError(
                "PretenantAuditEvent.actor_id must be a non-empty string"
            )
        if not self.resource_type:
            raise ValueError(
                "PretenantAuditEvent.resource_type must be a non-empty string"
            )
        if not self.resource_id:
            raise ValueError(
                "PretenantAuditEvent.resource_id must be a non-empty string"
            )

    def to_function_args(self) -> dict[str, Any]:
        """Map to keyword arguments for ``audit_pretenant_insert(...)``.

        Returns a dict with exactly 9 keys matching the function's
        parameter names (without the ``p_`` prefix). Slice 4's repository
        layer binds these to the SQL function call:

        .. code-block:: sql

            SELECT audit_pretenant_insert(
                :action,
                :actor_id,
                :actor_type,
                :resource_type,
                :resource_id,
                :correlation_id,
                :details::jsonb,
                :ip_address::inet,
                :user_agent
            )

        ``details`` is JSON-serialized via ``json.dumps(..., default=str)``
        so common non-JSON-native audit values (``UUID``, ``datetime``)
        are coerced to their ``str()`` representation rather than raising
        ``TypeError``. The repository layer applies the ``::jsonb`` cast
        in the SQL text itself.
        """
        return {
            "action": self.action,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "correlation_id": self.correlation_id,
            "details": json.dumps(self.details, default=str),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }


# ============================================================================
# Public API
# ============================================================================

__all__ = [
    # Type aliases
    "ActorType",
    "PretenantAction",
    # Sentinels — allowlists
    "ACTOR_TYPE_ALLOWLIST",
    "PRETENANT_ACTION_ALLOWLIST",
    # Sentinels — defaults
    "PRETENANT_DEFAULT_ACTOR_ID",
    "PRETENANT_DEFAULT_ACTOR_TYPE",
    "PRETENANT_DEFAULT_RESOURCE_ID",
    "PRETENANT_DEFAULT_RESOURCE_TYPE",
    # Dataclasses
    "AuditContext",
    "PretenantAuditEvent",
    "TenantAuditEvent",
]
