"""Denylist of credential-shaped keys that must not appear in user-supplied JSONB.

Per sprint plan §2.8 and v0.6 §5 of the Turn 2 plan, this module defines:

  - ``DENYLIST``: the canonical frozenset of forbidden key names (case-insensitive)
  - ``check_no_secrets(content, path)``: recursive scanner that raises on hit
  - ``SecretLeakDetected``: the exception type Pydantic re-wraps as ValidationError → 422

The scanner walks dicts and lists recursively. Keys are lowercased before
comparison; matching is case-insensitive. Path tracking yields actionable
error messages like ``bot.headers.authorization``.

What this module does NOT do (explicit non-goals from v0.6 §5.5):

  - No value scanning. ``{"endpoint_url": "Bearer eyJ..."}`` is accepted because
    the key is not in the denylist. Value-level entropy heuristics are a separate
    high-false-positive problem outside Turn 2 scope.
  - No encryption. Turn 2 enforces *rules* about secrets, not their storage.
  - No audit emission. Turn 3 wires the FastAPI exception handler that emits
    the four ``*_REJECTED_SECRET_LEAK`` audit events; Turn 2 only defines the
    constants in ``src/audit/logger.py::AuditActions``.
  - No Turn-2 enforcement on service-generated fields (e.g. ``RunRecord.metadata``).
    Only fields explicitly annotated with ``SafeJSONB`` are scanned, and per
    v0.6 §5.5 the annotation is per-field opt-in.
"""
from __future__ import annotations

from typing import Any

# Canonical denylist of credential-shaped keys. Case-insensitive matching.
# All comparisons go through lowercase, so this set holds lowercase forms.
#
# Keep this list short and high-signal. Adding marginal keys (e.g. "key" alone)
# would catch too many false positives in legitimate config dicts.
DENYLIST: frozenset[str] = frozenset({
    "authorization",
    "auth",
    "api_key",
    "api-key",
    "apikey",
    "secret",
    "secret_key",
    "secret-key",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "bearer",
    "bearer_token",
    "private_key",
    "privatekey",
    "client_secret",
    "client-secret",
    "x-api-key",
    "x-auth-token",
    "credentials",
    "credential",
})


class SecretLeakDetected(ValueError):
    """Raised when ``check_no_secrets`` finds a denylisted key.

    Subclasses ``ValueError`` so that Pydantic v2's ``AfterValidator`` folds it
    into the standard ``ValidationError`` flow, which FastAPI then renders as
    HTTP 422 with the field path attached.

    Attributes:
        field_path: dotted path to the offending key, e.g. ``bot.headers.authorization``
            or ``configs[0].password`` for list-of-dict cases. Used by the Turn 3
            audit emission handler to populate the ``field_path`` audit event field.
    """

    def __init__(self, message: str, field_path: str = "") -> None:
        super().__init__(message)
        self.field_path = field_path


def check_no_secrets(content: Any, path: str = "") -> None:
    """Recursively inspect ``content`` and raise on any denylisted key.

    Walks dicts and lists. Non-collection values (str, int, bool, etc.) are
    passed through without inspection — see the value-scanning non-goal above.

    Args:
        content: the value to inspect. Top-level call typically passes a dict;
            recursion handles nested dicts and lists transparently. Non-dict
            top-level values are accepted (no-op).
        path: the dotted path accumulated so far, used to build the error
            message. Callers should pass ``""`` (the default).

    Raises:
        SecretLeakDetected: on the first denylisted key encountered. The
            exception's ``field_path`` attribute carries the full path.
    """
    if not isinstance(content, dict):
        return
    for key, value in content.items():
        key_lower = key.lower() if isinstance(key, str) else ""
        if key_lower in DENYLIST:
            full_path = f"{path}.{key}" if path else key
            raise SecretLeakDetected(
                f"Denylisted key '{key}' found at path '{full_path}'",
                field_path=full_path,
            )
        if isinstance(value, dict):
            sub_path = f"{path}.{key}" if path else key
            check_no_secrets(value, sub_path)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    sub_path = (
                        f"{path}.{key}[{i}]" if path else f"{key}[{i}]"
                    )
                    check_no_secrets(item, sub_path)
