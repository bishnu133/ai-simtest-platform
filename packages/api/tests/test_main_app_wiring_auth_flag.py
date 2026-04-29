"""Turn 3 Session 3 additive tests for main.create_app auth flag.

Two tests:
  1. Default `create_app()` does NOT register TenantContextMiddleware
     (backwards-compat invariant — Turn 2's 234 tests must remain green).
  2. `create_app(auth_enabled=True)` raises NotImplementedError until
     Turn 4 composition root lands — proves the flag exists and is
     plumbed through, without requiring the full production wiring.

These tests go in a SEPARATE file rather than editing the shipped
`test_main_app_wiring.py` so the existing 4-test suite stays untouched
(plan zero-removal invariant).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI

from src.main import create_app


def test_create_app_default_does_not_register_tenant_context_middleware():
    """Default create_app() must NOT register TenantContextMiddleware.

    This is the backwards-compat guard: any shipped test that builds
    its app via `create_app()` (currently only test_main_app_wiring.py)
    continues to see a middleware stack with exactly CorrelationIdMiddleware,
    not auth middleware on top.
    """
    app = create_app()
    assert isinstance(app, FastAPI)

    middleware_class_names = [m.cls.__name__ for m in app.user_middleware]
    assert "CorrelationIdMiddleware" in middleware_class_names
    assert "TenantContextMiddleware" not in middleware_class_names, (
        f"Turn 2 baseline must not include TenantContextMiddleware by "
        f"default; got stack: {middleware_class_names}"
    )


def test_create_app_auth_enabled_true_raises_until_turn_4():
    """Calling with auth_enabled=True should raise NotImplementedError.

    The flag is wired but the production composition root is Turn 4.
    This test documents the current state and will change when
    app_factory.create_app() lands.
    """
    with pytest.raises(NotImplementedError) as exc_info:
        create_app(auth_enabled=True)
    assert "Turn 4" in str(exc_info.value)
