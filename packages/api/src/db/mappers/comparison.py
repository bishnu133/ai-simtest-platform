"""Comparison domain ↔ ORM mapper.

Two non-trivial translations:

  1. `regression_signals` is a list of typed `RegressionSignal` models
     at the domain layer. The ORM stores them as a JSONB array of dicts.
     The mapper does the dict↔model conversion both ways.

  2. `left_provenance` / `right_provenance` are `RunProvenance` models
     at the domain layer. The ORM stores each as a nullable JSONB dict.

  3. `metric_deltas` is a list of `MetricDelta` models or None.
     Stored as JSONB array or SQL NULL.

  4. `initiated_by_actor_id` exists on the ORM but not on the
     `ComparisonRecord` domain shape. Turn 2.7 Drift 4 (plan v0.2.1 §2.4):
     comparison_to_orm accepts an optional `write_ctx: WriteContext`;
     the actor_id is lifted from `write_ctx.actor.actor_id`. If
     `write_ctx` is None, the mapper falls back to
     `WriteContext.system().actor.actor_id` (= "system") for
     backward compatibility with callers not yet threaded through.
"""
from __future__ import annotations

from typing import Any, cast

from src.common.write_context import WriteContext
from src.comparisons.models import (
    ComparisonRecord,
    ComparisonStatus,
    MetricDelta,
    RegressionSignal,
    RunProvenance,
)
from src.db.models import Comparison
from src.runs.models import RunStatus


def _signal_to_dict(signal: RegressionSignal) -> dict[str, Any]:
    return {
        "type": signal.type,
        "severity": signal.severity,
        "metric": signal.metric,
        "payload": dict(signal.payload),
    }


def _dict_to_signal(d: dict[str, Any]) -> RegressionSignal:
    return RegressionSignal(
        type=d["type"],
        severity=d["severity"],
        metric=d["metric"],
        payload=dict(d.get("payload") or {}),
    )


def _provenance_to_dict(prov: RunProvenance) -> dict[str, Any]:
    return {
        "run_id": prov.run_id,
        "engine_version": prov.engine_version,
        "asset_versions_used": list(prov.asset_versions_used),
        "run_status": prov.run_status.value,
    }


def _dict_to_provenance(d: dict[str, Any]) -> RunProvenance:
    return RunProvenance(
        run_id=d["run_id"],
        engine_version=d["engine_version"],
        asset_versions_used=list(d.get("asset_versions_used") or []),
        run_status=RunStatus(d["run_status"]),
    )


def _delta_to_dict(delta: MetricDelta) -> dict[str, Any]:
    return {
        "metric": delta.metric,
        "left": delta.left,
        "right": delta.right,
        "delta": delta.delta,
    }


def _dict_to_delta(d: dict[str, Any]) -> MetricDelta:
    return MetricDelta(
        metric=d["metric"],
        left=d["left"],
        right=d["right"],
        delta=d["delta"],
    )


def comparison_to_domain(row: Comparison) -> ComparisonRecord:
    # regression_signals is stored as a JSONB list; tolerate both list and
    # dict-with-"items" shapes for forward compatibility.
    raw_signals = row.regression_signals
    if isinstance(raw_signals, list):
        signals = [_dict_to_signal(d) for d in raw_signals]
    elif isinstance(raw_signals, dict) and "items" in raw_signals:
        signals = [_dict_to_signal(d) for d in raw_signals["items"]]
    else:
        signals = []

    left_provenance = (
        _dict_to_provenance(row.left_provenance) if row.left_provenance else None
    )
    right_provenance = (
        _dict_to_provenance(row.right_provenance) if row.right_provenance else None
    )

    metric_deltas: list[MetricDelta] | None = None
    if row.metric_deltas is not None:
        if isinstance(row.metric_deltas, list):
            metric_deltas = [_dict_to_delta(d) for d in row.metric_deltas]
        elif isinstance(row.metric_deltas, dict) and "items" in row.metric_deltas:
            metric_deltas = [_dict_to_delta(d) for d in row.metric_deltas["items"]]

    # `result_ref` in the domain model is a scalar str | None. The ORM
    # stores (result_bucket, result_key); encode as "bucket/key" or None.
    result_ref: str | None = None
    if row.result_bucket and row.result_key:
        result_ref = f"{row.result_bucket}/{row.result_key}"

    return ComparisonRecord(
        id=str(row.id),
        workspace_id=str(row.workspace_id),
        tenant_id=str(row.tenant_id),
        left_run_id=str(row.left_run_id),
        right_run_id=str(row.right_run_id),
        status=ComparisonStatus(row.status),
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error=row.error,
        engine_version=row.engine_version,
        regression_signals=signals,
        left_provenance=left_provenance,
        right_provenance=right_provenance,
        verdict=cast(Any, row.verdict),  # Literal in domain, str in ORM
        evidence_count=row.evidence_count,
        metric_deltas=metric_deltas,
        comparison_profile=row.comparison_profile,
        result_ref=result_ref,
    )


def comparison_to_orm(
    record: ComparisonRecord,
    *,
    write_ctx: WriteContext | None = None,
) -> Comparison:
    """Map a ComparisonRecord → Comparison ORM row.

    Turn 2.7 Drift 4 (plan v0.2.1 §2.4):
        ``write_ctx`` is the new optional kwarg that carries the actor
        performing the write. If None, falls back to
        ``WriteContext.system()`` (= actor_id "system") to preserve
        backward compatibility with callers not yet threaded through
        (smoke scripts, fixtures, in-memory uses).
    """
    signals_json = [_signal_to_dict(s) for s in record.regression_signals]

    left_prov_json = (
        _provenance_to_dict(record.left_provenance) if record.left_provenance else None
    )
    right_prov_json = (
        _provenance_to_dict(record.right_provenance) if record.right_provenance else None
    )
    metric_deltas_json = (
        [_delta_to_dict(d) for d in record.metric_deltas]
        if record.metric_deltas is not None
        else None
    )

    # Unpack result_ref ("bucket/key" → (bucket, key)) if present.
    result_bucket: str | None = None
    result_key: str | None = None
    if record.result_ref and "/" in record.result_ref:
        result_bucket, _, result_key = record.result_ref.partition("/")

    # Drift 4 actor lift — pull from write_ctx (or fall back to system).
    effective_ctx = write_ctx if write_ctx is not None else WriteContext.system()
    actor_id = effective_ctx.actor.actor_id

    return Comparison(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        left_run_id=record.left_run_id,
        right_run_id=record.right_run_id,
        status=record.status.value,
        # initiated_by_actor_id sourced from write_ctx (Turn 2.7 Drift 4).
        # Falls back to "system" via WriteContext.system() when write_ctx
        # is None — preserves Turn 2.6 behavior for non-threaded callers.
        initiated_by_actor_id=actor_id,
        engine_version=record.engine_version,
        config={},  # not in the Week 6a domain shape
        regression_signals=signals_json,
        left_provenance=left_prov_json,
        right_provenance=right_prov_json,
        metric_deltas=metric_deltas_json,
        result_bucket=result_bucket,
        result_key=result_key,
        verdict=record.verdict,
        evidence_count=record.evidence_count,
        comparison_profile=record.comparison_profile,
        error=record.error,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )
