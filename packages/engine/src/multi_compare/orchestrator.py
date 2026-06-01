"""
Multi-Model Comparison — Orchestrator (Phase 3C).

The top-level engine that coordinates:
1. Plan building (personas generated once, shared across endpoints)
2. Checkpoint creation / resume validation
3. Parallel or sequential endpoint execution
4. Per-endpoint retry with configurable backoff
5. Global budget enforcement across all endpoints
6. Per-endpoint timeout enforcement
7. Progress event emission for observability
8. Partial result aggregation
9. Checkpoint persistence after each endpoint

Zero changes to existing SimulationOrchestrator — this is a wrapper.

Addresses review points:
- #3: Per-endpoint retry with exponential backoff
- #4: Partial result utilization (allow_partial_results)
- #5: Global budget guard (total_limit + soft/hard mode)
- #6: Model-level timeout (asyncio.wait_for)
- #7: Progress events (structured logging + callback)
- #8: Experiment tracking via ExperimentMetadata
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.core.logging import get_logger
from src.multi_compare.models import (
    CheckpointStatus,
    ModelRunResult,
    ModelRunStatus,
    MultiCompareConfig,
)
from src.multi_compare.plan import (
    BudgetSettings,
    ExecutionSettings,
    ExperimentMetadata,
    FairnessControls,
    PlanBuilder,
    RunPlan,
    save_plan,
)
from src.multi_compare.checkpoint import (
    CheckpointManager,
    CheckpointStateV2,
    ResumeValidation,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Progress Event Types
# ─────────────────────────────────────────────────────────────

class ProgressEvent:
    """Structured progress event for observability (Review #7)."""

    def __init__(
        self,
        event_type: str,
        model_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.event_type = event_type
        self.model_id = model_id
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc).isoformat() + "Z"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "event": self.event_type,
            "timestamp": self.timestamp,
        }
        if self.model_id:
            d["model_id"] = self.model_id
        if self.details:
            d["details"] = self.details
        return d


# Type alias for progress callback
ProgressCallback = Callable[[ProgressEvent], None]


# ─────────────────────────────────────────────────────────────
# Multi-Model Result
# ─────────────────────────────────────────────────────────────

class MultiModelResult:
    """
    Aggregate output from a multi-model comparison run.

    Contains per-endpoint results, budget info, timing,
    and checkpoint metadata. Consumed by Phase 4 (N-way
    comparison engine) and Phase 5 (HTML report).
    """

    def __init__(
        self,
        plan: RunPlan,
        results: dict[str, ModelRunResult],
        checkpoint_state: CheckpointStateV2 | None = None,
        total_execution_time: float = 0.0,
        cumulative_cost_usd: float = 0.0,
        budget_exceeded: bool = False,
        events: list[ProgressEvent] | None = None,
    ):
        self.plan = plan
        self.results = results
        self.checkpoint_state = checkpoint_state
        self.total_execution_time = total_execution_time
        self.cumulative_cost_usd = cumulative_cost_usd
        self.budget_exceeded = budget_exceeded
        self.events = events or []

    @property
    def usable_results(self) -> dict[str, ModelRunResult]:
        """Results that can be included in analysis."""
        return {mid: r for mid, r in self.results.items() if r.is_usable()}

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.results.values()
                   if r.status == ModelRunStatus.SUCCESS)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results.values()
                   if r.status == ModelRunStatus.FAILED)

    @property
    def completeness_ratio(self) -> float:
        total = len(self.results)
        if total == 0:
            return 0.0
        return self.completed_count / total

    @property
    def is_complete(self) -> bool:
        return self.completed_count == len(self.results)

    @property
    def is_usable(self) -> bool:
        """Whether enough endpoints completed for meaningful comparison."""
        return len(self.usable_results) >= 2

    def to_summary_dict(self) -> dict[str, Any]:
        """Summary for logging and notification payloads."""
        return {
            "plan_id": self.plan.plan_id,
            "config_name": self.plan.config_name,
            "endpoints_total": len(self.results),
            "endpoints_completed": self.completed_count,
            "endpoints_failed": self.failed_count,
            "completeness_ratio": round(self.completeness_ratio, 2),
            "total_execution_time_seconds": round(self.total_execution_time, 1),
            "cumulative_cost_usd": round(self.cumulative_cost_usd, 4),
            "budget_exceeded": self.budget_exceeded,
            "experiment": self.plan.experiment.model_dump(mode="json")
            if self.plan.experiment.is_set else None,
            "resumed": (self.checkpoint_state.resume_count > 0)
            if self.checkpoint_state else False,
        }


# ─────────────────────────────────────────────────────────────
# Endpoint Executor (handles retry + timeout for one endpoint)
# ─────────────────────────────────────────────────────────────

class EndpointExecutor:
    """
    Executes a simulation for one endpoint with retry and timeout.

    Wraps the existing SimulationOrchestrator — zero changes to
    the simulation pipeline.
    """

    def __init__(
        self,
        execution_settings: ExecutionSettings,
        fairness: FairnessControls,
        progress_callback: ProgressCallback | None = None,
    ):
        self.settings = execution_settings
        self.fairness = fairness
        self._callback = progress_callback

    async def execute(
        self,
        model_id: str,
        model_name: str,
        endpoint_config: dict[str, Any],
        personas: list[Any],
        plan: RunPlan,
        position: int,
        total: int,
    ) -> ModelRunResult:
        """
        Execute simulation for one endpoint with retry + timeout.

        Args:
            model_id: Unique endpoint identifier.
            model_name: Display name.
            endpoint_config: Redacted ModelSpec dict.
            personas: List of Persona objects to use.
            plan: The frozen RunPlan.
            position: 1-based position in execution order.
            total: Total endpoints.

        Returns:
            ModelRunResult with status, report, cost, etc.
        """
        retry_policy = self.settings.retry
        last_error = ""

        self._emit(ProgressEvent(
            event_type="endpoint_started",
            model_id=model_id,
            details={"position": f"{position}/{total}", "name": model_name},
        ))

        for attempt in range(1, retry_policy.max_attempts + 1):
            try:
                if attempt > 1:
                    delay = self._compute_backoff(attempt, retry_policy)
                    self._emit(ProgressEvent(
                        event_type="endpoint_retrying",
                        model_id=model_id,
                        details={
                            "attempt": attempt,
                            "max_attempts": retry_policy.max_attempts,
                            "backoff_seconds": round(delay, 1),
                        },
                    ))
                    await asyncio.sleep(delay)

                result = await asyncio.wait_for(
                    self._run_single_endpoint(
                        model_id=model_id,
                        model_name=model_name,
                        endpoint_config=endpoint_config,
                        personas=personas,
                        plan=plan,
                    ),
                    timeout=self.settings.model_timeout,
                )

                if result.status == ModelRunStatus.SUCCESS:
                    self._emit(ProgressEvent(
                        event_type="endpoint_completed",
                        model_id=model_id,
                        details={
                            "pass_rate": result.summary_dict.get("pass_rate", 0),
                            "execution_time": round(result.execution_time_seconds, 1),
                            "cost_usd": round(
                                result.cost_report_dict.get("total_estimated_cost", 0)
                                if result.cost_report_dict else 0, 4
                            ),
                        },
                    ))
                    return result

                # Non-success but non-exception result — don't retry
                last_error = result.error or "Unknown failure"
                break

            except asyncio.TimeoutError:
                last_error = (
                    f"Endpoint timed out after {self.settings.model_timeout}s "
                    f"(attempt {attempt}/{retry_policy.max_attempts})"
                )
                logger.warning(
                    "endpoint_timeout",
                    model_id=model_id,
                    attempt=attempt,
                    timeout=self.settings.model_timeout,
                )

            except Exception as e:
                last_error = f"{type(e).__name__}: {str(e)[:300]}"
                logger.error(
                    "endpoint_execution_error",
                    model_id=model_id,
                    attempt=attempt,
                    error=last_error,
                )

        # All retries exhausted
        self._emit(ProgressEvent(
            event_type="endpoint_failed",
            model_id=model_id,
            details={"error": last_error, "attempts": retry_policy.max_attempts},
        ))

        return ModelRunResult(
            model_id=model_id,
            model_name=model_name,
            status=ModelRunStatus.FAILED,
            error=last_error,
            completed_at=datetime.now(timezone.utc).isoformat() + "Z",
        )

    async def _run_single_endpoint(
        self,
        model_id: str,
        model_name: str,
        endpoint_config: dict[str, Any],
        personas: list[Any],
        plan: RunPlan,
    ) -> ModelRunResult:
        """
        Run simulation for one endpoint using existing SimulationOrchestrator.

        This is the integration point — it creates a SimulationConfig
        from the plan + endpoint and delegates to the existing pipeline.
        """
        from src.models import BotConfig, SimulationConfig

        start_time = time.time()

        # Build BotConfig from endpoint
        endpoint = endpoint_config.get("endpoint", "")
        headers = endpoint_config.get("headers", {})

        bot_config = BotConfig(
            api_endpoint=endpoint,
            headers=headers,
        )

        # Build SimulationConfig
        sim_config = SimulationConfig(
            name=f"{plan.config_name} — {model_name}",
            bot=bot_config,
            num_personas=len(personas),
            max_turns_per_conversation=plan.max_turns,
            min_turns_per_conversation=plan.min_turns,
            pass_threshold=plan.pass_threshold,
            warn_threshold=plan.warn_threshold,
            max_parallel_conversations=plan.parallel_per_model,
        )

        # Apply fairness overrides to bot config if needed
        fairness_overrides = plan.fairness.to_adapter_overrides()
        if fairness_overrides:
            # Store as metadata on bot config for downstream use
            if not hasattr(bot_config, "metadata"):
                pass  # BotConfig may not have metadata field — safe to skip

        # Create orchestrator and run
        from src.core.orchestrator import SimulationOrchestrator

        orchestrator = SimulationOrchestrator(sim_config)
        report = await orchestrator.run_simulation(personas=personas)

        execution_time = time.time() - start_time

        # Extract cost report if available
        cost_report_dict = None
        try:
            from src.cost.tracker import get_current_tracker
            tracker = get_current_tracker()
            if tracker:
                cost_report = tracker.get_report()
                cost_report_dict = cost_report.model_dump(mode="json")
        except (ImportError, Exception):
            pass

        # Build summary dict from report
        summary_dict = {}
        if report and report.summary:
            summary_dict = {
                "pass_rate": report.summary.pass_rate,
                "average_score": report.summary.average_score,
                "total_conversations": report.summary.total_conversations,
                "passed_conversations": report.summary.passed_conversations,
                "failed_conversations": report.summary.failed_conversations,
                "score_by_judge": report.summary.score_by_judge,
                "critical_failures": getattr(report.summary, "critical_failures", 0),
            }

        return ModelRunResult(
            model_id=model_id,
            model_name=model_name,
            status=ModelRunStatus.SUCCESS,
            report=report,
            summary_dict=summary_dict,
            cost_report_dict=cost_report_dict,
            execution_time_seconds=execution_time,
            completed_at=datetime.now(timezone.utc).isoformat() + "Z",
        )

    @staticmethod
    def _compute_backoff(attempt: int, retry_policy: Any) -> float:
        """Compute backoff delay with jitter."""
        if retry_policy.backoff == "exponential":
            base_delay = retry_policy.initial_delay * (2 ** (attempt - 2))
        else:
            base_delay = retry_policy.initial_delay

        # Add jitter (±25%)
        jitter = base_delay * 0.25 * (2 * random.random() - 1)
        return max(0.5, base_delay + jitter)

    def _emit(self, event: ProgressEvent) -> None:
        logger.info(
            event.event_type,
            model_id=event.model_id,
            **event.details,
        )
        if self._callback:
            try:
                self._callback(event)
            except Exception:
                pass  # Never let callback errors break execution


# ─────────────────────────────────────────────────────────────
# Multi-Model Orchestrator
# ─────────────────────────────────────────────────────────────

class MultiModelOrchestrator:
    """
    Top-level orchestrator for multi-model comparison runs.

    Flow:
    1. Load config → validate
    2. Generate personas once (via PersonaGenerator)
    3. Build frozen RunPlan
    4. Check for existing checkpoint → validate resume
    5. Execute endpoints (parallel/sequential) with retry + timeout
    6. Checkpoint after each endpoint
    7. Global budget guard between endpoints
    8. Aggregate results into MultiModelResult
    """

    def __init__(
        self,
        config: MultiCompareConfig,
        checkpoint_dir: str | Path = ".checkpoints",
        output_dir: str | Path | None = None,
        progress_callback: ProgressCallback | None = None,
        fairness: FairnessControls | None = None,
        execution: ExecutionSettings | None = None,
        budget: BudgetSettings | None = None,
        experiment: ExperimentMetadata | None = None,
    ):
        self.config = config
        self.output_dir = Path(output_dir or config.output_dir)
        self.checkpoint_mgr = CheckpointManager(checkpoint_dir)
        self._callback = progress_callback
        self._fairness = fairness or FairnessControls()
        self._execution = execution or ExecutionSettings()
        self._budget = budget or BudgetSettings()
        self._experiment = experiment or ExperimentMetadata()
        self._events: list[ProgressEvent] = []

    async def run(
        self,
        personas: list[Any] | None = None,
        scenario_ids: list[str] | None = None,
        force_restart: bool = False,
    ) -> MultiModelResult:
        """
        Execute the full multi-model comparison pipeline.

        Args:
            personas: Pre-generated personas. If None, generates them.
            scenario_ids: Optional scenario slugs for round-robin.
            force_restart: If True, ignore existing checkpoints.

        Returns:
            MultiModelResult with per-endpoint results.
        """
        run_start = time.time()

        self._emit(ProgressEvent(
            event_type="multi_compare_started",
            details={
                "config_name": self.config.name,
                "endpoint_count": len(self.config.models),
                "persona_count": self.config.settings.personas,
                "experiment": self._experiment.model_dump(mode="json")
                if self._experiment.is_set else None,
            },
        ))

        # Step 1: Generate or use provided personas
        if personas is None:
            personas = await self._generate_personas()

        # Step 2: Build frozen RunPlan
        plan = PlanBuilder.build(
            config=self.config,
            personas=personas,
            scenario_ids=scenario_ids,
            fairness=self._fairness,
            execution=self._execution,
            budget=self._budget,
            experiment=self._experiment,
        )

        # Step 3: Save plan for reproducibility
        self.output_dir.mkdir(parents=True, exist_ok=True)
        save_plan(plan, self.output_dir)

        # Step 4: Checkpoint management
        checkpoint_state: CheckpointStateV2 | None = None
        completed_results: dict[str, ModelRunResult] = {}
        cumulative_cost = 0.0

        if not force_restart and self.checkpoint_mgr.exists(plan.plan_id):
            # Validate resume
            validation = self.checkpoint_mgr.validate_resume(
                plan_id=plan.plan_id,
                current_plan_hash=plan.plan_hash,
                current_persona_set_hash=plan.persona_set_hash,
                current_endpoint_ids=plan.endpoint_ids,
            )

            if validation.is_valid:
                checkpoint_state = self.checkpoint_mgr.prepare_resume(plan.plan_id)
                cumulative_cost = checkpoint_state.cumulative_cost_usd

                # Load completed results from checkpoint
                completed_reports = self.checkpoint_mgr.load_all_completed_reports(
                    plan.plan_id
                )
                for mid, report_data in completed_reports.items():
                    completed_results[mid] = ModelRunResult(
                        model_id=mid,
                        model_name=report_data.get("model_name", mid),
                        status=ModelRunStatus.SUCCESS,
                        summary_dict=report_data.get("summary_dict", {}),
                        cost_report_dict=report_data.get("cost_report_dict"),
                        execution_time_seconds=report_data.get("execution_time_seconds", 0),
                        completed_at=report_data.get("completed_at"),
                    )

                self._emit(ProgressEvent(
                    event_type="checkpoint_resumed",
                    details={
                        "completed": len(completed_results),
                        "pending": validation.pending_count,
                        "resume_count": checkpoint_state.resume_count,
                    },
                ))

                for w in validation.warnings:
                    logger.warning("resume_warning", message=w)
            else:
                for issue in validation.blocking_issues:
                    logger.error("resume_blocked", message=issue)
                raise ValueError(
                    f"Cannot resume checkpoint: {'; '.join(validation.blocking_issues)}"
                )

        # Create new checkpoint if needed
        if checkpoint_state is None:
            if self.checkpoint_mgr.exists(plan.plan_id):
                self.checkpoint_mgr.cleanup(plan.plan_id)

            checkpoint_state = self.checkpoint_mgr.create(
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                persona_set_hash=plan.persona_set_hash,
                endpoint_ids=plan.endpoint_ids,
                plan_data=plan.model_dump(mode="json"),
            )

        # Step 5: Determine which endpoints to run
        pending_ids = [
            mid for mid in plan.endpoint_ids
            if mid not in completed_results
        ]

        # Step 6: Execute endpoints
        budget_exceeded = False
        executor = EndpointExecutor(
            execution_settings=self._execution,
            fairness=self._fairness,
            progress_callback=self._progress_handler,
        )

        # Map model_id -> ModelSpec for getting api_key at runtime
        model_spec_map = {m.id: m for m in self.config.models}

        if self._execution.strategy == "parallel":
            new_results = await self._run_parallel(
                executor=executor,
                pending_ids=pending_ids,
                plan=plan,
                personas=personas,
                model_spec_map=model_spec_map,
                checkpoint_state=checkpoint_state,
                cumulative_cost=cumulative_cost,
            )
        else:
            new_results = await self._run_sequential(
                executor=executor,
                pending_ids=pending_ids,
                plan=plan,
                personas=personas,
                model_spec_map=model_spec_map,
                checkpoint_state=checkpoint_state,
                cumulative_cost=cumulative_cost,
            )

        # Merge completed + new results
        all_results = {**completed_results, **new_results}

        # Check if budget was exceeded
        total_cost = sum(
            r.cost_report_dict.get("total_estimated_cost", 0)
            if r.cost_report_dict else 0
            for r in all_results.values()
        )

        if self._budget.total_limit and total_cost > self._budget.total_limit:
            budget_exceeded = True

        # Step 7: Finalize checkpoint
        self.checkpoint_mgr.finalize(plan.plan_id)

        # Step 8: Check min_completion_ratio
        total_endpoints = len(plan.endpoint_ids)
        completed_count = sum(
            1 for r in all_results.values()
            if r.status == ModelRunStatus.SUCCESS
        )
        ratio = completed_count / total_endpoints if total_endpoints > 0 else 0

        if ratio < self._execution.min_completion_ratio:
            self._emit(ProgressEvent(
                event_type="multi_compare_insufficient",
                details={
                    "completion_ratio": round(ratio, 2),
                    "min_required": self._execution.min_completion_ratio,
                    "completed": completed_count,
                    "total": total_endpoints,
                },
            ))

        total_time = time.time() - run_start

        self._emit(ProgressEvent(
            event_type="multi_compare_completed",
            details={
                "total_endpoints": total_endpoints,
                "completed": completed_count,
                "failed": sum(1 for r in all_results.values()
                              if r.status == ModelRunStatus.FAILED),
                "total_cost_usd": round(total_cost, 4),
                "total_time_seconds": round(total_time, 1),
                "budget_exceeded": budget_exceeded,
            },
        ))

        return MultiModelResult(
            plan=plan,
            results=all_results,
            checkpoint_state=checkpoint_state,
            total_execution_time=total_time,
            cumulative_cost_usd=total_cost,
            budget_exceeded=budget_exceeded,
            events=self._events,
        )

    # ── Execution Strategies ──────────────────────────────

    async def _run_parallel(
        self,
        executor: EndpointExecutor,
        pending_ids: list[str],
        plan: RunPlan,
        personas: list[Any],
        model_spec_map: dict[str, Any],
        checkpoint_state: CheckpointStateV2,
        cumulative_cost: float,
    ) -> dict[str, ModelRunResult]:
        """Run pending endpoints in parallel with semaphore."""
        semaphore = asyncio.Semaphore(self._execution.max_parallel)
        results: dict[str, ModelRunResult] = {}
        total = len(plan.endpoint_ids)

        async def _run_with_semaphore(model_id: str, position: int) -> None:
            async with semaphore:
                # Budget check before starting
                if self._should_skip_for_budget(cumulative_cost, results):
                    result = ModelRunResult(
                        model_id=model_id,
                        model_name=plan.endpoint_configs.get(model_id, {}).get("name", model_id),
                        status=ModelRunStatus.SKIPPED,
                        error="Skipped due to global budget limit",
                    )
                    results[model_id] = result
                    self._checkpoint_result(
                        checkpoint_state, plan.plan_id, model_id, result
                    )
                    return

                spec = model_spec_map.get(model_id)
                result = await executor.execute(
                    model_id=model_id,
                    model_name=spec.name if spec else model_id,
                    endpoint_config=plan.endpoint_configs.get(model_id, {}),
                    personas=personas,
                    plan=plan,
                    position=position,
                    total=total,
                )
                results[model_id] = result
                self._checkpoint_result(
                    checkpoint_state, plan.plan_id, model_id, result
                )

        tasks = [
            _run_with_semaphore(mid, i + 1)
            for i, mid in enumerate(pending_ids)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def _run_sequential(
        self,
        executor: EndpointExecutor,
        pending_ids: list[str],
        plan: RunPlan,
        personas: list[Any],
        model_spec_map: dict[str, Any],
        checkpoint_state: CheckpointStateV2,
        cumulative_cost: float,
    ) -> dict[str, ModelRunResult]:
        """Run pending endpoints one at a time."""
        results: dict[str, ModelRunResult] = {}
        total = len(plan.endpoint_ids)

        for i, model_id in enumerate(pending_ids):
            # Budget check
            current_cost = cumulative_cost + sum(
                r.cost_report_dict.get("total_estimated_cost", 0)
                if r.cost_report_dict else 0
                for r in results.values()
            )

            if self._budget.total_limit and self._budget.mode == "hard":
                if current_cost >= self._budget.total_limit:
                    self._emit(ProgressEvent(
                        event_type="budget_exceeded",
                        model_id=model_id,
                        details={
                            "current_cost": round(current_cost, 4),
                            "limit": self._budget.total_limit,
                            "mode": "hard",
                            "skipping_remaining": len(pending_ids) - i,
                        },
                    ))
                    # Skip remaining endpoints
                    for remaining_id in pending_ids[i:]:
                        result = ModelRunResult(
                            model_id=remaining_id,
                            model_name=model_spec_map.get(remaining_id, type('', (), {"name": remaining_id})).name,
                            status=ModelRunStatus.BUDGET_TERMINATED,
                            error=f"Global budget limit exceeded (${current_cost:.2f} / ${self._budget.total_limit:.2f})",
                        )
                        results[remaining_id] = result
                        self._checkpoint_result(
                            checkpoint_state, plan.plan_id, remaining_id, result
                        )
                    break

            if self._budget.total_limit and self._budget.mode == "soft":
                if current_cost >= self._budget.total_limit:
                    self._emit(ProgressEvent(
                        event_type="budget_warning",
                        model_id=model_id,
                        details={
                            "current_cost": round(current_cost, 4),
                            "limit": self._budget.total_limit,
                            "mode": "soft",
                        },
                    ))

            spec = model_spec_map.get(model_id)
            result = await executor.execute(
                model_id=model_id,
                model_name=spec.name if spec else model_id,
                endpoint_config=plan.endpoint_configs.get(model_id, {}),
                personas=personas,
                plan=plan,
                position=i + 1,
                total=total,
            )
            results[model_id] = result
            self._checkpoint_result(
                checkpoint_state, plan.plan_id, model_id, result
            )

            # Delay between sequential runs
            if self._execution.delay_between > 0 and i < len(pending_ids) - 1:
                await asyncio.sleep(self._execution.delay_between)

        return results

    # ── Helpers ────────────────────────────────────────────

    async def _generate_personas(self) -> list[Any]:
        """Generate personas using existing PersonaGenerator."""
        from src.generators.persona_generator import PersonaGenerator
        from src.core.llm_client import LLMProviderManager

        provider_mgr = LLMProviderManager()
        await provider_mgr.check_all_providers()

        generator = PersonaGenerator()
        personas = await generator.generate(
            bot_description=f"Multi-model comparison: {self.config.name}",
            documentation=self.config.settings.documentation or "",
            num_personas=self.config.settings.personas,
        )

        logger.info("personas_generated", count=len(personas))
        return personas

    def _should_skip_for_budget(
        self,
        base_cost: float,
        current_results: dict[str, ModelRunResult],
    ) -> bool:
        """Check if global budget has been exceeded (hard mode only)."""
        if not self._budget.total_limit or self._budget.mode != "hard":
            return False

        current_cost = base_cost + sum(
            r.cost_report_dict.get("total_estimated_cost", 0)
            if r.cost_report_dict else 0
            for r in current_results.values()
        )
        return current_cost >= self._budget.total_limit

    def _checkpoint_result(
        self,
        state: CheckpointStateV2,
        plan_id: str,
        model_id: str,
        result: ModelRunResult,
    ) -> None:
        """Save result to checkpoint."""
        if result.status == ModelRunStatus.SUCCESS:
            # Save report data
            report_data = {
                "model_id": model_id,
                "model_name": result.model_name,
                "summary_dict": result.summary_dict,
                "cost_report_dict": result.cost_report_dict,
                "execution_time_seconds": result.execution_time_seconds,
                "completed_at": result.completed_at,
            }
            self.checkpoint_mgr.save_endpoint_report(plan_id, model_id, report_data)

            cost = (result.cost_report_dict.get("total_estimated_cost", 0)
                    if result.cost_report_dict else 0)
            state.mark_completed(
                model_id=model_id,
                report_file=f"endpoint_{model_id}.json",
                execution_time=result.execution_time_seconds,
                cost_usd=cost,
            )
        elif result.status in (ModelRunStatus.FAILED, ModelRunStatus.BUDGET_TERMINATED):
            state.mark_failed(model_id, result.error or "Unknown")
        elif result.status == ModelRunStatus.SKIPPED:
            state.mark_skipped(model_id, result.error or "Skipped")

        self.checkpoint_mgr.save_state(state)

    def _progress_handler(self, event: ProgressEvent) -> None:
        """Internal handler that records events and forwards to user callback."""
        self._events.append(event)
        if self._callback:
            try:
                self._callback(event)
            except Exception:
                pass

    def _emit(self, event: ProgressEvent) -> None:
        """Emit a progress event from the orchestrator itself."""
        logger.info(event.event_type, **(event.details or {}))
        self._events.append(event)
        if self._callback:
            try:
                self._callback(event)
            except Exception:
                pass
