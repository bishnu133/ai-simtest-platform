"""
Run-Scoped Cost Tracker — P4 #27 (Review-Hardened)

Core tracking engine for LLM cost observability.

Review fixes applied:
  #1  — Global tracker uses contextvars.ContextVar (async/thread/server safe)
  #2  — Hard budget raises on ANY cost-bearing event, not just SUCCESS
  #3  — Finalized report is cached; repeated finalize() returns same object
  #4  — Tracks failed_calls_without_usage as first-class metric
  #5  — Component/subcomponent names normalized on input
  #6  — Supports configured_model / resolved_model fields
  #8  — Budget behavior documented with clear orchestration contracts
  #9  — Input validation: clamps negatives, enforces has_usage/status consistency
  #10 — Tracks pricing_lookup_failures and unknown_priced_models

Design principles:
  - RUN-SCOPED: Each tracker is tied to a run_id. No cost leakage between runs.
  - THREAD-SAFE: Uses threading.Lock for concurrent recording.
  - CONTEXT-LOCAL: Global accessor uses ContextVar, safe for async/server.
  - FINALIZABLE: tracker.finalize() produces a frozen CostReport snapshot.
  - CACHED: Repeated finalize() returns the same cached snapshot.
  - HONEST: Missing usage is tracked, never hidden as zero cost.
  - VALIDATED: Input is normalized and sanitized before accumulation.

Budget orchestration contract (Review #8):
  - SOFT mode: After budget exceeded, check_budget() returns True.
    Orchestrator should finish current conversation, then start no new ones.
  - HARD mode: Raises BudgetExceededError on the event that crosses the
    threshold, for ANY cost-bearing call (has_usage=True), not just SUCCESS.
    Orchestrator should catch and perform safe cleanup.
  - In both modes, already-recorded events are preserved for accurate reporting.

Usage:
    # Option 1: Context manager (recommended)
    with RunCostContext(run_id="abc") as tracker:
        tracker.record(model="gpt-4-turbo", prompt_tokens=100, ...)
        report = tracker.finalize(total_conversations=5, total_turns=25)

    # Option 2: Global accessor (CLI convenience, context-local)
    init_cost_tracker(run_id="abc")
    get_cost_tracker().record(...)
    report = get_cost_tracker().finalize(...)

Retry/failed-call accounting rules:
  - Track when usage metadata is present (regardless of success/failure)
  - Each provider attempt is counted separately
  - Failed calls WITH usage: counted as cost (provider processed tokens)
  - Failed calls WITHOUT usage: counted as calls_without_usage + failed_calls_without_usage
  - Retries: each attempt is a separate event
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from src.cost.models import (
    BudgetMode,
    CallStatus,
    ComponentCost,
    CostConfidence,
    CostEvent,
    CostReport,
    ModelCost,
    PricingSource,
    normalize_component_name,
    normalize_subcomponent_name,
)
from src.cost.pricing import estimate_cost

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised in hard budget mode when cost exceeds the limit."""
    def __init__(self, current_cost: float, budget_limit: float):
        self.current_cost = current_cost
        self.budget_limit = budget_limit
        super().__init__(
            f"Budget exceeded: estimated ${current_cost:.4f} > limit ${budget_limit:.2f}"
        )


class RunCostTracker:
    """
    Run-scoped cost tracker. One instance per simulation run.

    Accumulates CostEvents from all LLM calls, computes breakdowns,
    enforces budget limits, and produces a finalized CostReport.
    """

    def __init__(
        self,
        run_id: str = "",
        budget_limit: Optional[float] = None,
        budget_mode: BudgetMode = BudgetMode.SOFT,
    ):
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.budget_limit = budget_limit
        self.budget_mode = budget_mode

        # Accumulation state (protected by lock)
        self._lock = threading.Lock()
        self._events: list[CostEvent] = []
        self._total_cost: float = 0.0
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._total_tokens: int = 0
        self._total_calls: int = 0
        self._calls_with_usage: int = 0
        self._calls_without_usage: int = 0
        self._successful_calls: int = 0
        self._failed_calls_with_usage: int = 0
        self._failed_calls_without_usage: int = 0  # Review #4
        self._retried_calls: int = 0

        # Per-component accumulation
        self._component_data: dict[str, dict] = {}
        # Per-model accumulation
        self._model_data: dict[str, dict] = {}

        # Budget state
        self._budget_exceeded: bool = False
        self._budget_exceeded_at_cost: float = 0.0
        self._budget_exceeded_at_timestamp: float = 0.0
        self._budget_exceeded_event_index: int = -1

        # Finalization state — Review #3: cached report
        self._finalized: bool = False
        self._final_report: Optional[CostReport] = None

        # Review #10: Pricing observability
        self._pricing_lookup_failures: int = 0
        self._unknown_priced_models: set[str] = set()
        self._zero_cost_due_to_unknown: int = 0

    @property
    def is_finalized(self) -> bool:
        return self._finalized

    @property
    def total_estimated_cost(self) -> float:
        return self._total_cost

    @property
    def budget_exceeded(self) -> bool:
        return self._budget_exceeded

    def record(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        component: str = "other",
        subcomponent: str = "",
        provider: str = "",
        configured_model: str = "",
        resolved_model: str = "",
        status: str = CallStatus.SUCCESS.value,
        has_usage: bool = True,
        request_id: str = "",
        metadata: Optional[dict] = None,
    ) -> Optional[CostEvent]:
        """
        Record a single LLM call's usage.

        Called after every LLM response (success or failure).
        Thread-safe. Returns the created CostEvent, or None if the
        tracker is already finalized (event was ignored).

        Review #2: Hard budget raises on ANY cost-bearing event (has_usage=True).
        Review #5: Component/subcomponent names are normalized.
        Review #9: Input is validated and sanitized.

        Raises:
            BudgetExceededError: In HARD mode when budget limit is crossed
                                 by any cost-bearing call.
        """
        if self._finalized:
            logger.warning("cost_tracker_finalized", extra={"action": "record_ignored", "model": model})
            return None

        # ── Review #5: Normalize names ─────────────────────────
        component = normalize_component_name(component)
        subcomponent = normalize_subcomponent_name(subcomponent)

        # ── Review #9: Input validation ────────────────────────
        # Clamp negative token counts to zero
        prompt_tokens = max(0, prompt_tokens)
        completion_tokens = max(0, completion_tokens)
        total_tokens = max(0, total_tokens)

        # Enforce has_usage / status consistency
        if status == CallStatus.FAILED_NO_USAGE.value:
            has_usage = False

        # If has_usage=False, zero out tokens and cost
        if not has_usage:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0

        # Compute total tokens if not provided
        if total_tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
            total_tokens = prompt_tokens + completion_tokens

        # ── Review #10: Estimate cost with pricing source ──────
        estimated_cost = 0.0
        pricing_source = PricingSource.ZERO_TOKENS.value
        if has_usage and (prompt_tokens > 0 or completion_tokens > 0):
            cost_val, source = estimate_cost(model, prompt_tokens, completion_tokens)
            estimated_cost = cost_val
            pricing_source = source.value
        elif has_usage and model:
            # has_usage=True but zero tokens — mark pricing unknown
            pricing_source = PricingSource.ZERO_TOKENS.value
        elif not model and has_usage:
            pricing_source = PricingSource.NO_MODEL.value

        # Review #6: populate configured/resolved model
        if not configured_model:
            configured_model = model
        if not resolved_model:
            resolved_model = model

        event = CostEvent(
            timestamp=time.time(),
            run_id=self.run_id,
            model=model,
            configured_model=configured_model,
            resolved_model=resolved_model,
            provider=provider,
            component=component,
            subcomponent=subcomponent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost,
            status=status,
            has_usage=has_usage,
            pricing_source=pricing_source,
            request_id=request_id,
            metadata=metadata or {},
        )

        should_raise_budget = False
        with self._lock:
            self._events.append(event)
            self._total_calls += 1

            if has_usage:
                self._calls_with_usage += 1
                self._total_prompt_tokens += prompt_tokens
                self._total_completion_tokens += completion_tokens
                self._total_tokens += total_tokens
                self._total_cost += estimated_cost
            else:
                self._calls_without_usage += 1

            # Status tracking — Review #4: track failed_without_usage
            if status == CallStatus.SUCCESS.value:
                self._successful_calls += 1
            elif status == CallStatus.FAILED_WITH_USAGE.value:
                self._failed_calls_with_usage += 1
            elif status == CallStatus.FAILED_NO_USAGE.value:
                self._failed_calls_without_usage += 1
            elif status == CallStatus.RETRIED.value:
                self._retried_calls += 1

            # Review #10: Track pricing observability
            if pricing_source == PricingSource.UNKNOWN.value:
                self._pricing_lookup_failures += 1
                self._unknown_priced_models.add(model)
                self._zero_cost_due_to_unknown += 1

            # Component accumulation
            self._accumulate_component(component, subcomponent, event)
            # Model accumulation — keyed by (resolved_model, provider) for truth
            self._accumulate_model(
                resolved_model=event.resolved_model,
                configured_model=event.configured_model,
                provider=provider,
                event=event,
            )

            # Budget check
            if self.budget_limit is not None and not self._budget_exceeded:
                if self._total_cost >= self.budget_limit:
                    self._budget_exceeded = True
                    self._budget_exceeded_at_cost = self._total_cost
                    self._budget_exceeded_at_timestamp = event.timestamp
                    self._budget_exceeded_event_index = len(self._events) - 1
                    logger.warning(
                        "budget_exceeded",
                        extra={
                            "cost": f"${self._total_cost:.4f}",
                            "limit": f"${self.budget_limit:.2f}",
                            "mode": self.budget_mode.value if isinstance(self.budget_mode, BudgetMode) else self.budget_mode,
                        },
                    )

            # Review #2: Hard budget raises on ANY cost-bearing event
            if (self._budget_exceeded
                    and self.budget_mode == BudgetMode.HARD
                    and has_usage):
                should_raise_budget = True

        # Raise outside the lock to avoid deadlocks
        if should_raise_budget:
            raise BudgetExceededError(self._total_cost, self.budget_limit)

        return event

    def _accumulate_component(self, component: str, subcomponent: str, event: CostEvent) -> None:
        """Accumulate into per-component tracking. Must be called under lock."""
        if component not in self._component_data:
            self._component_data[component] = {
                "total_calls": 0, "calls_with_usage": 0, "calls_without_usage": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "estimated_cost_usd": 0.0, "subcomponents": {},
            }
        cd = self._component_data[component]
        cd["total_calls"] += 1
        if event.has_usage:
            cd["calls_with_usage"] += 1
            cd["prompt_tokens"] += event.prompt_tokens
            cd["completion_tokens"] += event.completion_tokens
            cd["total_tokens"] += event.total_tokens
            cd["estimated_cost_usd"] += event.estimated_cost_usd
        else:
            cd["calls_without_usage"] += 1

        # Subcomponent
        if subcomponent:
            if subcomponent not in cd["subcomponents"]:
                cd["subcomponents"][subcomponent] = {
                    "total_calls": 0, "calls_with_usage": 0, "calls_without_usage": 0,
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                }
            sc = cd["subcomponents"][subcomponent]
            sc["total_calls"] += 1
            if event.has_usage:
                sc["calls_with_usage"] += 1
                sc["prompt_tokens"] += event.prompt_tokens
                sc["completion_tokens"] += event.completion_tokens
                sc["total_tokens"] += event.total_tokens
                sc["estimated_cost_usd"] += event.estimated_cost_usd
            else:
                sc["calls_without_usage"] += 1

    def _accumulate_model(
        self,
        resolved_model: str,
        configured_model: str,
        provider: str,
        event: CostEvent,
    ) -> None:
        """
        Accumulate into per-model tracking. Must be called under lock.

        Aggregation key: (resolved_model, provider). This ensures the same
        model string served by different providers is tracked separately
        (e.g. "gpt-4" via Azure vs OpenAI). The configured_model is stored
        for reference but is not part of the key.
        """
        agg_key = f"{resolved_model}||{provider}" if provider else resolved_model
        if agg_key not in self._model_data:
            self._model_data[agg_key] = {
                "model": resolved_model,
                "configured_model": configured_model,
                "provider": provider,
                "total_calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            }
        md = self._model_data[agg_key]
        md["total_calls"] += 1
        if event.has_usage:
            md["prompt_tokens"] += event.prompt_tokens
            md["completion_tokens"] += event.completion_tokens
            md["total_tokens"] += event.total_tokens
            md["estimated_cost_usd"] += event.estimated_cost_usd

    def check_budget(self) -> bool:
        """
        Check if budget is exceeded. Returns True if over budget.

        Orchestration contract (Review #8):
          - Soft mode: Caller should finish the current conversation,
            then start no new conversations.
          - Hard mode: BudgetExceededError was already raised during record().
            This method is informational only.
        """
        return self._budget_exceeded

    def finalize(
        self,
        total_conversations: int = 0,
        total_turns: int = 0,
    ) -> CostReport:
        """
        Produce a frozen CostReport snapshot.

        Review #3: After first finalization, returns the cached report.
        No more events can be recorded after this call.
        All reporters (console, HTML, JSON) should use this snapshot.

        Args:
            total_conversations: Number of completed conversations (for per-conversation cost)
            total_turns: Number of judged turns (for per-turn cost)

        Returns:
            Frozen CostReport with all breakdowns computed.
        """
        with self._lock:
            # Review #3: Return cached report if already finalized
            if self._finalized and self._final_report is not None:
                return self._final_report

            self._finalized = True

            # Build per-component breakdown
            per_component_list: list[tuple[str, ComponentCost]] = []
            for comp_name, cd in self._component_data.items():
                # Build subcomponents as tuple
                subs = tuple(
                    (sc_name, ComponentCost(
                        component=sc_name,
                        total_calls=sc_data["total_calls"],
                        calls_with_usage=sc_data["calls_with_usage"],
                        calls_without_usage=sc_data["calls_without_usage"],
                        prompt_tokens=sc_data["prompt_tokens"],
                        completion_tokens=sc_data["completion_tokens"],
                        total_tokens=sc_data["total_tokens"],
                        estimated_cost_usd=sc_data["estimated_cost_usd"],
                    ))
                    for sc_name, sc_data in cd.get("subcomponents", {}).items()
                )
                cc = ComponentCost(
                    component=comp_name,
                    total_calls=cd["total_calls"],
                    calls_with_usage=cd["calls_with_usage"],
                    calls_without_usage=cd["calls_without_usage"],
                    prompt_tokens=cd["prompt_tokens"],
                    completion_tokens=cd["completion_tokens"],
                    total_tokens=cd["total_tokens"],
                    estimated_cost_usd=cd["estimated_cost_usd"],
                    subcomponents=subs,
                )
                per_component_list.append((comp_name, cc))

            # Build per-model breakdown
            per_model_list: list[tuple[str, ModelCost]] = []
            for agg_key, md in self._model_data.items():
                per_model_list.append((agg_key, ModelCost(
                    model=md["model"],
                    provider=md["provider"],
                    total_calls=md["total_calls"],
                    prompt_tokens=md["prompt_tokens"],
                    completion_tokens=md["completion_tokens"],
                    total_tokens=md["total_tokens"],
                    estimated_cost_usd=md["estimated_cost_usd"],
                )))

            # Deterministic ordering: sort by cost descending, then name ascending
            per_component_list.sort(key=lambda x: (-x[1].estimated_cost_usd, x[0]))
            per_model_list.sort(key=lambda x: (-x[1].estimated_cost_usd, x[0]))

            # Derived metrics
            cost_per_conversation = (
                self._total_cost / total_conversations if total_conversations > 0 else 0.0
            )
            cost_per_turn = (
                self._total_cost / total_turns if total_turns > 0 else 0.0
            )

            # Confidence — Review #7
            confidence = CostConfidence.from_ratio(
                self._calls_with_usage, self._total_calls
            )
            # Build confidence notes
            notes_parts = []
            if self._pricing_lookup_failures > 0:
                notes_parts.append(
                    f"{self._pricing_lookup_failures} pricing lookup(s) failed — "
                    f"cost defaulted to $0 for: {', '.join(sorted(self._unknown_priced_models))}"
                )
            if self._calls_without_usage > 0:
                notes_parts.append(
                    f"{self._calls_without_usage} call(s) returned no usage metadata"
                )
            confidence_notes = "; ".join(notes_parts)

            # Highest cost identifiers
            highest_comp = ""
            highest_comp_cost = 0.0
            for comp_name, cc in per_component_list:
                if cc.estimated_cost_usd > highest_comp_cost:
                    highest_comp_cost = cc.estimated_cost_usd
                    highest_comp = comp_name

            highest_model = ""
            highest_model_cost = 0.0
            for model_name, mc in per_model_list:
                if mc.estimated_cost_usd > highest_model_cost:
                    highest_model_cost = mc.estimated_cost_usd
                    highest_model = model_name

            report = CostReport(
                run_id=self.run_id,
                total_prompt_tokens=self._total_prompt_tokens,
                total_completion_tokens=self._total_completion_tokens,
                total_tokens=self._total_tokens,
                total_estimated_cost_usd=self._total_cost,
                total_calls=self._total_calls,
                calls_with_usage=self._calls_with_usage,
                calls_without_usage=self._calls_without_usage,
                usage_coverage_confidence=confidence.value,
                confidence_notes=confidence_notes,
                successful_calls=self._successful_calls,
                failed_calls_with_usage=self._failed_calls_with_usage,
                failed_calls_without_usage=self._failed_calls_without_usage,
                retried_calls=self._retried_calls,
                per_component=tuple(per_component_list),
                per_model=tuple(per_model_list),
                cost_per_completed_conversation=cost_per_conversation,
                cost_per_turn=cost_per_turn,
                total_conversations=total_conversations,
                total_turns=total_turns,
                budget_limit=self.budget_limit,
                budget_mode=(self.budget_mode.value
                             if isinstance(self.budget_mode, BudgetMode)
                             else self.budget_mode),
                budget_exceeded=self._budget_exceeded,
                budget_exceeded_at_cost=self._budget_exceeded_at_cost,
                budget_exceeded_at_timestamp=self._budget_exceeded_at_timestamp,
                budget_exceeded_event_index=self._budget_exceeded_event_index,
                highest_cost_component=highest_comp,
                highest_cost_model=highest_model,
                pricing_lookup_failures=self._pricing_lookup_failures,
                unknown_priced_models=tuple(sorted(self._unknown_priced_models)),
                zero_cost_due_to_unknown_pricing=self._zero_cost_due_to_unknown,
            )

            # Cache the report — Review #3
            self._final_report = report
            return report

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def events(self) -> list[CostEvent]:
        """Access recorded events (read-only copy)."""
        return list(self._events)


# ── Global accessor (context-local) — Review #1 ──────────────────────────────

# ContextVar replaces module-global _current_tracker for async/server safety
_cost_tracker_var: contextvars.ContextVar[Optional[RunCostTracker]] = contextvars.ContextVar(
    "cost_tracker", default=None
)
_tracker_lock = threading.Lock()


def init_cost_tracker(
    run_id: str = "",
    budget_limit: Optional[float] = None,
    budget_mode: BudgetMode = BudgetMode.SOFT,
) -> RunCostTracker:
    """
    Initialize the cost tracker for the current context.

    Uses contextvars.ContextVar (Review #1) so each async task / thread
    can have its own tracker without interfering with others.
    """
    tracker = RunCostTracker(
        run_id=run_id,
        budget_limit=budget_limit,
        budget_mode=budget_mode,
    )
    _cost_tracker_var.set(tracker)
    return tracker


def get_cost_tracker() -> Optional[RunCostTracker]:
    """Get the current context's tracker. Returns None if not initialized."""
    return _cost_tracker_var.get(None)


def reset_cost_tracker() -> None:
    """Reset the current context's tracker. Safe to call multiple times."""
    _cost_tracker_var.set(None)


@contextmanager
def RunCostContext(
    run_id: str = "",
    budget_limit: Optional[float] = None,
    budget_mode: BudgetMode = BudgetMode.SOFT,
):
    """
    Context manager for run-scoped cost tracking.

    Usage:
        with RunCostContext(run_id="run123") as tracker:
            tracker.record(...)
            report = tracker.finalize()

    Supports nesting: restores the previous ContextVar value on exit
    using the token returned by ContextVar.set(), rather than always
    resetting to None. This means an outer RunCostContext's tracker
    is correctly restored when an inner context exits.
    """
    tracker = RunCostTracker(
        run_id=run_id,
        budget_limit=budget_limit,
        budget_mode=budget_mode,
    )
    token = _cost_tracker_var.set(tracker)
    try:
        yield tracker
    finally:
        _cost_tracker_var.reset(token)
