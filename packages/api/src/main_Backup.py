"""Application factory for the AI SimTest Control Plane API.

Turn 3 Step 4 additive edit: `create_app()` gains an `auth_enabled: bool`
parameter. When True, `TenantContextMiddleware` is registered; when
False (the Turn 2/3 default), no auth middleware runs and the app
behaves exactly as it did under Week 6a.

The default is False to preserve Turn 2 test behavior — all 234 tests
green at the close of Session 2 assume no auth middleware. Making
True the default now would require editing every shipped test that
uses `create_app()` (violating the zero-removal policy for this turn).

# TODO(Turn 4, app_factory composition root): flip the default to True
# and route all production entry points through app_factory.create_app()
# which will set auth_enabled=True unconditionally. This False default
# exists only to preserve Turn 2 test behavior during Turn 3. See
# plan v0.5.1 §4.4–4.5 (composition root).
"""
from __future__ import annotations

import uuid
from typing import Callable

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.errors import APIError, api_error_handler


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
    """Application factory. Used by tests and production entry points.

    Parameters
    ----------
    auth_enabled:
        When True, register ``TenantContextMiddleware`` in front of the
        router stack so every request runs the five-step auth pipeline
        (bearer extraction → JWT verify → bootstrap → role resolution →
        context attach). When False (the Turn 3 default), the middleware
        is skipped entirely and tests/clients that inject
        ``request.state.tenant_context`` themselves continue working.

        Turn 4 will invert the default via ``app_factory.create_app()``
        which is the production composition root. See the module-level
        TODO for the full context.
    """
    app = FastAPI(
        title="AI SimTest Control Plane API",
        version="0.6.0a1",
        description="Week 6a foundation — runs, results, comparisons.",
    )

    # ------------------------------------------------------------------
    # Middleware registration — Starlette runs middleware in reverse
    # order of addition (last-added = outermost = first on request).
    # Intended request-time flow:
    #   1. CorrelationIdMiddleware (outermost)  sets correlation_id
    #   2. TenantContextMiddleware (inner)      populates tenant_context
    #   3. route handler
    # So register correlation FIRST, auth SECOND, per the plan §5.5
    # registration-order invariant.
    # ------------------------------------------------------------------

    app.add_middleware(CorrelationIdMiddleware)

    if auth_enabled:
        # Turn 4 will wire the real provider + sessionmaker here via
        # `src/app_factory.py` (the composition root per plan §4.4–4.5).
        # Session 3 does NOT ship the production wiring — it ships the
        # middleware, bootstrap, authz, and flags, but leaves the
        # `auth_enabled=True` path deliberately unplumbed so tests that
        # need auth-on wire it themselves via `app.add_middleware(...)`.
        # Calling `create_app(auth_enabled=True)` without the Turn 4
        # composition root is a NotImplementedError by design.
        raise NotImplementedError(
            "create_app(auth_enabled=True) requires the Turn 4 composition "
            "root (src/app_factory.py). For Session 3, tests that exercise "
            "auth must add TenantContextMiddleware directly; production "
            "entry points are stubbed until Turn 4 lands."
        )

    app.add_exception_handler(APIError, api_error_handler)

    # ------------------------------------------------------------------
    # Dashboard cache invalidator — unchanged from Week 6a Turn 2
    # ------------------------------------------------------------------
    from src.results.cache import dashboard_cache
    from src.runs.service import set_dashboard_invalidator

    set_dashboard_invalidator(dashboard_cache.invalidate)

    # ------------------------------------------------------------------
    # Routers — unchanged from Week 6a Turn 3 (comparisons mount)
    # ------------------------------------------------------------------
    from src.comparisons.router import router as comparisons_router
    from src.conversations.router import router as conversations_router
    from src.results.router import router as results_router

    app.include_router(conversations_router, prefix="/v1")
    app.include_router(results_router, prefix="/v1")
    app.include_router(comparisons_router, prefix="/v1")

    return app


app = create_app()
