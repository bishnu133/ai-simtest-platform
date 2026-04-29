"""Application factory shim — delegates to ``src.app_factory.create_app``.

Turn 4 introduces a real composition root in ``src/app_factory.py``.
This module preserves the legacy ``src.main.create_app(...)`` entry
point as a thin shim so that:

* The two shipped ``test_main_app_wiring_auth_flag.py`` tests remain
  green without modification (zero-removal invariant).
* The four shipped ``test_main_app_wiring.py`` tests remain green —
  the ``app = create_app()`` module-level instance still exists.
* New code paths (production entry, integration tests, app_factory
  guardrail tests) call ``src.app_factory.create_app(settings=...)``
  directly and bypass this shim.

Behavioural contract preserved here
-----------------------------------
1. ``create_app()`` (no args) returns a FastAPI with
   ``CorrelationIdMiddleware`` registered and NO
   ``TenantContextMiddleware`` (Turn 2/3 backwards compat).
2. ``create_app(auth_enabled=True)`` raises ``NotImplementedError``
   containing the substring ``"Turn 4"``. The legacy ``main.create_app``
   path does NOT get production wiring; production callers must invoke
   ``src.app_factory.create_app(settings=AppSettings(auth_enabled=True, ...))``
   directly so guardrails (DB URL, real provider, etc.) are enforced.
3. ``CorrelationIdMiddleware`` lives in this module (it always has) so
   ``app_factory`` and any future caller can import it without taking
   on a dependency on ``src.main``'s ``create_app``.
"""
from __future__ import annotations

import uuid
from typing import Callable

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.errors import APIError, api_error_handler  # noqa: F401  (re-export)


CORRELATION_HEADER = "X-Correlation-Id"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware: set or echo a correlation ID on every request.

    Per v1.2.2 §11.1, every endpoint inherits correlation ID propagation
    automatically. The ID is also echoed on the response so clients can
    correlate logs.
    """

    async def dispatch(
        self, request: Request, call_next: Callable
    ):  # pragma: no cover — mechanics
        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        ctx = getattr(request.state, "tenant_context", None)
        if ctx is not None and getattr(ctx, "correlation_id", None) is None:
            try:
                request.state.tenant_context = ctx.model_copy(
                    update={"correlation_id": correlation_id}
                )
            except Exception:
                pass

        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def create_app(*, auth_enabled: bool = False) -> FastAPI:
    """Legacy entry point — preserved for backwards compat.

    Turn 4 production code uses ``src.app_factory.create_app(settings=...)``
    instead. This shim:

    * On ``auth_enabled=True`` — raises ``NotImplementedError`` (the
      production composition root requires explicit AppSettings; calling
      this shim with auth_enabled=True is a misconfiguration).
    * On ``auth_enabled=False`` (the default) — delegates to
      ``src.app_factory.create_app`` with a test-shaped AppSettings,
      preserving the exact middleware stack and route surface that the
      shipped 264-test baseline expects.
    """
    if auth_enabled:
        # Production callers must use src.app_factory.create_app(settings=...)
        # directly so AppSettings guardrails (real provider, real DB URL,
        # etc.) are enforced. The legacy shim path stays explicitly
        # opt-out of production wiring. The substring "Turn 4" is asserted
        # by tests/test_main_app_wiring_auth_flag.py.
        raise NotImplementedError(
            "create_app(auth_enabled=True) requires the Turn 4 composition "
            "root (src/app_factory.py) with explicit AppSettings. "
            "Call `src.app_factory.create_app(settings=AppSettings(...))` "
            "directly so production guardrails are enforced."
        )

    # Local import to avoid circular import at module load
    # (src.app_factory imports CorrelationIdMiddleware from this module).
    from src.app_factory import create_app as factory_create_app
    from src.config import AppSettings

    return factory_create_app(
        settings=AppSettings(
            app_env="test",
            auth_provider="dev",
            auth_enabled=False,
        )
    )


# Module-level app instance — preserved for any code that does
# `from src.main import app`. Tests that import `app` directly continue
# to work.
app = create_app()
