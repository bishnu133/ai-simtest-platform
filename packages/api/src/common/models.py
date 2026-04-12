"""Foundation types shared across all services.

These are the core data shapes referenced throughout the SaaS control plane.
They are kept intentionally small and dependency-free so they can be imported
from any module without circular import risk.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Actor model — who performed an action
# ---------------------------------------------------------------------------

ActorType = Literal["human", "service_account", "system", "support"]


class ActorRef(BaseModel):
    """Reference to the actor (user or service) that performed an action.

    Every audit entry and every asset state transition carries one of these.
    """

    model_config = ConfigDict(frozen=True)

    actor_id: str
    actor_type: ActorType
    display_name: str | None = None
    email: str | None = None

    @classmethod
    def system(cls) -> ActorRef:
        """Standard system actor for internal operations."""
        return cls(
            actor_id="system",
            actor_type="system",
            display_name="AI SimTest System",
        )


# ---------------------------------------------------------------------------
# Asset Registry — 7 first-class versioned resource types
# ---------------------------------------------------------------------------


class AssetType(str, Enum):
    """The 7 first-class asset types managed by the registry."""

    JUDGE_PACK = "judge_pack"
    POLICY_PACK = "policy_pack"
    SCENARIO_PACK = "scenario_pack"
    WORKFLOW_PACK = "workflow_pack"
    DATASET = "dataset"
    BOT_PROFILE = "bot_profile"
    SCORECARD = "scorecard"


class AssetStatus(str, Enum):
    """Lifecycle states for versioned assets."""

    DRAFT = "draft"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class AssetRef(BaseModel):
    """Immutable reference to a specific version of an asset.

    RunContract carries these instead of inline configs so that runs are
    reproducible and integrity-verifiable.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: str
    asset_type: AssetType
    version: int
    content_hash: str  # SHA-256 hex digest of resolved content

    @property
    def qualified_id(self) -> str:
        return f"{self.asset_type.value}:{self.asset_id}@v{self.version}"


# ---------------------------------------------------------------------------
# Tenant context — set by middleware, carried through the request
# ---------------------------------------------------------------------------


class TenantContext(BaseModel):
    """Request-scoped tenant and workspace identity.

    Populated by tenant middleware after auth. Any service method that
    touches tenant data requires one of these.
    """

    tenant_id: str
    workspace_id: str
    actor: ActorRef
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# Pagination — enterprise-safe cursor pagination (no OFFSET at scale)
# ---------------------------------------------------------------------------


class CursorPage(BaseModel):
    """Generic cursor-paginated response envelope."""

    items: list[Any]
    next_cursor: str | None = None
    has_more: bool = False


def utcnow() -> datetime:
    """Timezone-aware UTC now. Single source of truth for timestamps."""
    return datetime.now(timezone.utc)
