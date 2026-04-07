"""
Multi-Model Comparison — Checkpoint & Resume Engine (Phase 3B).

Provides:
- CheckpointManager: Saves/loads/validates checkpoint state
- Resume validation: Config drift detection (plan_hash, persona_set,
  endpoint_set comparison)
- Partial result assembly: Load completed endpoint reports

Design principles:
- Checkpoint is a directory: .checkpoints/{plan_id}/
- Each completed endpoint produces endpoint_{id}_report.json
- state.json tracks overall progress
- Resume requires plan_hash match (Review #2)
- Partial results supported (Review #4)
- Zero coupling to orchestrator internals

Addresses review points:
- #2: Manifest validation on resume (plan_hash + persona_set_hash)
- #4: Partial result utilization (load whatever completed)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ConfigDict

from src.multi_compare.models import (
    CheckpointStatus,
    ModelRunStatus,
)


# ─────────────────────────────────────────────────────────────
# Checkpoint State Model
# ─────────────────────────────────────────────────────────────

class CheckpointEntry(BaseModel):
    """Status of a single endpoint in the checkpoint."""
    model_config = ConfigDict(frozen=False)

    model_id: str
    status: str = Field(default="pending")  # pending | completed | failed | skipped
    completed_at: str | None = None
    error: str | None = None
    report_file: str | None = None
    execution_time_seconds: float = 0.0
    cost_usd: float = 0.0
    retry_count: int = 0


class CheckpointStateV2(BaseModel):
    """
    Persistent checkpoint state for multi-model comparison runs.

    Saved to .checkpoints/{plan_id}/state.json.
    Versioned schema for forward compatibility.
    """
    model_config = ConfigDict(frozen=False)

    schema_version: int = Field(default=1)

    # Identity (tied to a specific RunPlan)
    plan_id: str
    plan_hash: str
    persona_set_hash: str
    endpoint_set_hash: str = Field(
        default="",
        description="Hash of sorted endpoint IDs for drift detection",
    )

    # Progress
    status: CheckpointStatus = CheckpointStatus.IN_PROGRESS
    entries: dict[str, CheckpointEntry] = Field(
        default_factory=dict,
        description="model_id -> CheckpointEntry",
    )

    # Timestamps
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat() + "Z",
    )

    # Resume tracking
    resume_count: int = 0
    resume_history: list[str] = Field(
        default_factory=list,
        description="ISO timestamps of each resume",
    )

    # Budget tracking
    cumulative_cost_usd: float = 0.0

    @property
    def completed_ids(self) -> list[str]:
        return [mid for mid, e in self.entries.items() if e.status == "completed"]

    @property
    def failed_ids(self) -> list[str]:
        return [mid for mid, e in self.entries.items() if e.status == "failed"]

    @property
    def pending_ids(self) -> list[str]:
        return [mid for mid, e in self.entries.items() if e.status == "pending"]

    @property
    def skipped_ids(self) -> list[str]:
        return [mid for mid, e in self.entries.items() if e.status == "skipped"]

    @property
    def total_endpoints(self) -> int:
        return len(self.entries)

    @property
    def completion_ratio(self) -> float:
        if self.total_endpoints == 0:
            return 0.0
        return len(self.completed_ids) / self.total_endpoints

    def mark_completed(
        self,
        model_id: str,
        report_file: str,
        execution_time: float = 0.0,
        cost_usd: float = 0.0,
    ) -> None:
        if model_id in self.entries:
            entry = self.entries[model_id]
            entry.status = "completed"
            entry.completed_at = datetime.now(timezone.utc).isoformat() + "Z"
            entry.report_file = report_file
            entry.execution_time_seconds = execution_time
            entry.cost_usd = cost_usd
            self.cumulative_cost_usd += cost_usd
            self._touch()

    def mark_failed(
        self,
        model_id: str,
        error: str,
        retry_count: int = 0,
    ) -> None:
        if model_id in self.entries:
            entry = self.entries[model_id]
            entry.status = "failed"
            entry.error = error
            entry.retry_count = retry_count
            self._touch()

    def mark_skipped(self, model_id: str, reason: str) -> None:
        if model_id in self.entries:
            entry = self.entries[model_id]
            entry.status = "skipped"
            entry.error = reason
            self._touch()

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat() + "Z"


# ─────────────────────────────────────────────────────────────
# Resume Validation Result
# ─────────────────────────────────────────────────────────────

class ResumeValidation(BaseModel):
    """Result of validating whether a checkpoint can be resumed."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    completed_count: int = 0
    pending_count: int = 0
    failed_count: int = 0


# ─────────────────────────────────────────────────────────────
# Checkpoint Manager
# ─────────────────────────────────────────────────────────────

class CheckpointManager:
    """
    Manages checkpoint state for multi-model comparison runs.

    Directory structure:
        {checkpoint_dir}/{plan_id}/
        ├── state.json              # CheckpointStateV2
        ├── plan.json               # Frozen RunPlan copy
        ├── endpoint_bot1.json      # Completed report for bot1
        ├── endpoint_bot2.json      # Completed report for bot2
        └── ...
    """

    STATE_FILE = "state.json"
    PLAN_FILE = "plan.json"

    def __init__(self, checkpoint_dir: str | Path = ".checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)

    def _plan_dir(self, plan_id: str) -> Path:
        return self.checkpoint_dir / plan_id

    def _state_path(self, plan_id: str) -> Path:
        return self._plan_dir(plan_id) / self.STATE_FILE

    def _report_path(self, plan_id: str, model_id: str) -> Path:
        return self._plan_dir(plan_id) / f"endpoint_{model_id}.json"

    # ── Lifecycle ──────────────────────────────────────────

    def create(
        self,
        plan_id: str,
        plan_hash: str,
        persona_set_hash: str,
        endpoint_ids: list[str],
        plan_data: dict[str, Any] | None = None,
    ) -> CheckpointStateV2:
        """
        Create a new checkpoint for a run.

        Initializes all endpoints as 'pending'.
        Optionally saves a copy of the plan for reference.
        """
        plan_dir = self._plan_dir(plan_id)
        plan_dir.mkdir(parents=True, exist_ok=True)

        # Compute endpoint set hash
        endpoint_set_hash = _compute_hash(
            json.dumps(sorted(endpoint_ids))
        )

        entries = {
            mid: CheckpointEntry(model_id=mid)
            for mid in endpoint_ids
        }

        state = CheckpointStateV2(
            plan_id=plan_id,
            plan_hash=plan_hash,
            persona_set_hash=persona_set_hash,
            endpoint_set_hash=endpoint_set_hash,
            entries=entries,
        )

        self._save_state(state)

        # Save plan copy if provided
        if plan_data:
            plan_path = self._plan_dir(plan_id) / self.PLAN_FILE
            plan_path.write_text(
                json.dumps(plan_data, indent=2, default=str),
                encoding="utf-8",
            )

        return state

    def exists(self, plan_id: str) -> bool:
        """Check if a checkpoint exists for this plan."""
        return self._state_path(plan_id).exists()

    def load_state(self, plan_id: str) -> CheckpointStateV2 | None:
        """Load checkpoint state. Returns None if not found."""
        state_path = self._state_path(plan_id)
        if not state_path.exists():
            return None

        data = json.loads(state_path.read_text(encoding="utf-8"))
        return CheckpointStateV2(**data)

    def save_state(self, state: CheckpointStateV2) -> None:
        """Save checkpoint state to disk."""
        self._save_state(state)

    def _save_state(self, state: CheckpointStateV2) -> None:
        state_path = self._state_path(state.plan_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )

    # ── Endpoint Reports ──────────────────────────────────

    def save_endpoint_report(
        self,
        plan_id: str,
        model_id: str,
        report_data: dict[str, Any],
    ) -> Path:
        """Save a completed endpoint's report to checkpoint directory."""
        report_path = self._report_path(plan_id, model_id)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report_data, indent=2, default=str),
            encoding="utf-8",
        )
        return report_path

    def load_endpoint_report(
        self,
        plan_id: str,
        model_id: str,
    ) -> dict[str, Any] | None:
        """Load a completed endpoint's report. Returns None if not found."""
        report_path = self._report_path(plan_id, model_id)
        if not report_path.exists():
            return None
        return json.loads(report_path.read_text(encoding="utf-8"))

    def load_all_completed_reports(
        self,
        plan_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Load all completed endpoint reports. Returns model_id -> report_data."""
        state = self.load_state(plan_id)
        if not state:
            return {}

        results = {}
        for model_id in state.completed_ids:
            report = self.load_endpoint_report(plan_id, model_id)
            if report is not None:
                results[model_id] = report

        return results

    # ── Resume Validation (Review #2) ─────────────────────

    def validate_resume(
        self,
        plan_id: str,
        current_plan_hash: str,
        current_persona_set_hash: str,
        current_endpoint_ids: list[str],
    ) -> ResumeValidation:
        """
        Validate that a checkpoint can be safely resumed.

        Checks:
        - Plan hash matches (config hasn't changed)
        - Persona set hash matches (personas are identical)
        - Endpoint set hash matches (same endpoints)
        - At least some work was completed

        Returns:
            ResumeValidation with blocking issues and warnings.
        """
        state = self.load_state(plan_id)
        if state is None:
            return ResumeValidation(
                is_valid=False,
                blocking_issues=["No checkpoint found for this plan"],
            )

        blocking: list[str] = []
        warnings: list[str] = []

        # Check plan hash
        if state.plan_hash and current_plan_hash:
            if state.plan_hash != current_plan_hash:
                blocking.append(
                    f"Config changed since checkpoint was created "
                    f"(plan hash: {current_plan_hash[:12]}... "
                    f"vs checkpoint: {state.plan_hash[:12]}...). "
                    f"Use --force-restart to start fresh."
                )

        # Check persona set hash
        if state.persona_set_hash and current_persona_set_hash:
            if state.persona_set_hash != current_persona_set_hash:
                blocking.append(
                    f"Persona set changed "
                    f"(hash: {current_persona_set_hash[:12]}... "
                    f"vs checkpoint: {state.persona_set_hash[:12]}...)"
                )

        # Check endpoint set
        current_ep_hash = _compute_hash(
            json.dumps(sorted(current_endpoint_ids))
        )
        if state.endpoint_set_hash and current_ep_hash != state.endpoint_set_hash:
            blocking.append(
                f"Endpoint set changed. Checkpoint has "
                f"{list(state.entries.keys())}, current config has "
                f"{current_endpoint_ids}."
            )

        # Check there's actually work done to resume from
        if not state.completed_ids:
            warnings.append(
                "Checkpoint exists but no endpoints completed yet. "
                "Resuming will re-run everything."
            )

        # Check for stale status
        if state.status == CheckpointStatus.COMPLETED:
            warnings.append(
                "Checkpoint shows run already completed. "
                "Resuming will skip all endpoints."
            )

        return ResumeValidation(
            is_valid=len(blocking) == 0,
            blocking_issues=blocking,
            warnings=warnings,
            completed_count=len(state.completed_ids),
            pending_count=len(state.pending_ids),
            failed_count=len(state.failed_ids),
        )

    def prepare_resume(self, plan_id: str) -> CheckpointStateV2:
        """
        Prepare a checkpoint for resume.

        - Increments resume_count
        - Records resume timestamp
        - Resets failed endpoints to pending (for retry)
        - Saves updated state

        Returns:
            Updated CheckpointStateV2.
        """
        state = self.load_state(plan_id)
        if state is None:
            raise FileNotFoundError(f"No checkpoint found for plan {plan_id}")

        state.resume_count += 1
        state.resume_history.append(
            datetime.now(timezone.utc).isoformat() + "Z"
        )

        # Reset failed endpoints to pending for retry
        for model_id in state.failed_ids:
            state.entries[model_id].status = "pending"
            state.entries[model_id].error = None
            state.entries[model_id].retry_count = 0

        state.status = CheckpointStatus.IN_PROGRESS
        self._save_state(state)
        return state

    # ── Finalization ──────────────────────────────────────

    def finalize(self, plan_id: str) -> CheckpointStateV2:
        """
        Finalize a checkpoint after all endpoints have been processed.

        Sets final status based on completion ratio.
        """
        state = self.load_state(plan_id)
        if state is None:
            raise FileNotFoundError(f"No checkpoint found for plan {plan_id}")

        completed = len(state.completed_ids)
        total = state.total_endpoints

        if completed == total:
            if state.resume_count > 0:
                state.status = CheckpointStatus.COMPLETED_WITH_RESUME
            else:
                state.status = CheckpointStatus.COMPLETED
        else:
            state.status = CheckpointStatus.PARTIAL

        state._touch()
        self._save_state(state)
        return state

    # ── Cleanup ───────────────────────────────────────────

    def cleanup(self, plan_id: str) -> bool:
        """Remove checkpoint directory for a completed run."""
        plan_dir = self._plan_dir(plan_id)
        if plan_dir.exists():
            shutil.rmtree(plan_dir)
            return True
        return False


# ─────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────

def _compute_hash(data: str) -> str:
    """SHA-256 hash of a string."""
    import hashlib
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
