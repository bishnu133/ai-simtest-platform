"""B.4 — RunResult contract + InMemoryRunResultStore (Engine-Integration)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runs.models import RUN_RESULT_SCHEMA_VERSION, ResultStatus, RunResult
from src.runs.repository import InMemoryRunResultStore, RunResultStore


def _make(**overrides) -> RunResult:
    base = dict(
        run_id="run-1", tenant_id="ten-1", workspace_id="ws-1",
        result_status=ResultStatus.PRODUCED,
        engine_version="engine_v1", adapter_version="adapter_v1",
    )
    base.update(overrides)
    return RunResult(**base)


def test_runresult_defaults():
    r = _make()
    assert r.schema_version == RUN_RESULT_SCHEMA_VERSION == "1"
    assert r.summary == {} and r.metrics == {} and r.judge_scores == {}
    assert r.failures == [] and r.raw_engine_payload == {}
    assert r.created_at is not None


def test_result_status_values():
    assert [s.value for s in ResultStatus] == ["produced", "partial", "failed"]


def test_adapter_version_required():
    with pytest.raises(ValidationError):
        RunResult(
            run_id="run-1", tenant_id="ten-1", workspace_id="ws-1",
            result_status=ResultStatus.PRODUCED, engine_version="engine_v1",
        )


@pytest.mark.parametrize("field", ["run_id", "adapter_version"])
def test_empty_string_rejected(field):
    with pytest.raises(ValidationError):
        _make(**{field: ""})


@pytest.mark.asyncio
async def test_put_get_roundtrip():
    store = InMemoryRunResultStore()
    await store.put(_make())
    got = await store.get("run-1", tenant_id="ten-1", workspace_id="ws-1")
    assert got is not None and got.run_id == "run-1" and got.adapter_version == "adapter_v1"


@pytest.mark.asyncio
async def test_get_absent_returns_none():
    store = InMemoryRunResultStore()
    assert await store.get("nope", tenant_id="ten-1", workspace_id="ws-1") is None


@pytest.mark.asyncio
async def test_tenant_workspace_isolation():
    store = InMemoryRunResultStore()
    await store.put(_make(run_id="shared"))
    assert await store.get("shared", tenant_id="ten-2", workspace_id="ws-1") is None
    assert await store.get("shared", tenant_id="ten-1", workspace_id="ws-2") is None
    assert await store.get("shared", tenant_id="ten-1", workspace_id="ws-1") is not None


def test_runtime_checkable_protocol():
    assert isinstance(InMemoryRunResultStore(), RunResultStore)


@pytest.mark.asyncio
async def test_retrieved_mutation_does_not_mutate_stored():
    store = InMemoryRunResultStore()
    await store.put(_make(metrics={"x": 1}))
    got1 = await store.get("run-1", tenant_id="ten-1", workspace_id="ws-1")
    got1.metrics["x"] = 999
    got1.failures.append({"injected": True})
    got2 = await store.get("run-1", tenant_id="ten-1", workspace_id="ws-1")
    assert got2.metrics == {"x": 1} and got2.failures == []


@pytest.mark.asyncio
async def test_input_mutation_after_put_does_not_mutate_stored():
    store = InMemoryRunResultStore()
    r = _make(metrics={"x": 1})
    await store.put(r)
    r.metrics["x"] = 999
    got = await store.get("run-1", tenant_id="ten-1", workspace_id="ws-1")
    assert got.metrics == {"x": 1}
