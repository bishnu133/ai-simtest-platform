"""Engine-backed comparison provider (B.5a).

Adapts the headless engine's pure `ComparisonEngine.compare_dicts(...)` output
(`ComparisonResult`) down to the platform's `list[RegressionSignal]`.

R-1: only the pure comparison surface is used — no orchestrator, no LLM, no
file I/O. The engine import is LAZY (inside `compute`) so importing this module
stays engine-free; the B.5b startup guard (R-4) can therefore detect a missing
engine intentionally, and flag-off deployments never import the engine.

NOT wired anywhere in B.5a: the default provider remains FailClosed (503).
Wiring + feature flag + R-4/R-6 guards land in B.5b.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.comparisons.models import ComparisonRecord, RegressionSignal
from src.comparisons.provider import ComparisonDataNotReady
from src.runs.models import ResultStatus
from src.runs.repository import RunResultStore

if TYPE_CHECKING:  # engine types referenced only by annotations — never imported at runtime
    from ai_simtest_engine.core.comparison_models import ComparisonConfig, ComparisonResult

__all__ = ["ENGINE_COMPARISON_ADAPTER_VERSION", "EngineComparisonProvider"]

ENGINE_COMPARISON_ADAPTER_VERSION = "1"

# FailurePatternDelta.severity (engine string) -> platform RegressionSignal severity.
# Unknown/unexpected strings fall back to "warning" (engine output may evolve).
_PATTERN_SEVERITY = {"low": "info", "medium": "warning", "high": "critical", "critical": "critical"}


def _failure_severity(raw: str) -> str:
    return _PATTERN_SEVERITY.get((raw or "").strip().lower(), "warning")


def _metric_payload(md: Any, adapter_version: str) -> dict[str, Any]:
    return {
        "baseline_value": md.baseline_value,
        "current_value": md.current_value,
        "delta": md.delta,
        "delta_percent": md.delta_percent,
        "direction": md.direction.value,
        "adapter_version": adapter_version,
    }


def _failure_payload(fp: Any, adapter_version: str) -> dict[str, Any]:
    return {
        "status": fp.status,
        "baseline_frequency": fp.baseline_frequency,
        "current_frequency": fp.current_frequency,
        "severity": fp.severity,
        "delta": fp.delta,
        "adapter_version": adapter_version,
    }


def _adapt(result: "ComparisonResult", *, adapter_version: str) -> list[RegressionSignal]:
    """Down-project ComparisonResult.summary -> regression signals (regressions only)."""
    summary = result.summary
    verdict_fail = summary.verdict.value == "fail"
    signals: list[RegressionSignal] = []

    cf = summary.critical_failures
    if cf.is_regression:
        signals.append(RegressionSignal(
            type="critical_failures_increase", severity="critical",
            metric="critical_failures", payload=_metric_payload(cf, adapter_version)))

    pr = summary.pass_rate
    if pr.is_regression:
        sev = "critical" if (pr.current_value < result.config.pass_rate_floor or verdict_fail) else "warning"
        signals.append(RegressionSignal(
            type="pass_rate_drop", severity=sev,
            metric="pass_rate", payload=_metric_payload(pr, adapter_version)))

    av = summary.average_score
    if av.is_regression:
        sev = "critical" if verdict_fail else "warning"
        signals.append(RegressionSignal(
            type="average_score_drop", severity=sev,
            metric="average_score", payload=_metric_payload(av, adapter_version)))

    for jc in summary.judge_comparisons:
        if jc.is_regression:
            sev = "critical" if verdict_fail else "warning"
            signals.append(RegressionSignal(
                type="judge_drift", severity=sev, metric=f"judge.{jc.judge_name}",
                payload={
                    "judge_name": jc.judge_name,
                    "baseline_score": jc.baseline_score,
                    "current_score": jc.current_score,
                    "delta": jc.delta,
                    "delta_percent": jc.delta_percent,
                    "direction": jc.direction.value,
                    "adapter_version": adapter_version,
                }))

    for fp in summary.new_failures:
        signals.append(RegressionSignal(
            type="new_failure_cluster", severity=_failure_severity(fp.severity),
            metric=f"failure.{fp.pattern_name}", payload=_failure_payload(fp, adapter_version)))

    for fp in summary.worsened_failures:
        signals.append(RegressionSignal(
            type="failure_worsened", severity=_failure_severity(fp.severity),
            metric=f"failure.{fp.pattern_name}", payload=_failure_payload(fp, adapter_version)))

    return signals


class EngineComparisonProvider:
    """Computes regression signals from two RunResult snapshots via the engine.

    left_run_id = baseline, right_run_id = current (delta = current - baseline).
    """

    def __init__(self, run_result_store: RunResultStore, config: "ComparisonConfig | None" = None) -> None:
        self._store = run_result_store
        self._config = config

    async def compute(self, record: ComparisonRecord) -> tuple[list[RegressionSignal], str | None]:
        left = await self._store.get(
            record.left_run_id, tenant_id=record.tenant_id, workspace_id=record.workspace_id)
        right = await self._store.get(
            record.right_run_id, tenant_id=record.tenant_id, workspace_id=record.workspace_id)

        missing = [rid for rid, r in
                   ((record.left_run_id, left), (record.right_run_id, right)) if r is None]
        if missing:
            raise ComparisonDataNotReady(
                "Run result snapshots are not ready for comparison",
                details={"missing_run_ids": missing,
                         "left_run_id": record.left_run_id,
                         "right_run_id": record.right_run_id})

        failed = [r.run_id for r in (left, right) if r.result_status == ResultStatus.FAILED]
        if failed:
            raise ComparisonDataNotReady(
                "Run result(s) failed; no usable engine output to compare",
                details={"failed_run_ids": failed})

        # Lazy engine import (R-1 pure surface; keeps module engine-free for R-4).
        from ai_simtest_engine.core.comparison_engine import ComparisonEngine

        engine = ComparisonEngine(self._config)
        result = engine.compare_dicts(left.summary, right.summary)
        signals = _adapt(result, adapter_version=ENGINE_COMPARISON_ADAPTER_VERSION)
        return signals, None
