"""Secret-handling guardrails for user-supplied JSONB content.

This package implements the v0.6 §5 SafeJSONB validation layer for Turn 2:
prevent denylisted credential keys from entering any user-writable JSONB
field in an API request model.

Audit emission for rejection events is **deferred to Turn 3** — Turn 2 only
defines the four event code constants in `src/audit/logger.py::AuditActions`.
"""
from src.secrets.denylist import (
    DENYLIST,
    SecretLeakDetected,
    check_no_secrets,
)
from src.secrets.safe_jsonb import SafeJSONB

__all__ = [
    "DENYLIST",
    "SecretLeakDetected",
    "check_no_secrets",
    "SafeJSONB",
]
