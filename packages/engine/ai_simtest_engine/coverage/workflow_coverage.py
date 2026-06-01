"""
Workflow Coverage Extension — Adds workflow step coverage as a 5th dimension
to the CoverageAnalyzer.

Measures: Which workflow steps were actually exercised across the test run,
expressed as a coverage percentage. This answers: "Did the simulation test
all defined workflow steps, or did some never get exercised?"

Two integration modes:
  1. Direct: Pass workflow results to CoverageAnalyzer.analyze() via new param
  2. File-based: Load workflow_*.json exports from output directory

This module extends the existing coverage system non-destructively:
  - Adds WorkflowCoverage dataclass
  - Adds workflow_coverage dimension to CoverageReport
  - Updates overall score computation with workflow weight
  - Adds workflow gaps and recommendations

Usage:
    from ai_simtest_engine.coverage.workflow_coverage import (
        WorkflowCoverage,
        analyze_workflow_coverage,
        load_workflow_coverage_from_exports,
    )

    # Direct mode
    wf_coverage = analyze_workflow_coverage(workflow_exports)

    # File mode
    wf_coverage = load_workflow_coverage_from_exports(output_dir)

    # Integrate into CoverageReport
    inject_workflow_coverage_into_report(coverage_report, wf_coverage)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Data Model ────────────────────────────────────────────────────────────────

@dataclass
class WorkflowStepCoverage:
    """Coverage metrics for a single workflow step across all conversations."""
    step_name: str = ""
    step_id: str = ""
    required: bool = True
    times_completed: int = 0
    times_partial: int = 0
    times_missed: int = 0
    total_evaluated: int = 0
    completion_rate: float = 0.0  # (completed + 0.5*partial) / total


@dataclass
class WorkflowCoverage:
    """Coverage metrics for workflow step exercising across a test run."""
    # Per-workflow breakdown
    workflows_evaluated: list[str] = field(default_factory=list)
    workflow_domains: list[str] = field(default_factory=list)

    # Step-level coverage
    total_steps_defined: int = 0
    total_required_steps: int = 0
    steps_exercised: int = 0  # completed or partial at least once
    steps_never_exercised: int = 0
    step_details: list[WorkflowStepCoverage] = field(default_factory=list)

    # Aggregate
    step_coverage_score: float = 0.0  # weighted: required steps count more
    workflow_pass_rate: float = 0.0
    total_conversations: int = 0
    total_passed: int = 0
    total_failed: int = 0
    total_critical: int = 0

    # Gaps
    never_exercised_steps: list[str] = field(default_factory=list)
    low_coverage_steps: list[str] = field(default_factory=list)  # < 50% completion rate

    # Overall dimension score (0.0–1.0)
    score: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for JSON export."""
        return {
            "score": round(self.score, 4),
            "workflows_evaluated": self.workflows_evaluated,
            "workflow_domains": self.workflow_domains,
            "total_steps_defined": self.total_steps_defined,
            "total_required_steps": self.total_required_steps,
            "steps_exercised": self.steps_exercised,
            "steps_never_exercised": self.steps_never_exercised,
            "step_coverage_score": round(self.step_coverage_score, 4),
            "workflow_pass_rate": round(self.workflow_pass_rate, 4),
            "total_conversations": self.total_conversations,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "total_critical": self.total_critical,
            "never_exercised_steps": self.never_exercised_steps,
            "low_coverage_steps": self.low_coverage_steps,
            "step_details": [
                {
                    "step_name": sd.step_name,
                    "step_id": sd.step_id,
                    "required": sd.required,
                    "times_completed": sd.times_completed,
                    "times_partial": sd.times_partial,
                    "times_missed": sd.times_missed,
                    "total_evaluated": sd.total_evaluated,
                    "completion_rate": round(sd.completion_rate, 4),
                }
                for sd in self.step_details
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowCoverage:
        """Deserialize from dict."""
        wc = cls()
        wc.score = data.get("score", 0.0)
        wc.workflows_evaluated = data.get("workflows_evaluated", [])
        wc.workflow_domains = data.get("workflow_domains", [])
        wc.total_steps_defined = data.get("total_steps_defined", 0)
        wc.total_required_steps = data.get("total_required_steps", 0)
        wc.steps_exercised = data.get("steps_exercised", 0)
        wc.steps_never_exercised = data.get("steps_never_exercised", 0)
        wc.step_coverage_score = data.get("step_coverage_score", 0.0)
        wc.workflow_pass_rate = data.get("workflow_pass_rate", 0.0)
        wc.total_conversations = data.get("total_conversations", 0)
        wc.total_passed = data.get("total_passed", 0)
        wc.total_failed = data.get("total_failed", 0)
        wc.total_critical = data.get("total_critical", 0)
        wc.never_exercised_steps = data.get("never_exercised_steps", [])
        wc.low_coverage_steps = data.get("low_coverage_steps", [])
        for sd_data in data.get("step_details", []):
            wc.step_details.append(WorkflowStepCoverage(
                step_name=sd_data.get("step_name", ""),
                step_id=sd_data.get("step_id", ""),
                required=sd_data.get("required", True),
                times_completed=sd_data.get("times_completed", 0),
                times_partial=sd_data.get("times_partial", 0),
                times_missed=sd_data.get("times_missed", 0),
                total_evaluated=sd_data.get("total_evaluated", 0),
                completion_rate=sd_data.get("completion_rate", 0.0),
            ))
        return wc


# ── Analyzer ──────────────────────────────────────────────────────────────────

def analyze_workflow_coverage(
    workflow_exports: list[dict[str, Any]],
) -> WorkflowCoverage:
    """
    Analyze workflow step coverage from workflow export dicts.

    Args:
        workflow_exports: List of workflow export dicts. Each must contain:
            - workflow (str): name
            - domain (str): domain
            - total_conversations (int)
            - passed (int)
            - failed (int)
            - critical_failures (int)
            - results (list[dict]): per-conversation results with step_results

    Returns:
        WorkflowCoverage with step-level coverage analysis.
    """
    wc = WorkflowCoverage()

    if not workflow_exports:
        return wc

    # ── Aggregate workflow-level stats ─────────────────────────────────
    for exp in workflow_exports:
        wf_name = exp.get("workflow", "Unknown")
        wf_domain = exp.get("domain", "general")
        if wf_name not in wc.workflows_evaluated:
            wc.workflows_evaluated.append(wf_name)
        if wf_domain not in wc.workflow_domains:
            wc.workflow_domains.append(wf_domain)

        wc.total_conversations += exp.get("total_conversations", 0)
        wc.total_passed += exp.get("passed", 0)
        wc.total_failed += exp.get("failed", 0)
        wc.total_critical += exp.get("critical_failures", 0)

    wc.workflow_pass_rate = (
        wc.total_passed / wc.total_conversations
        if wc.total_conversations > 0
        else 0.0
    )

    # ── Step-level analysis ───────────────────────────────────────────
    # Collect all step results across all workflows and conversations
    # Key: (workflow_name, step_name) → {completed, partial, missed, total, required, step_id}
    step_tracker: dict[tuple[str, str], dict] = {}

    for exp in workflow_exports:
        wf_name = exp.get("workflow", "Unknown")
        results = exp.get("results", [])

        for result in results:
            # Skip skipped results (non-applicable conversations)
            if result.get("status") == "skipped_not_applicable":
                continue

            # Handle both full WorkflowResult dicts and summary dicts
            step_results = result.get("step_results", [])

            # If step_results not directly available, try completed/missed lists
            if not step_results:
                completed = result.get("completed_steps", [])
                missed = result.get("missed_steps", [])
                for sname in completed:
                    key = (wf_name, sname)
                    if key not in step_tracker:
                        step_tracker[key] = {
                            "completed": 0, "partial": 0, "missed": 0,
                            "total": 0, "required": True, "step_id": sname,
                        }
                    step_tracker[key]["completed"] += 1
                    step_tracker[key]["total"] += 1
                for sname in missed:
                    key = (wf_name, sname)
                    if key not in step_tracker:
                        step_tracker[key] = {
                            "completed": 0, "partial": 0, "missed": 0,
                            "total": 0, "required": True, "step_id": sname,
                        }
                    step_tracker[key]["missed"] += 1
                    step_tracker[key]["total"] += 1
            else:
                for sr in step_results:
                    sname = sr.get("step_name", sr.get("name", ""))
                    sid = sr.get("step_id", sr.get("id", sname))
                    status = sr.get("status", "missed")
                    required = sr.get("required", True)

                    key = (wf_name, sname)
                    if key not in step_tracker:
                        step_tracker[key] = {
                            "completed": 0, "partial": 0, "missed": 0,
                            "total": 0, "required": required, "step_id": sid,
                        }

                    if status == "completed":
                        step_tracker[key]["completed"] += 1
                    elif status == "partial":
                        step_tracker[key]["partial"] += 1
                    else:
                        step_tracker[key]["missed"] += 1
                    step_tracker[key]["total"] += 1

    # ── Build step details ────────────────────────────────────────────
    for (wf_name, sname), data in step_tracker.items():
        total = data["total"]
        completed = data["completed"]
        partial = data["partial"]
        missed = data["missed"]

        completion_rate = (
            (completed + 0.5 * partial) / total if total > 0 else 0.0
        )

        display_name = f"{sname} ({wf_name})" if len(wc.workflows_evaluated) > 1 else sname

        sc = WorkflowStepCoverage(
            step_name=display_name,
            step_id=data["step_id"],
            required=data["required"],
            times_completed=completed,
            times_partial=partial,
            times_missed=missed,
            total_evaluated=total,
            completion_rate=completion_rate,
        )
        wc.step_details.append(sc)
        wc.total_steps_defined += 1
        if data["required"]:
            wc.total_required_steps += 1

    # ── Classify coverage quality ─────────────────────────────────────
    for sd in wc.step_details:
        if sd.times_completed == 0 and sd.times_partial == 0:
            wc.steps_never_exercised += 1
            wc.never_exercised_steps.append(sd.step_name)
        else:
            wc.steps_exercised += 1

        if sd.completion_rate < 0.5 and (sd.times_completed > 0 or sd.times_partial > 0):
            wc.low_coverage_steps.append(sd.step_name)

    # ── Compute step coverage score ───────────────────────────────────
    # Weighted: required steps count 2x, optional steps count 1x
    if wc.step_details:
        weighted_sum = 0.0
        weight_total = 0.0
        for sd in wc.step_details:
            w = 2.0 if sd.required else 1.0
            weighted_sum += sd.completion_rate * w
            weight_total += w
        wc.step_coverage_score = weighted_sum / weight_total if weight_total > 0 else 0.0
    else:
        wc.step_coverage_score = 0.0

    # ── Overall dimension score ───────────────────────────────────────
    # 60% step coverage + 30% pass rate + 10% zero-critical bonus
    critical_bonus = 1.0 if wc.total_critical == 0 else 0.0
    wc.score = round(
        wc.step_coverage_score * 0.60
        + wc.workflow_pass_rate * 0.30
        + critical_bonus * 0.10,
        4,
    )

    return wc


def load_workflow_coverage_from_exports(
    output_dir: str | Path,
) -> WorkflowCoverage | None:
    """
    Load workflow exports from output directory and analyze coverage.

    Looks for workflow_*.json files in output_dir.

    Returns:
        WorkflowCoverage if any workflow files found, None otherwise.
    """
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return None

    exports = []
    for wf_file in sorted(output_dir.glob("workflow_*.json")):
        try:
            with open(wf_file) as f:
                exports.append(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load workflow export {wf_file}: {e}")
            continue

    if not exports:
        return None

    return analyze_workflow_coverage(exports)


# ── Integration with CoverageReport ──────────────────────────────────────────

def inject_workflow_coverage_into_report(
    coverage_report: Any,
    workflow_coverage: WorkflowCoverage,
    weight: float = 0.20,
) -> None:
    """
    Inject workflow coverage as a 5th dimension into an existing CoverageReport.

    Updates:
      - dimension_scores["workflow"] = workflow_coverage.score
      - dimension_weights (re-normalized to include workflow)
      - overall_coverage (re-computed)
      - grade (re-computed)
      - gaps (workflow gaps appended)
      - recommendations (workflow recommendations appended)

    Args:
        coverage_report: CoverageReport instance
        workflow_coverage: WorkflowCoverage instance
        weight: Weight for workflow dimension (default 0.20, other weights rescaled)
    """
    # Add workflow dimension score
    coverage_report.dimension_scores["workflow"] = workflow_coverage.score

    # Re-normalize weights to include workflow
    existing_total = sum(
        v for k, v in coverage_report.dimension_weights.items() if k != "workflow"
    )
    if existing_total > 0:
        scale = (1.0 - weight) / existing_total
        for k in list(coverage_report.dimension_weights.keys()):
            if k != "workflow":
                coverage_report.dimension_weights[k] *= scale
    coverage_report.dimension_weights["workflow"] = weight

    # Re-compute overall coverage
    overall = sum(
        coverage_report.dimension_scores.get(dim, 0.0) * coverage_report.dimension_weights.get(dim, 0.0)
        for dim in coverage_report.dimension_weights
    )
    coverage_report.overall_coverage = round(overall, 4)

    # Re-compute grade
    from ai_simtest_engine.coverage.constants import CoverageGrade
    coverage_report.grade = CoverageGrade.from_score(coverage_report.overall_coverage)

    # Append workflow gaps
    for step in workflow_coverage.never_exercised_steps:
        coverage_report.gaps.append(f"Workflow step '{step}' was never exercised")

    for step in workflow_coverage.low_coverage_steps:
        coverage_report.gaps.append(f"Workflow step '{step}' has low completion rate (<50%)")

    if workflow_coverage.total_critical > 0:
        coverage_report.gaps.append(
            f"{workflow_coverage.total_critical} critical workflow violation(s) detected"
        )

    # Append workflow recommendations
    if workflow_coverage.never_exercised_steps:
        n = len(workflow_coverage.never_exercised_steps)
        coverage_report.recommendations.append(
            f"{n} workflow step(s) never exercised. "
            f"Add scenarios or personas that trigger these steps."
        )

    if workflow_coverage.score < 0.6:
        coverage_report.recommendations.append(
            f"Workflow coverage is {workflow_coverage.score:.0%}. "
            f"Consider adding more diverse conversations to improve step coverage."
        )

    if workflow_coverage.total_critical > 0:
        coverage_report.recommendations.append(
            "Critical workflow violations found. "
            "Review and fix the bot's handling of required workflow steps."
        )
