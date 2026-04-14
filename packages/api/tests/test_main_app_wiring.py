"""Week 6a Turn 1: app wiring + correlation ID middleware tests (v1.2.2 §11.1)."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.errors import APIError
from src.main import CORRELATION_HEADER, create_app


def _build_app_with_test_routes() -> FastAPI:
    app = create_app()

    @app.get("/_test/echo")
    async def echo(request: Request):
        return {"correlation_id": request.state.correlation_id}

    @app.get("/_test/raise")
    async def raise_api_error():
        raise APIError("boom", code="test_code", http_status=418)

    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app_with_test_routes()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_app_boots_and_openapi_generates(app: FastAPI):
    schema = app.openapi()
    assert schema["info"]["title"] == "AI SimTest Control Plane API"
    assert "paths" in schema


def test_correlation_id_generated_when_absent(client: TestClient):
    resp = client.get("/_test/echo")
    assert resp.status_code == 200, resp.text
    cid = resp.headers.get(CORRELATION_HEADER)
    assert cid is not None
    uuid.UUID(cid)
    assert resp.json()["correlation_id"] == cid


def test_correlation_id_propagated_when_provided(client: TestClient):
    provided = "test-correlation-12345"
    resp = client.get("/_test/echo", headers={CORRELATION_HEADER: provided})
    assert resp.status_code == 200, resp.text
    assert resp.headers[CORRELATION_HEADER] == provided
    assert resp.json()["correlation_id"] == provided


def test_api_error_handler_returns_canonical_envelope(client: TestClient):
    resp = client.get("/_test/raise", headers={CORRELATION_HEADER: "corr-1"})
    assert resp.status_code == 418
    body = resp.json()
    assert body["error"]["code"] == "test_code"
    assert body["error"]["message"] == "boom"
    assert body["error"]["correlation_id"] == "corr-1"
    assert body["error"]["details"] == {}
