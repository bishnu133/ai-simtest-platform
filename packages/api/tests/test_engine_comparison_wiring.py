"""B.5b-i — engine comparison provider wiring + R-4/R-6 startup guards."""
from __future__ import annotations

import sys

import pytest

from src.app_factory import (
    FatalConfigurationError,
    _build_comparison_provider,
    _enforce_production_guardrails,
)
from src.comparisons.engine_provider import EngineComparisonProvider
from src.comparisons.provider import FailClosedComparisonProvider
from src.config import AppSettings
from src.runs.repository import InMemoryRunResultStore


def _settings(**ov):
    base = dict(app_env="test")
    base.update(ov)
    return AppSettings(**base)


# ---- builder swap ----

def test_builder_default_is_failclosed():
    p = _build_comparison_provider(_settings(), InMemoryRunResultStore())
    assert isinstance(p, FailClosedComparisonProvider)


def test_builder_flag_off_returns_failclosed():
    p = _build_comparison_provider(
        _settings(use_engine_comparison_provider=False), InMemoryRunResultStore())
    assert isinstance(p, FailClosedComparisonProvider)


def test_builder_flag_on_returns_engine_provider():
    p = _build_comparison_provider(
        _settings(use_engine_comparison_provider=True), InMemoryRunResultStore())
    assert isinstance(p, EngineComparisonProvider)


# ---- guardrails ----

def test_guard_flag_off_no_error():
    _enforce_production_guardrails(_settings(use_engine_comparison_provider=False))


def test_guard_flag_on_dev_env_ok():
    # engine installed + dev/test env -> no raise
    _enforce_production_guardrails(
        _settings(use_engine_comparison_provider=True, app_env="test"))


def test_guard_r4_missing_engine_is_fatal(monkeypatch):
    # Reliable even if the module was imported earlier: a None entry in
    # sys.modules forces `import ...` to raise ImportError (a plain del would
    # let it re-import successfully).
    monkeypatch.setitem(sys.modules, "ai_simtest_engine.core.comparison_engine", None)
    with pytest.raises(FatalConfigurationError) as ei:
        _enforce_production_guardrails(
            _settings(use_engine_comparison_provider=True, app_env="test"))
    assert "ai_simtest_engine" in str(ei.value)


def test_guard_r6_forbids_flag_in_staging():
    # staging + flag on -> R-6 fatal (engine present, so it is R-6 not R-4)
    with pytest.raises(FatalConfigurationError) as ei:
        _enforce_production_guardrails(_settings(
            use_engine_comparison_provider=True, app_env="staging",
            database_url="postgresql+asyncpg://u:p@localhost/db"))
    assert "use_engine_comparison_provider" in str(ei.value)
