"""
Cost Tracking Data Models — P4 #27 (Review-Hardened)

Enterprise-grade cost observability for AI SimTest runs.

Models:
  - CostEvent: Single LLM call usage record (the atomic unit)
  - ComponentCost: Aggregated cost for one component (e.g. quality_judge) — FROZEN
  - ModelCost: Aggregated cost for one model (e.g. gpt-4-turbo) — FROZEN
  - CostReport: Complete run-level cost summary — FROZEN immutable snapshot

Review fixes applied:
  #3  — CostReport, ComponentCost, ModelCost are now frozen dataclasses
  #4  — Added failed_calls_without_usage as first-class metric
  #5  — Added normalize_component_name / normalize_subcomponent_name helpers
  #6  — Added resolved_model + configured_model fields on CostEvent
  #7  — Renamed confidence to usage_coverage_confidence, added confidence_notes
  #10 — Added pricing_source field + unknown_priced_models tracking in report

Design principles:
  - All costs are ESTIMATED (based on LiteLLM pricing + usage metadata)
  - Missing usage is tracked honestly, never hidden as zero
  - Extensible: usage_type field supports future non-LLM costs
  - Serializable: to_dict/from_dict for JSON export
  - Frozen snapshots: report objects are immutable after creation
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────────────

class BudgetMode(str, Enum):
    """How budget enforcement behaves when limit is crossed."""
    SOFT = "soft"    # Finish current conversation/batch, then stop
    HARD = "hard"    # Stop immediately after the crossing call


class CostConfidence(str, Enum):
    """How trustworthy the cost estimate is, based on usage metadata coverage."""
    HIGH = "high"        # >90% of calls returned usage
    PARTIAL = "partial"  # 50-90% of calls returned usage
    LOW = "low"          # <50% of calls returned usage
    NONE = "none"        # No calls made or no usage at all

    @classmethod
    def from_ratio(cls, calls_with: int, total_calls: int) -> CostConfidence:
        if total_calls == 0:
            return cls.NONE
        ratio = calls_with / total_calls
        if ratio >= 0.9:
            return cls.HIGH
        elif ratio >= 0.5:
            return cls.PARTIAL
        return cls.LOW


class PricingSource(str, Enum):
    """Where the cost estimate came from — distinguishes real pricing from unknown."""
    LITELLM = "litellm"                # LiteLLM pricing table lookup succeeded
    FREE_LOCAL = "free_local"          # Known free/local model (Ollama, LM Studio)
    UNKNOWN = "unknown"                # Pricing lookup failed — cost defaulted to $0
    ZERO_TOKENS = "zero_tokens"        # No tokens to price
    NO_MODEL = "no_model"              # Empty model string


class UsageType(str, Enum):
    """Type of usage being tracked. Extensible for future non-LLM costs."""
    LLM_TEXT = "llm_text"
    EMBEDDING = "embedding"       # Future
    RERANKER = "reranker"         # Future
    TOOL_CALL = "tool_call"       # Future
    OTHER = "other"               # Future


class CallStatus(str, Enum):
    """Status of an individual LLM call."""
    SUCCESS = "success"
    FAILED_WITH_USAGE = "failed_with_usage"
    FAILED_NO_USAGE = "failed_no_usage"
    RETRIED = "retried"


# ── Known Components ──────────────────────────────────────────────────────────

KNOWN_COMPONENTS = [
    "persona_generator",
    "user_simulator",
    "quality_judge",
    "workflow_judge",
    "expansion",
    "rag_eval",
    "replay",
    "discovery",
    "document_analyzer",
    "other",
]


# ── Component Name Normalization (Review #5) ──────────────────────────────────

# Set of known components for validation (lowercase, underscore-separated)
_KNOWN_COMPONENTS_SET = frozenset(KNOWN_COMPONENTS)

# Track unknown component names seen (for observability)
_unknown_components_seen: set[str] = set()


def _camel_to_snake(name: str) -> str:
    """
    Convert camelCase or PascalCase to snake_case.

    Examples:
      "workflowJudge"    → "workflow_judge"
      "PascalCaseValue"  → "pascal_case_value"
      "HTMLParser"       → "html_parser"
      "getHTTPResponse"  → "get_http_response"
      "already_snake"    → "already_snake"
    """
    # Insert underscore before uppercase letters that follow a lowercase/digit
    result = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    # Insert underscore between consecutive uppercase and uppercase+lowercase
    result = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', result)
    return result.lower()


def normalize_component_name(name: str) -> str:
    """
    Normalize component name to prevent inconsistent buckets.

    Rules:
      - Strip leading/trailing whitespace
      - Convert camelCase/PascalCase to snake_case
      - Lowercase
      - Replace spaces and hyphens with underscores
      - Collapse multiple underscores
      - Empty string becomes "other"
      - Logs a debug warning for unknown component names not in KNOWN_COMPONENTS

    Examples:
      "Workflow Judge"  → "workflow_judge"
      "workflow-judge"  → "workflow_judge"
      "workflowJudge"  → "workflow_judge"
      "PascalCase"      → "pascal_case"
      "  Quality_Judge " → "quality_judge"
    """
    if not name or not name.strip():
        return "other"
    normalized = name.strip()
    # Apply camelCase → snake_case before lowercasing
    normalized = _camel_to_snake(normalized)
    normalized = normalized.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized)
    normalized = normalized.strip("_")
    result = normalized or "other"

    # Review fix 5: Track unknown component names
    if result not in _KNOWN_COMPONENTS_SET:
        if result not in _unknown_components_seen:
            _unknown_components_seen.add(result)
            logger.debug(
                "unknown_component_name",
                extra={
                    "normalized": result,
                    "original": name,
                    "known_components": list(KNOWN_COMPONENTS),
                },
            )
    return result


def normalize_subcomponent_name(name: str) -> str:
    """
    Normalize subcomponent name using the same rules as component.

    Returns empty string if input is empty/whitespace.
    No KNOWN_COMPONENTS check for subcomponents (they are freeform).
    """
    if not name or not name.strip():
        return ""
    normalized = name.strip()
    normalized = _camel_to_snake(normalized)
    normalized = normalized.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def get_unknown_components_seen() -> set[str]:
    """Return the set of unknown component names observed so far (for observability)."""
    return set(_unknown_components_seen)


def reset_unknown_components_tracking() -> None:
    """Reset the unknown components tracker. Used in tests."""
    _unknown_components_seen.clear()


# ── CostEvent (atomic usage record) ──────────────────────────────────────────

@dataclass
class CostEvent:
    """
    Single LLM call usage record — the atomic unit of cost tracking.

    One CostEvent is created for every LLM call that returns (or fails).
    """
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""
    model: str = ""
    configured_model: str = ""     # Review #6: what the user configured
    resolved_model: str = ""       # Review #6: what actually ran (if known)
    provider: str = ""
    component: str = "other"
    subcomponent: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    status: str = CallStatus.SUCCESS.value
    has_usage: bool = True
    usage_type: str = UsageType.LLM_TEXT.value
    pricing_source: str = PricingSource.LITELLM.value  # Review #10
    request_id: str = ""       # Provider request ID if available
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "model": self.model,
            "configured_model": self.configured_model,
            "resolved_model": self.resolved_model,
            "provider": self.provider,
            "component": self.component,
            "subcomponent": self.subcomponent,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "status": self.status,
            "has_usage": self.has_usage,
            "usage_type": self.usage_type,
            "pricing_source": self.pricing_source,
            "request_id": self.request_id,
        }


# ── ComponentCost (aggregated per component) — FROZEN ─────────────────────────

@dataclass(frozen=True)
class ComponentCost:
    """Aggregated cost for one component (e.g. quality_judge). Immutable after creation."""
    component: str = ""
    total_calls: int = 0
    calls_with_usage: int = 0
    calls_without_usage: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # Subcomponent breakdown — tuple of pairs for immutability
    subcomponents: tuple = ()  # tuple of (name, ComponentCost) pairs

    def get_subcomponents_dict(self) -> dict[str, "ComponentCost"]:
        """Get subcomponents as a dict (convenience accessor)."""
        return dict(self.subcomponents)

    def to_dict(self) -> dict:
        d = {
            "component": self.component,
            "total_calls": self.total_calls,
            "calls_with_usage": self.calls_with_usage,
            "calls_without_usage": self.calls_without_usage,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }
        if self.subcomponents:
            d["subcomponents"] = {
                k: v.to_dict() for k, v in self.subcomponents
            }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ComponentCost:
        subs = tuple(
            (k, ComponentCost.from_dict(v))
            for k, v in data.get("subcomponents", {}).items()
        )
        return cls(
            component=data.get("component", ""),
            total_calls=data.get("total_calls", 0),
            calls_with_usage=data.get("calls_with_usage", 0),
            calls_without_usage=data.get("calls_without_usage", 0),
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
            subcomponents=subs,
        )


# ── ModelCost (aggregated per model) — FROZEN ─────────────────────────────────

@dataclass(frozen=True)
class ModelCost:
    """Aggregated cost for one model (e.g. gpt-4-turbo). Immutable after creation."""
    model: str = ""
    provider: str = ""
    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "provider": self.provider,
            "total_calls": self.total_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelCost:
        return cls(
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            total_calls=data.get("total_calls", 0),
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
        )


# ── CostReport (immutable run-level snapshot) — FROZEN ────────────────────────

@dataclass(frozen=True)
class CostReport:
    """
    Complete cost summary for a simulation run.

    This is a FROZEN IMMUTABLE SNAPSHOT produced by tracker.finalize().
    All reporters (console, HTML, JSON) consume this snapshot.
    All costs are ESTIMATED based on LiteLLM pricing + usage metadata.

    Review #3: frozen=True enforces immutability at the dataclass level.
    Review #4: failed_calls_without_usage is now a first-class metric.
    Review #7: confidence renamed to usage_coverage_confidence + notes.
    Review #10: unknown_priced_models + pricing_lookup_failures tracked.
    """
    # Run identification
    run_id: str = ""

    # Totals
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    total_calls: int = 0

    # Usage coverage
    calls_with_usage: int = 0
    calls_without_usage: int = 0
    # Review #7: renamed from cost_confidence
    usage_coverage_confidence: str = CostConfidence.NONE.value
    confidence_notes: str = ""

    # Call status breakdown — Review #4: added failed_calls_without_usage
    successful_calls: int = 0
    failed_calls_with_usage: int = 0
    failed_calls_without_usage: int = 0
    retried_calls: int = 0

    # Breakdowns (tuples for frozen dataclass compatibility)
    per_component: tuple = ()   # tuple of (name, ComponentCost) pairs
    per_model: tuple = ()       # tuple of (name, ModelCost) pairs

    # Derived metrics
    cost_per_completed_conversation: float = 0.0
    cost_per_turn: float = 0.0
    total_conversations: int = 0
    total_turns: int = 0

    # Budget
    budget_limit: Optional[float] = None
    budget_mode: str = BudgetMode.SOFT.value
    budget_exceeded: bool = False
    budget_exceeded_at_cost: float = 0.0
    budget_exceeded_at_timestamp: float = 0.0
    budget_exceeded_event_index: int = -1

    # Highest cost identifiers
    highest_cost_component: str = ""
    highest_cost_model: str = ""

    # Review #10: Pricing observability
    pricing_lookup_failures: int = 0
    unknown_priced_models: tuple = ()   # tuple of model name strings
    zero_cost_due_to_unknown_pricing: int = 0

    # Backward-compatible property
    @property
    def cost_confidence(self) -> str:
        """Backward compatibility alias for usage_coverage_confidence."""
        return self.usage_coverage_confidence

    def get_per_component(self) -> dict[str, ComponentCost]:
        """Get per_component as a dict (convenience accessor)."""
        return dict(self.per_component)

    def get_per_model(self) -> dict[str, ModelCost]:
        """Get per_model as a dict (convenience accessor)."""
        return dict(self.per_model)

    def to_dict(self) -> dict:
        """Serialize for JSON export. All costs labeled as estimated."""
        return {
            "run_id": self.run_id,
            "note": "All costs are ESTIMATED based on LiteLLM pricing and returned usage metadata. "
                    "Actual provider billing may differ.",
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_estimated_cost_usd": round(self.total_estimated_cost_usd, 6),
            "total_calls": self.total_calls,
            "calls_with_usage": self.calls_with_usage,
            "calls_without_usage": self.calls_without_usage,
            "usage_coverage_confidence": self.usage_coverage_confidence,
            "confidence_notes": self.confidence_notes,
            "successful_calls": self.successful_calls,
            "failed_calls_with_usage": self.failed_calls_with_usage,
            "failed_calls_without_usage": self.failed_calls_without_usage,
            "retried_calls": self.retried_calls,
            "cost_per_completed_conversation": round(self.cost_per_completed_conversation, 6),
            "cost_per_turn": round(self.cost_per_turn, 6),
            "total_conversations": self.total_conversations,
            "total_turns": self.total_turns,
            "budget_limit": self.budget_limit,
            "budget_mode": self.budget_mode,
            "budget_exceeded": self.budget_exceeded,
            "budget_exceeded_at_cost": round(self.budget_exceeded_at_cost, 6),
            "budget_exceeded_at_timestamp": self.budget_exceeded_at_timestamp,
            "budget_exceeded_event_index": self.budget_exceeded_event_index,
            "highest_cost_component": self.highest_cost_component,
            "highest_cost_model": self.highest_cost_model,
            "pricing_lookup_failures": self.pricing_lookup_failures,
            "unknown_priced_models": list(self.unknown_priced_models),
            "zero_cost_due_to_unknown_pricing": self.zero_cost_due_to_unknown_pricing,
            "per_component": {
                k: v.to_dict() for k, v in self.per_component
            },
            "per_model": {
                k: v.to_dict() for k, v in self.per_model
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> CostReport:
        per_comp = tuple(
            (k, ComponentCost.from_dict(v))
            for k, v in data.get("per_component", {}).items()
        )
        per_mod = tuple(
            (k, ModelCost.from_dict(v))
            for k, v in data.get("per_model", {}).items()
        )
        return cls(
            run_id=data.get("run_id", ""),
            total_prompt_tokens=data.get("total_prompt_tokens", 0),
            total_completion_tokens=data.get("total_completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            total_estimated_cost_usd=data.get("total_estimated_cost_usd", 0.0),
            total_calls=data.get("total_calls", 0),
            calls_with_usage=data.get("calls_with_usage", 0),
            calls_without_usage=data.get("calls_without_usage", 0),
            usage_coverage_confidence=data.get(
                "usage_coverage_confidence",
                data.get("cost_confidence", CostConfidence.NONE.value),
            ),
            confidence_notes=data.get("confidence_notes", ""),
            successful_calls=data.get("successful_calls", 0),
            failed_calls_with_usage=data.get("failed_calls_with_usage", 0),
            failed_calls_without_usage=data.get("failed_calls_without_usage", 0),
            retried_calls=data.get("retried_calls", 0),
            per_component=per_comp,
            per_model=per_mod,
            cost_per_completed_conversation=data.get("cost_per_completed_conversation", 0.0),
            cost_per_turn=data.get("cost_per_turn", 0.0),
            total_conversations=data.get("total_conversations", 0),
            total_turns=data.get("total_turns", 0),
            budget_limit=data.get("budget_limit"),
            budget_mode=data.get("budget_mode", BudgetMode.SOFT.value),
            budget_exceeded=data.get("budget_exceeded", False),
            budget_exceeded_at_cost=data.get("budget_exceeded_at_cost", 0.0),
            budget_exceeded_at_timestamp=data.get("budget_exceeded_at_timestamp", 0.0),
            budget_exceeded_event_index=data.get("budget_exceeded_event_index", -1),
            highest_cost_component=data.get("highest_cost_component", ""),
            highest_cost_model=data.get("highest_cost_model", ""),
            pricing_lookup_failures=data.get("pricing_lookup_failures", 0),
            unknown_priced_models=tuple(data.get("unknown_priced_models", [])),
            zero_cost_due_to_unknown_pricing=data.get("zero_cost_due_to_unknown_pricing", 0),
        )
