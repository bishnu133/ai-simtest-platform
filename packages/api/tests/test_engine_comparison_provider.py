"""B.5a — adapter (_adapt) + EngineComparisonProvider.compute."""
from __future__ import annotations

import pytest

from ai_simtest_engine.core.comparison_models import (
    ComparisonConfig, ComparisonResult, ComparisonSummary, DeltaDirection,
    FailurePatternDelta, JudgeComparison, MetricDelta, RegressionVerdict,
)

from src.comparisons.engine_provider import (
    ENGINE_COMPARISON_ADAPTER_VERSION, EngineComparisonProvider, _adapt,
)
from src.comparisons.models import ComparisonRecord
from src.comparisons.provider import ComparisonDataNotReady
from src.runs.models import ResultStatus, RunResult
from src.runs.repository import InMemoryRunResultStore


# ---------- adapter unit tests (engine ComparisonResult built directly) ----------

def _md(name, base, cur, hib=True):
    return MetricDelta.compute(name, base, cur, higher_is_better=hib)


def _summary(**ov):
    base = dict(
        baseline_name="b", current_name="c",
        pass_rate=_md("pass_rate", 1.0, 1.0),
        average_score=_md("average_score", 1.0, 1.0),
        critical_failures=_md("critical_failures", 0, 0, hib=False),
        total_conversations=_md("total_conversations", 10, 10),
        total_turns=_md("total_turns", 100, 100),
    )
    base.update(ov)
    return ComparisonSummary(**base)


def _result(summary, config=None):
    return ComparisonResult(summary=summary, config=config or ComparisonConfig(),
                            baseline_report={}, current_report={})


def test_adapt_no_regression_empty():
    assert _adapt(_result(_summary()), adapter_version="1") == []


def test_adapt_pass_rate_drop_warning():
    s = _summary(pass_rate=_md("pass_rate", 0.9, 0.7), verdict=RegressionVerdict.WARNING)
    sig = _adapt(_result(s), adapter_version="1")
    assert len(sig) == 1
    assert sig[0].type == "pass_rate_drop" and sig[0].severity == "warning"
    assert sig[0].metric == "pass_rate"
    assert sig[0].payload["adapter_version"] == "1"
    assert sig[0].payload["direction"] == "regressed"


def test_adapt_pass_rate_drop_critical_on_fail():
    s = _summary(pass_rate=_md("pass_rate", 0.9, 0.7), verdict=RegressionVerdict.FAIL)
    assert _adapt(_result(s), adapter_version="1")[0].severity == "critical"


def test_adapt_critical_failures_increase_is_critical():
    s = _summary(critical_failures=_md("critical_failures", 0, 3, hib=False))
    sig = [x for x in _adapt(_result(s), adapter_version="1") if x.type == "critical_failures_increase"]
    assert len(sig) == 1 and sig[0].severity == "critical"


def test_adapt_average_score_drop():
    s = _summary(average_score=_md("average_score", 0.8, 0.6))
    sig = [x for x in _adapt(_result(s), adapter_version="1") if x.type == "average_score_drop"]
    assert len(sig) == 1 and sig[0].severity == "warning"


def test_adapt_judge_drift():
    jc = JudgeComparison(judge_name="grounding", baseline_score=0.9, current_score=0.6,
                         delta=-0.3, delta_percent=-33.3, direction=DeltaDirection.REGRESSED,
                         is_regression=True)
    sig = [x for x in _adapt(_result(_summary(judge_comparisons=[jc])), adapter_version="1")
           if x.type == "judge_drift"]
    assert len(sig) == 1 and sig[0].metric == "judge.grounding"
    assert sig[0].severity == "warning" and sig[0].payload["judge_name"] == "grounding"


def test_adapt_new_failure_cluster_severity_map():
    fps = [
        FailurePatternDelta(pattern_name="loop", status="new", current_frequency=3, severity="high"),
        FailurePatternDelta(pattern_name="pii", status="new", current_frequency=1, severity="low"),
        FailurePatternDelta(pattern_name="weird", status="new", current_frequency=1, severity="bogus"),
    ]
    sig = {x.metric: x for x in _adapt(_result(_summary(new_failures=fps)), adapter_version="1")
           if x.type == "new_failure_cluster"}
    assert sig["failure.loop"].severity == "critical"
    assert sig["failure.pii"].severity == "info"
    assert sig["failure.weird"].severity == "warning"  # unknown -> warning


def test_adapt_failure_worsened():
    fp = FailurePatternDelta(pattern_name="loop", status="worsened", baseline_frequency=1,
                             current_frequency=4, severity="medium", delta=3)
    sig = [x for x in _adapt(_result(_summary(worsened_failures=[fp])), adapter_version="1")
           if x.type == "failure_worsened"]
    assert len(sig) == 1 and sig[0].severity == "warning" and sig[0].metric == "failure.loop"


# ---------- provider.compute (real compare_dicts + store) ----------

_GOOD = {"pass_rate": 0.9, "average_score": 0.8, "critical_failures": 1,
         "total_conversations": 10, "total_turns": 100}
_REGRESSED = {"pass_rate": 0.7, "average_score": 0.6, "critical_failures": 5,
              "total_conversations": 10, "total_turns": 100}


def _rr(run_id, summary, status=ResultStatus.PRODUCED):
    return RunResult(run_id=run_id, tenant_id="ten-1", workspace_id="ws-1",
                     result_status=status, engine_version="engine_v1",
                     adapter_version="run_adapter_v1", summary=summary)


def _record():
    return ComparisonRecord(id="cmp-1", workspace_id="ws-1", tenant_id="ten-1",
                            left_run_id="L", right_run_id="R")


@pytest.mark.asyncio
async def test_compute_happy_path_returns_signals():
    store = InMemoryRunResultStore()
    await store.put(_rr("L", _GOOD))
    await store.put(_rr("R", _REGRESSED))
    signals, error = await EngineComparisonProvider(store).compute(_record())
    assert error is None
    types = {s.type for s in signals}
    assert {"pass_rate_drop", "average_score_drop", "critical_failures_increase"} <= types


@pytest.mark.asyncio
async def test_compute_no_regression_empty():
    store = InMemoryRunResultStore()
    await store.put(_rr("L", _GOOD))
    await store.put(_rr("R", dict(_GOOD)))
    signals, error = await EngineComparisonProvider(store).compute(_record())
    assert error is None and signals == []


@pytest.mark.asyncio
async def test_compute_absent_left_raises():
    store = InMemoryRunResultStore()
    await store.put(_rr("R", _GOOD))
    with pytest.raises(ComparisonDataNotReady):
        await EngineComparisonProvider(store).compute(_record())


@pytest.mark.asyncio
async def test_compute_absent_right_raises():
    store = InMemoryRunResultStore()
    await store.put(_rr("L", _GOOD))
    with pytest.raises(ComparisonDataNotReady):
        await EngineComparisonProvider(store).compute(_record())


@pytest.mark.asyncio
async def test_compute_failed_result_raises():
    store = InMemoryRunResultStore()
    await store.put(_rr("L", _GOOD))
    await store.put(_rr("R", _GOOD, status=ResultStatus.FAILED))
    with pytest.raises(ComparisonDataNotReady):
        await EngineComparisonProvider(store).compute(_record())


@pytest.mark.asyncio
async def test_compute_partial_with_summary_is_comparable():
    store = InMemoryRunResultStore()
    await store.put(_rr("L", _GOOD, status=ResultStatus.PARTIAL))
    await store.put(_rr("R", _REGRESSED, status=ResultStatus.PARTIAL))
    signals, error = await EngineComparisonProvider(store).compute(_record())
    assert error is None and any(s.type == "pass_rate_drop" for s in signals)


@pytest.mark.asyncio
async def test_compute_data_not_ready_is_409():
    store = InMemoryRunResultStore()
    with pytest.raises(ComparisonDataNotReady) as ei:
        await EngineComparisonProvider(store).compute(_record())
    assert ei.value.http_status == 409 and ei.value.code == "comparison_data_not_ready"
