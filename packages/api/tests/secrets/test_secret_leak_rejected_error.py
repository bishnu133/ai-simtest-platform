"""FH-Tier-2 Slice 2 (B.1): additive unit checks for SecretLeakRejected."""
from __future__ import annotations

from fastapi import status

from src.api.errors import ErrorCodes, SecretLeakRejected


def test_secret_leak_rejected_code_status_details():
    exc = SecretLeakRejected("denylisted key", details={"field_path": "config.api_key"})
    assert exc.code == ErrorCodes.SECRET_LEAK_DETECTED == "secret_leak_detected"
    assert exc.http_status == status.HTTP_422_UNPROCESSABLE_CONTENT == 422
    assert exc.details == {"field_path": "config.api_key"}
