"""
Tests for Multi-Model Comparison — Phase 3 (Orchestrator + Checkpoint).

Covers:
- Sub-Phase 3A: RunPlan, PlanBuilder, FairnessControls, ExperimentMetadata
- Sub-Phase 3B: CheckpointManager, resume validation, config drift
- Sub-Phase 3C: EndpointExecutor, MultiModelOrchestrator, budget guards

Test categories match review points:
- Review #1: Plan immutability + plan_hash
- Review #2: Checkpoint resume validation (config drift)
- Review #3: Retry + timeout orchestration
- Review #4: Partial result utilization
- Review #5: Global budget control
- Review #6: Model-level timeout
- Review #7: Progress events / observability
- Review #8: Experiment tracking
- Review #10: Fairness controls
- Medium #1: Plan export / reproducibility
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.multi_compare.plan import (
    FairnessControls,
    ExperimentMetadata,
    RetryPolicy,
    ExecutionSettings,
    BudgetSettings,
    SerializedPersona,
    RunPlan,
    PlanBuilder,
    save_plan,
    load_plan,
)
from src.multi_compare.checkpoint import (
    CheckpointEntry,
    CheckpointStateV2,
    CheckpointManager,
    ResumeValidation,
)
from src.multi_compare.orchestrator import (
    ProgressEvent,
    MultiModelResult,
    EndpointExecutor,
    MultiModelOrchestrator,
)
from src.multi_compare.models import (
    AdapterType,
    ComparisonMode,
    ModelSpec,
    ComparisonSettings,
    MultiCompareConfig,
    ModelRunResult,
    ModelRunStatus,
    CheckpointStatus,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

def _make_model_spec(id: str, name: str = "", endpoint: str = "https://api.example.com/v1") -> ModelSpec:
    return ModelSpec(
        id=id,
        name=name or id,
        endpoint=endpoint,
        adapter=AdapterType.OPENAI,
        tags=["test"],
    )


def _make_config(
    models: list[ModelSpec] | None = None,
    champion: str = "model-a",
) -> MultiCompareConfig:
    if models is None:
        models = [
            _make_model_spec("model-a", "Model A"),
            _make_model_spec("model-b", "Model B"),
        ]
    return MultiCompareConfig(
        name="Test Comparison",
        mode=ComparisonMode.CHAMPION_CHALLENGER,
        champion=champion,
        models=models,
        settings=ComparisonSettings(personas=5, max_turns=6),
    )


class MockPersona:
    """Lightweight mock of Persona for testing."""
    def __init__(self, id: str, name: str = "", persona_type: str = "standard"):
        self.id = id
        self.name = name or f"Persona {id}"
        self.persona_type = type("PT", (), {"value": persona_type})()
        self.role = "user"
        self.technical_level = type("TL", (), {"value": "intermediate"})()
        self.goals = ["Test the bot"]
        self.tone = "neutral"


def _make_personas(count: int = 5) -> list[MockPersona]:
    return [MockPersona(f"p{i+1:03d}", f"Persona {i+1}") for i in range(count)]


# ═══════════════════════════════════════════════════════════════
# SECTION 1: Plan Models (Review #1, #10, #8, Medium #1)
# ═══════════════════════════════════════════════════════════════


class TestFairnessControls:
    """Review #10: Temperature, seed, deterministic controls."""

    def test_defaults(self):
        fc = FairnessControls()
        assert fc.deterministic is False
        assert fc.temperature is None
        assert fc.seed is None

    def test_frozen(self):
        fc = FairnessControls(deterministic=True)
        with pytest.raises(Exception):
            fc.deterministic = False

    def test_adapter_overrides_deterministic(self):
        fc = FairnessControls(deterministic=True)
        overrides = fc.to_adapter_overrides()
        assert overrides["temperature"] == 0.0
        assert overrides["seed"] == 42

    def test_adapter_overrides_explicit(self):
        fc = FairnessControls(temperature=0.5, seed=123)
        overrides = fc.to_adapter_overrides()
        assert overrides["temperature"] == 0.5
        assert overrides["seed"] == 123

    def test_adapter_overrides_empty_when_defaults(self):
        fc = FairnessControls()
        overrides = fc.to_adapter_overrides()
        assert overrides == {}


class TestExperimentMetadata:
    """Review #8: Experiment tracking."""

    def test_defaults(self):
        em = ExperimentMetadata()
        assert em.is_set is False

    def test_with_values(self):
        em = ExperimentMetadata(
            name="gpt4_vs_claude",
            version="v1.2",
            tags={"team": "ml"},
        )
        assert em.is_set is True
        assert em.name == "gpt4_vs_claude"

    def test_frozen(self):
        em = ExperimentMetadata(name="test")
        with pytest.raises(Exception):
            em.name = "changed"


class TestExecutionSettings:
    """Review #3 and #6: Retry policy and model timeout."""

    def test_defaults(self):
        es = ExecutionSettings()
        assert es.strategy == "parallel"
        assert es.max_parallel == 3
        assert es.retry.max_attempts == 3
        assert es.retry.backoff == "exponential"
        assert es.model_timeout == 600
        assert es.allow_partial_results is True
        assert es.min_completion_ratio == 0.5

    def test_sequential(self):
        es = ExecutionSettings(strategy="sequential", delay_between=5.0)
        assert es.strategy == "sequential"
        assert es.delay_between == 5.0

    def test_frozen(self):
        es = ExecutionSettings()
        with pytest.raises(Exception):
            es.strategy = "sequential"


class TestBudgetSettings:
    """Review #5: Global budget control."""

    def test_no_limit(self):
        bs = BudgetSettings()
        assert bs.total_limit is None
        assert bs.mode == "soft"

    def test_hard_limit(self):
        bs = BudgetSettings(total_limit=50.0, mode="hard")
        assert bs.total_limit == 50.0
        assert bs.mode == "hard"


class TestSerializedPersona:
    def test_from_mock(self):
        p = MockPersona("p001", "Alice")
        sp = SerializedPersona(
            id=p.id, name=p.name,
            persona_type=p.persona_type.value,
        )
        assert sp.id == "p001"
        assert sp.persona_type == "standard"

    def test_frozen(self):
        sp = SerializedPersona(id="p001", name="Alice", persona_type="standard")
        with pytest.raises(Exception):
            sp.name = "Bob"


class TestRunPlan:
    """Review #1: Plan immutability and plan_hash."""

    def test_frozen_prevents_mutation(self):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas)
        with pytest.raises(Exception):
            plan.max_turns = 99

    def test_plan_hash_computed(self):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas)
        assert plan.plan_hash != ""
        assert len(plan.plan_hash) == 64  # SHA-256 hex

    def test_plan_hash_deterministic(self):
        """Same config + same personas = same plan_hash."""
        config = _make_config()
        personas = _make_personas(3)
        plan1 = PlanBuilder.build(config, personas)
        plan2 = PlanBuilder.build(config, personas)
        assert plan1.plan_hash == plan2.plan_hash

    def test_plan_hash_changes_with_config(self):
        """Different config = different plan_hash."""
        personas = _make_personas(3)
        config1 = _make_config()
        config2 = _make_config()
        config2_models = [
            _make_model_spec("model-a", "Model A"),
            _make_model_spec("model-c", "Model C"),
        ]
        config2 = MultiCompareConfig(
            name="Different Comparison",
            mode=ComparisonMode.CHAMPION_CHALLENGER,
            champion="model-a",
            models=config2_models,
            settings=ComparisonSettings(personas=5, max_turns=10),
        )
        plan1 = PlanBuilder.build(config1, personas)
        plan2 = PlanBuilder.build(config2, personas)
        assert plan1.plan_hash != plan2.plan_hash

    def test_validate_integrity(self):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas)
        assert plan.validate_integrity() is True

    def test_persona_deep_copy(self):
        """Review #1: Modifying source personas doesn't affect plan."""
        personas = _make_personas(3)
        original_name = personas[0].name
        config = _make_config()
        plan = PlanBuilder.build(config, personas)

        # Mutate source
        personas[0].name = "MUTATED"

        # Plan should be unaffected
        assert plan.personas[0].name == original_name

    def test_endpoint_ids(self):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas)
        assert plan.endpoint_ids == ["model-a", "model-b"]

    def test_scenario_assignment(self):
        config = _make_config()
        personas = _make_personas(4)
        plan = PlanBuilder.build(
            config, personas,
            scenario_ids=["clarification", "goal_shift"],
        )
        assert len(plan.scenario_assignments) == 4
        # Round-robin: p001->clarification, p002->goal_shift, p003->clarification...
        assert plan.scenario_assignments["p001"] == "clarification"
        assert plan.scenario_assignments["p002"] == "goal_shift"
        assert plan.scenario_assignments["p003"] == "clarification"


class TestPlanIO:
    """Medium #1: Plan export / reproducibility."""

    def test_save_and_load(self, tmp_path):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas)

        # Save
        saved = save_plan(plan, tmp_path)
        assert saved.exists()

        # Load
        loaded = load_plan(saved)
        assert loaded.plan_id == plan.plan_id
        assert loaded.plan_hash == plan.plan_hash
        assert loaded.validate_integrity() is True

    def test_load_tampered_plan_fails(self, tmp_path):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas)
        save_plan(plan, tmp_path)

        # Tamper with the file
        plan_file = tmp_path / "plan.json"
        data = json.loads(plan_file.read_text())
        data["max_turns"] = 999
        plan_file.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="integrity check failed"):
            load_plan(plan_file)

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_plan(tmp_path / "nonexistent.json")


# ═══════════════════════════════════════════════════════════════
# SECTION 2: Checkpoint Manager (Review #2, #4)
# ═══════════════════════════════════════════════════════════════


class TestCheckpointStateV2:
    def test_initial_state(self):
        state = CheckpointStateV2(
            plan_id="test-plan",
            plan_hash="abc123",
            persona_set_hash="def456",
            entries={
                "model-a": CheckpointEntry(model_id="model-a"),
                "model-b": CheckpointEntry(model_id="model-b"),
            },
        )
        assert state.total_endpoints == 2
        assert len(state.pending_ids) == 2
        assert len(state.completed_ids) == 0
        assert state.completion_ratio == 0.0

    def test_mark_completed(self):
        state = CheckpointStateV2(
            plan_id="test",
            plan_hash="abc",
            persona_set_hash="def",
            entries={
                "a": CheckpointEntry(model_id="a"),
                "b": CheckpointEntry(model_id="b"),
            },
        )
        state.mark_completed("a", "endpoint_a.json", execution_time=10.0, cost_usd=1.50)
        assert "a" in state.completed_ids
        assert "b" in state.pending_ids
        assert state.completion_ratio == 0.5
        assert state.cumulative_cost_usd == 1.50

    def test_mark_failed(self):
        state = CheckpointStateV2(
            plan_id="test",
            plan_hash="abc",
            persona_set_hash="def",
            entries={"a": CheckpointEntry(model_id="a")},
        )
        state.mark_failed("a", "Connection timeout", retry_count=3)
        assert "a" in state.failed_ids
        assert state.entries["a"].error == "Connection timeout"
        assert state.entries["a"].retry_count == 3

    def test_mark_skipped(self):
        state = CheckpointStateV2(
            plan_id="test",
            plan_hash="abc",
            persona_set_hash="def",
            entries={"a": CheckpointEntry(model_id="a")},
        )
        state.mark_skipped("a", "Budget exceeded")
        assert "a" in state.skipped_ids


class TestCheckpointManager:
    def test_create_and_load(self, tmp_path):
        mgr = CheckpointManager(tmp_path)
        state = mgr.create(
            plan_id="plan-1",
            plan_hash="hash1",
            persona_set_hash="phash1",
            endpoint_ids=["a", "b", "c"],
        )
        assert state.total_endpoints == 3
        assert mgr.exists("plan-1")

        loaded = mgr.load_state("plan-1")
        assert loaded is not None
        assert loaded.plan_id == "plan-1"
        assert len(loaded.pending_ids) == 3

    def test_save_and_load_endpoint_report(self, tmp_path):
        mgr = CheckpointManager(tmp_path)
        mgr.create("plan-1", "hash", "phash", ["a"])

        report_data = {"model_id": "a", "summary_dict": {"pass_rate": 0.85}}
        mgr.save_endpoint_report("plan-1", "a", report_data)

        loaded = mgr.load_endpoint_report("plan-1", "a")
        assert loaded is not None
        assert loaded["summary_dict"]["pass_rate"] == 0.85

    def test_load_all_completed_reports(self, tmp_path):
        mgr = CheckpointManager(tmp_path)
        state = mgr.create("plan-1", "hash", "phash", ["a", "b", "c"])

        # Complete a and b
        mgr.save_endpoint_report("plan-1", "a", {"model_id": "a"})
        mgr.save_endpoint_report("plan-1", "b", {"model_id": "b"})
        state.mark_completed("a", "endpoint_a.json")
        state.mark_completed("b", "endpoint_b.json")
        mgr.save_state(state)

        reports = mgr.load_all_completed_reports("plan-1")
        assert "a" in reports
        assert "b" in reports
        assert "c" not in reports

    def test_nonexistent_returns_none(self, tmp_path):
        mgr = CheckpointManager(tmp_path)
        assert mgr.load_state("nonexistent") is None
        assert not mgr.exists("nonexistent")


class TestResumeValidation:
    """Review #2: Checkpoint resume validation — config drift detection."""

    def _setup(self, tmp_path) -> tuple[CheckpointManager, str]:
        mgr = CheckpointManager(tmp_path)
        mgr.create(
            plan_id="plan-1",
            plan_hash="original_hash",
            persona_set_hash="original_personas",
            endpoint_ids=["a", "b"],
        )
        return mgr, "plan-1"

    def test_valid_resume(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        result = mgr.validate_resume(
            plan_id=plan_id,
            current_plan_hash="original_hash",
            current_persona_set_hash="original_personas",
            current_endpoint_ids=["a", "b"],
        )
        assert result.is_valid is True
        assert len(result.blocking_issues) == 0

    def test_config_changed_blocks_resume(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        result = mgr.validate_resume(
            plan_id=plan_id,
            current_plan_hash="different_hash",
            current_persona_set_hash="original_personas",
            current_endpoint_ids=["a", "b"],
        )
        assert result.is_valid is False
        assert any("Config changed" in issue for issue in result.blocking_issues)

    def test_persona_set_changed_blocks_resume(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        result = mgr.validate_resume(
            plan_id=plan_id,
            current_plan_hash="original_hash",
            current_persona_set_hash="different_personas",
            current_endpoint_ids=["a", "b"],
        )
        assert result.is_valid is False
        assert any("Persona set changed" in issue for issue in result.blocking_issues)

    def test_endpoint_set_changed_blocks_resume(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        result = mgr.validate_resume(
            plan_id=plan_id,
            current_plan_hash="original_hash",
            current_persona_set_hash="original_personas",
            current_endpoint_ids=["a", "b", "c"],  # added endpoint
        )
        assert result.is_valid is False
        assert any("Endpoint set changed" in issue for issue in result.blocking_issues)

    def test_no_checkpoint_found(self, tmp_path):
        mgr = CheckpointManager(tmp_path)
        result = mgr.validate_resume("nonexistent", "h", "p", ["a"])
        assert result.is_valid is False
        assert any("No checkpoint" in i for i in result.blocking_issues)

    def test_prepare_resume_resets_failed(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        state = mgr.load_state(plan_id)
        state.mark_failed("a", "timeout")
        mgr.save_state(state)

        resumed = mgr.prepare_resume(plan_id)
        assert resumed.resume_count == 1
        assert "a" in resumed.pending_ids  # failed -> pending
        assert len(resumed.resume_history) == 1

    def test_finalize_completed(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        state = mgr.load_state(plan_id)
        state.mark_completed("a", "a.json")
        state.mark_completed("b", "b.json")
        mgr.save_state(state)

        final = mgr.finalize(plan_id)
        assert final.status == CheckpointStatus.COMPLETED

    def test_finalize_partial(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        state = mgr.load_state(plan_id)
        state.mark_completed("a", "a.json")
        state.mark_failed("b", "error")
        mgr.save_state(state)

        final = mgr.finalize(plan_id)
        assert final.status == CheckpointStatus.PARTIAL

    def test_cleanup(self, tmp_path):
        mgr, plan_id = self._setup(tmp_path)
        assert mgr.exists(plan_id)
        mgr.cleanup(plan_id)
        assert not mgr.exists(plan_id)


# ═══════════════════════════════════════════════════════════════
# SECTION 3: Progress Events (Review #7)
# ═══════════════════════════════════════════════════════════════


class TestProgressEvent:
    def test_event_creation(self):
        event = ProgressEvent(
            event_type="endpoint_started",
            model_id="model-a",
            details={"position": "1/3"},
        )
        assert event.event_type == "endpoint_started"
        assert event.model_id == "model-a"
        assert event.timestamp

    def test_to_dict(self):
        event = ProgressEvent("test_event", "model-x", {"key": "value"})
        d = event.to_dict()
        assert d["event"] == "test_event"
        assert d["model_id"] == "model-x"
        assert d["details"]["key"] == "value"
        assert "timestamp" in d


# ═══════════════════════════════════════════════════════════════
# SECTION 4: MultiModelResult (Review #4)
# ═══════════════════════════════════════════════════════════════


class TestMultiModelResult:
    """Review #4: Partial result utilization."""

    def _make_result(
        self, model_id: str, status: ModelRunStatus = ModelRunStatus.SUCCESS,
        pass_rate: float = 0.8,
    ) -> ModelRunResult:
        return ModelRunResult(
            model_id=model_id,
            model_name=model_id,
            status=status,
            summary_dict={"pass_rate": pass_rate} if status == ModelRunStatus.SUCCESS else {},
            error="Failed" if status == ModelRunStatus.FAILED else None,
        )

    def test_full_completion(self):
        plan = PlanBuilder.build(_make_config(), _make_personas(3))
        results = {
            "model-a": self._make_result("model-a"),
            "model-b": self._make_result("model-b"),
        }
        mmr = MultiModelResult(plan=plan, results=results)
        assert mmr.is_complete
        assert mmr.completeness_ratio == 1.0
        assert mmr.is_usable

    def test_partial_results_still_usable(self):
        """3 of 4 endpoints complete — still usable for comparison."""
        models = [
            _make_model_spec("a"), _make_model_spec("b"),
            _make_model_spec("c"), _make_model_spec("d"),
        ]
        config = MultiCompareConfig(
            name="Test", mode=ComparisonMode.HEAD_TO_HEAD,
            models=models, settings=ComparisonSettings(personas=3),
        )
        plan = PlanBuilder.build(config, _make_personas(3))
        results = {
            "a": self._make_result("a"),
            "b": self._make_result("b"),
            "c": self._make_result("c"),
            "d": self._make_result("d", ModelRunStatus.FAILED),
        }
        mmr = MultiModelResult(plan=plan, results=results)
        assert not mmr.is_complete
        assert mmr.completeness_ratio == 0.75
        assert mmr.is_usable  # 3 usable results >= 2
        assert len(mmr.usable_results) == 3
        assert mmr.failed_count == 1

    def test_budget_terminated_still_usable(self):
        plan = PlanBuilder.build(_make_config(), _make_personas(3))
        results = {
            "model-a": self._make_result("model-a"),
            "model-b": ModelRunResult(
                model_id="model-b", model_name="model-b",
                status=ModelRunStatus.BUDGET_TERMINATED,
                summary_dict={"pass_rate": 0.6},
            ),
        }
        mmr = MultiModelResult(plan=plan, results=results)
        assert len(mmr.usable_results) == 2  # BUDGET_TERMINATED is usable

    def test_all_failed_not_usable(self):
        plan = PlanBuilder.build(_make_config(), _make_personas(3))
        results = {
            "model-a": self._make_result("model-a", ModelRunStatus.FAILED),
            "model-b": self._make_result("model-b", ModelRunStatus.FAILED),
        }
        mmr = MultiModelResult(plan=plan, results=results)
        assert not mmr.is_usable

    def test_summary_dict(self):
        plan = PlanBuilder.build(
            _make_config(), _make_personas(3),
            experiment=ExperimentMetadata(name="test_exp"),
        )
        results = {
            "model-a": self._make_result("model-a"),
            "model-b": self._make_result("model-b"),
        }
        mmr = MultiModelResult(plan=plan, results=results, total_execution_time=120.5)
        summary = mmr.to_summary_dict()
        assert summary["plan_id"] == plan.plan_id
        assert summary["endpoints_total"] == 2
        assert summary["endpoints_completed"] == 2
        assert summary["experiment"]["name"] == "test_exp"


# ═══════════════════════════════════════════════════════════════
# SECTION 5: EndpointExecutor (Review #3, #6)
# ═══════════════════════════════════════════════════════════════


class TestEndpointExecutor:
    """Review #3: Retry with backoff, Review #6: Model-level timeout."""

    def test_backoff_exponential(self):
        from src.multi_compare.plan import RetryPolicy
        policy = RetryPolicy(initial_delay=2.0, backoff="exponential")
        # Attempt 2: base = 2.0 * 2^0 = 2.0 (± jitter)
        delay = EndpointExecutor._compute_backoff(2, policy)
        assert 1.0 <= delay <= 3.0  # 2.0 ± 50% jitter range
        # Attempt 3: base = 2.0 * 2^1 = 4.0
        delay3 = EndpointExecutor._compute_backoff(3, policy)
        assert 2.5 <= delay3 <= 5.5

    def test_backoff_fixed(self):
        from src.multi_compare.plan import RetryPolicy
        policy = RetryPolicy(initial_delay=5.0, backoff="fixed")
        delay = EndpointExecutor._compute_backoff(2, policy)
        assert 3.0 <= delay <= 7.0  # 5.0 ± jitter

    def test_progress_events_emitted(self):
        events = []
        executor = EndpointExecutor(
            execution_settings=ExecutionSettings(),
            fairness=FairnessControls(),
            progress_callback=lambda e: events.append(e),
        )
        # The executor emits events — we test that the callback is called
        event = ProgressEvent("test", "model-a", {"key": "val"})
        executor._emit(event)
        assert len(events) == 1
        assert events[0].event_type == "test"

    def test_callback_error_does_not_break(self):
        """Callback errors should not crash the executor."""
        def bad_callback(e):
            raise RuntimeError("callback broke")

        executor = EndpointExecutor(
            execution_settings=ExecutionSettings(),
            fairness=FairnessControls(),
            progress_callback=bad_callback,
        )
        # Should not raise
        executor._emit(ProgressEvent("test", "model-a"))


# ═══════════════════════════════════════════════════════════════
# SECTION 6: MultiModelOrchestrator (Review #5, #7, #8)
# ═══════════════════════════════════════════════════════════════


class TestMultiModelOrchestrator:
    """Integration tests for the orchestrator."""

    def test_initialization(self, tmp_path):
        config = _make_config()
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
            output_dir=tmp_path / "output",
            budget=BudgetSettings(total_limit=100.0, mode="hard"),
            experiment=ExperimentMetadata(name="test_exp"),
        )
        assert orch.config == config
        assert orch._budget.total_limit == 100.0
        assert orch._experiment.name == "test_exp"

    def test_budget_skip_check_no_limit(self, tmp_path):
        config = _make_config()
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
        )
        # No budget limit — should never skip
        assert orch._should_skip_for_budget(0, {}) is False

    def test_budget_skip_check_hard_exceeded(self, tmp_path):
        config = _make_config()
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
            budget=BudgetSettings(total_limit=10.0, mode="hard"),
        )
        results = {
            "a": ModelRunResult(
                model_id="a", model_name="a",
                cost_report_dict={"total_estimated_cost": 12.0},
            ),
        }
        assert orch._should_skip_for_budget(0, results) is True

    def test_budget_skip_check_soft_does_not_skip(self, tmp_path):
        config = _make_config()
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
            budget=BudgetSettings(total_limit=10.0, mode="soft"),
        )
        results = {
            "a": ModelRunResult(
                model_id="a", model_name="a",
                cost_report_dict={"total_estimated_cost": 12.0},
            ),
        }
        # Soft mode never skips
        assert orch._should_skip_for_budget(0, results) is False

    def test_checkpoint_result_saves(self, tmp_path):
        config = _make_config()
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
            output_dir=tmp_path / "output",
        )
        state = orch.checkpoint_mgr.create(
            "plan-1", "hash", "phash", ["a", "b"]
        )

        result = ModelRunResult(
            model_id="a", model_name="Model A",
            status=ModelRunStatus.SUCCESS,
            summary_dict={"pass_rate": 0.9},
            execution_time_seconds=30.0,
        )
        orch._checkpoint_result(state, "plan-1", "a", result)

        # Verify checkpoint was updated
        loaded = orch.checkpoint_mgr.load_state("plan-1")
        assert "a" in loaded.completed_ids

        # Verify report was saved
        report = orch.checkpoint_mgr.load_endpoint_report("plan-1", "a")
        assert report is not None
        assert report["summary_dict"]["pass_rate"] == 0.9

    def test_checkpoint_failed_result(self, tmp_path):
        config = _make_config()
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
            output_dir=tmp_path / "output",
        )
        state = orch.checkpoint_mgr.create(
            "plan-1", "hash", "phash", ["a"]
        )

        result = ModelRunResult(
            model_id="a", model_name="Model A",
            status=ModelRunStatus.FAILED,
            error="Connection refused",
        )
        orch._checkpoint_result(state, "plan-1", "a", result)

        loaded = orch.checkpoint_mgr.load_state("plan-1")
        assert "a" in loaded.failed_ids

    def test_events_recorded(self, tmp_path):
        config = _make_config()
        captured = []
        orch = MultiModelOrchestrator(
            config=config,
            checkpoint_dir=tmp_path / "checkpoints",
            progress_callback=lambda e: captured.append(e),
        )
        orch._emit(ProgressEvent("test_event", details={"key": "val"}))
        assert len(orch._events) == 1
        assert len(captured) == 1
        assert captured[0].event_type == "test_event"


# ═══════════════════════════════════════════════════════════════
# SECTION 7: PlanBuilder Edge Cases
# ═══════════════════════════════════════════════════════════════


class TestPlanBuilderEdgeCases:
    def test_with_all_options(self):
        config = _make_config()
        personas = _make_personas(4)
        plan = PlanBuilder.build(
            config=config,
            personas=personas,
            scenario_ids=["clarification", "prompt_injection"],
            fairness=FairnessControls(deterministic=True, temperature=0.0, seed=42),
            execution=ExecutionSettings(strategy="sequential", model_timeout=300),
            budget=BudgetSettings(total_limit=50.0, mode="hard"),
            experiment=ExperimentMetadata(
                name="full_test",
                version="v2.0",
                tags={"team": "qa"},
            ),
        )
        assert plan.fairness.deterministic is True
        assert plan.execution.strategy == "sequential"
        assert plan.budget.total_limit == 50.0
        assert plan.experiment.name == "full_test"
        assert len(plan.scenario_assignments) == 4
        assert plan.validate_integrity() is True

    def test_empty_scenarios(self):
        config = _make_config()
        personas = _make_personas(3)
        plan = PlanBuilder.build(config, personas, scenario_ids=None)
        assert len(plan.scenario_assignments) == 0
        assert plan.scenario_assignment_hash == ""

    def test_persona_type_enum_extraction(self):
        """Handle persona_type as enum or string."""
        # Mock with enum-like
        p1 = MockPersona("p1")
        p1.persona_type = type("E", (), {"value": "adversarial"})()

        # Mock with plain string
        p2 = MockPersona("p2")
        p2.persona_type = "edge_case"

        config = _make_config()
        plan = PlanBuilder.build(config, [p1, p2])
        assert plan.personas[0].persona_type == "adversarial"
        assert plan.personas[1].persona_type == "edge_case"

    def test_large_persona_set(self):
        """Handle 100 personas without issues."""
        config = _make_config()
        personas = _make_personas(100)
        plan = PlanBuilder.build(config, personas)
        assert len(plan.personas) == 100
        assert plan.validate_integrity()
