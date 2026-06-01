"""
Cost Pricing — LiteLLM pricing wrapper with safe fallbacks.

Uses litellm.cost_per_token() which covers 300+ models.
Returns $0.00 for local/free models (Ollama, LM Studio).
Gracefully handles unknown models without crashing.

Review #10: Now returns PricingSource alongside cost so callers can
distinguish "genuinely free local model" from "cloud model whose
pricing lookup failed." This is critical for cost reporting trust.

All values are ESTIMATED. Provider billing may differ due to:
  - Cached token pricing differences
  - Reasoning token categories
  - Provider pricing changes
  - Special billing rules
"""
from __future__ import annotations

import logging
from typing import Optional

from ai_simtest_engine.cost.models import PricingSource

logger = logging.getLogger(__name__)

# Models known to be free/local (cost = $0)
_FREE_PREFIXES = ("ollama/", "ollama_chat/", "lm_studio/")


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[float, PricingSource]:
    """
    Estimate USD cost for a single LLM call.

    Uses LiteLLM's built-in pricing table.
    Returns (0.0, PricingSource.FREE_LOCAL) for local/free models.
    Returns (0.0, PricingSource.UNKNOWN) for unknown models (pricing lookup failed).
    Never raises — returns (0.0, source) on any error.

    Args:
        model: Model string (e.g. "gpt-4-turbo", "ollama/llama3.1:8b")
        prompt_tokens: Input tokens consumed
        completion_tokens: Output tokens generated

    Returns:
        Tuple of (estimated_cost_usd, pricing_source).
    """
    if not model:
        return 0.0, PricingSource.NO_MODEL

    if prompt_tokens == 0 and completion_tokens == 0:
        return 0.0, PricingSource.ZERO_TOKENS

    # Local models are free
    model_lower = model.lower()
    if any(model_lower.startswith(p) for p in _FREE_PREFIXES):
        return 0.0, PricingSource.FREE_LOCAL

    try:
        import litellm
        # litellm.cost_per_token returns (prompt_cost_per_token, completion_cost_per_token)
        # Note: This is per-token cost, NOT total cost. We multiply by token counts ourselves.
        # LiteLLM may raise NotFoundError, ValueError, or other exceptions for unknown models.
        prompt_cost_per_token, completion_cost_per_token = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        cost = (prompt_cost_per_token * prompt_tokens) + (completion_cost_per_token * completion_tokens)
        return max(0.0, cost), PricingSource.LITELLM
    except Exception as exc:
        # Unknown model or litellm error — return 0 with UNKNOWN source
        logger.debug(
            "cost_estimate_fallback: pricing lookup failed for model=%s "
            "(prompt_tokens=%d, completion_tokens=%d, error_type=%s, error=%s)",
            model, prompt_tokens, completion_tokens,
            type(exc).__name__, str(exc)[:200],
        )
        return 0.0, PricingSource.UNKNOWN


def estimate_cost_from_usage(
    model: str,
    usage: Optional[object],
) -> tuple[int, int, int, float, PricingSource]:
    """
    Extract token counts from a LiteLLM usage object and estimate cost.

    Args:
        model: Model string
        usage: LiteLLM response.usage object (has prompt_tokens, completion_tokens, total_tokens)

    Returns:
        Tuple of (prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, pricing_source)
        All zeros if usage is None or missing fields.
    """
    if usage is None:
        return 0, 0, 0, 0.0, PricingSource.ZERO_TOKENS

    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or 0

    # If total_tokens is missing but we have the parts, compute it
    if total_tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens

    cost, source = estimate_cost(model, prompt_tokens, completion_tokens)
    return prompt_tokens, completion_tokens, total_tokens, cost, source


def is_free_model(model: str) -> bool:
    """Check if a model is known to be free/local."""
    if not model:
        return False
    return any(model.lower().startswith(p) for p in _FREE_PREFIXES)
