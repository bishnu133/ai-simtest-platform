"""
Cost Tracking Module — P4 #27 (Review-Hardened)

Enterprise-grade cost observability for AI SimTest runs.

Tracks LLM token usage and estimated cost across all components:
  - Per-component breakdown (persona_generator, quality_judge, etc.)
  - Per-model breakdown (gpt-4-turbo, ollama/llama3.1:8b, etc.)
  - Budget enforcement (soft/hard modes)
  - Usage coverage confidence indicator (high/partial/low)
  - Retry and failed-call accounting (including failed_without_usage)
  - Pricing source tracking (litellm vs free_local vs unknown)
  - Component name normalization
  - Frozen immutable report snapshots
  - Context-local global tracker (ContextVar-based)

Place under: src/cost/

Usage:
    from ai_simtest_engine.cost import RunCostTracker, RunCostContext, get_cost_tracker

    # Option 1: Context manager
    with RunCostContext(run_id="abc", budget_limit=5.0) as tracker:
        tracker.record(model="gpt-4-turbo", prompt_tokens=100, ...)
        report = tracker.finalize(total_conversations=5)

    # Option 2: Global accessor (context-local, async-safe)
    from ai_simtest_engine.cost import init_cost_tracker, get_cost_tracker
    init_cost_tracker(run_id="abc")
    get_cost_tracker().record(...)
    report = get_cost_tracker().finalize(...)

All costs are ESTIMATED based on LiteLLM pricing + usage metadata.
"""
from ai_simtest_engine.cost.models import (
    BudgetMode,
    CallStatus,
    ComponentCost,
    CostConfidence,
    CostEvent,
    CostReport,
    ModelCost,
    PricingSource,
    UsageType,
    KNOWN_COMPONENTS,
    normalize_component_name,
    normalize_subcomponent_name,
    get_unknown_components_seen,
    reset_unknown_components_tracking,
)
from ai_simtest_engine.cost.tracker import (
    BudgetExceededError,
    RunCostContext,
    RunCostTracker,
    get_cost_tracker,
    init_cost_tracker,
    reset_cost_tracker,
)
from ai_simtest_engine.cost.pricing import (
    estimate_cost,
    estimate_cost_from_usage,
    is_free_model,
)
from ai_simtest_engine.cost.cost_html import (
    generate_cost_html,
    inject_cost_into_report,
)

__all__ = [
    # Core
    "RunCostTracker",
    "RunCostContext",
    "BudgetExceededError",
    # Global accessors
    "init_cost_tracker",
    "get_cost_tracker",
    "reset_cost_tracker",
    # Models
    "CostEvent",
    "ComponentCost",
    "ModelCost",
    "CostReport",
    # Enums
    "BudgetMode",
    "CallStatus",
    "CostConfidence",
    "PricingSource",
    "UsageType",
    "KNOWN_COMPONENTS",
    # Normalization
    "normalize_component_name",
    "normalize_subcomponent_name",
    "get_unknown_components_seen",
    "reset_unknown_components_tracking",
    # Pricing
    "estimate_cost",
    "estimate_cost_from_usage",
    "is_free_model",
    # HTML
    "generate_cost_html",
    "inject_cost_into_report",
]
