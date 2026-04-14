"""FastAPI application entry point for the AI SimTest control plane API.

Wires:
  - Correlation ID middleware (v1.2.2 §11.1): reads X-Correlation-Id from
    incoming requests, generates UUIDv4 if absent, attaches to
    request.state.correlation_id, and echoes on every response.
  - Typed APIError exception handler from src.api.errors.
  - Routers for assets (Week 5), and Week 6a routers as they land.

Week 6a Turn 1 mounts only the foundation. Conversations / results /
comparisons routers land in Turns 2 and 3.
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.errors import APIError, api_error_handler

CORRELATION_HEADER = "X-Correlation-Id"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Read or generate X-Correlation-Id and attach to request state.

    Per v1.2.2 §11.1, every endpoint inherits correlation ID propagation
    automatically. The ID is also echoed on the response so clients can
    correlate logs.
    """

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # Also propagate into TenantContext if one was set by an earlier
        # middleware. This makes audit events automatically correlation-aware.
        ctx = getattr(request.state, "tenant_context", None)
        if ctx is not None and getattr(ctx, "correlation_id", None) is None:
            try:
                # TenantContext is a Pydantic model — model_copy is safe.
                request.state.tenant_context = ctx.model_copy(
                    update={"correlation_id": correlation_id}
                )
            except Exception:
                pass

        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def create_app() -> FastAPI:
    """Application factory. Used by tests and production entry points."""
    app = FastAPI(
        title="AI SimTest Control Plane API",
        version="0.6.0a1",
        description="Week 6a foundation — runs, results, comparisons.",
    )

    app.add_middleware(CorrelationIdMiddleware)
    app.add_exception_handler(APIError, api_error_handler)

    # Wire the dashboard cache invalidator into RunStateTransition so every
    # run status mutation invalidates the cache (v1.2.2 §M1).
    from src.results.cache import dashboard_cache
    from src.runs.service import set_dashboard_invalidator

    set_dashboard_invalidator(dashboard_cache.invalidate)

    # Mount Turn 2 routers
    from src.conversations.router import router as conversations_router
    from src.results.router import router as results_router

    app.include_router(conversations_router, prefix="/v1")
    app.include_router(results_router, prefix="/v1")

    # Mount Turn 3 router
    from src.comparisons.router import router as comparisons_router

    app.include_router(comparisons_router, prefix="/v1")

    return app


app = create_app()
