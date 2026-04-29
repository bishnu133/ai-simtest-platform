"""``SafeJSONB`` Pydantic type for user-supplied JSONB fields.

Per v0.6 §5 of the Turn 2 plan, this module defines a single Annotated type
that opts a Pydantic field into automatic secret-key scanning via the
``check_no_secrets`` recursive walker from ``src.secrets.denylist``.

Usage on a request model:

    from src.secrets import SafeJSONB

    class AssetCreateRequest(BaseModel):
        content: SafeJSONB = Field(default_factory=dict)
        tags: SafeJSONB | None = None

The validator runs **after** Pydantic's own dict-ness check, so by the time
``_validate_safe_jsonb`` is called we know we have a dict (or ``None`` for
optional fields, which we pass through). On a denylist hit, ``check_no_secrets``
raises ``SecretLeakDetected`` (a ``ValueError`` subclass), which Pydantic v2
folds into ``ValidationError``, which FastAPI then renders as HTTP 422 with
the ``field_path`` attached.

What this type does NOT do (per v0.6 §5.5):
  - Does not encrypt anything.
  - Does not scan values, only keys.
  - Does not run on service-generated fields. Per-field opt-in only.
  - Does not emit audit events. Turn 3 wires the FastAPI exception handler
    that consumes ``SecretLeakDetected.field_path`` and emits the four
    ``*_REJECTED_SECRET_LEAK`` events. Turn 2 only defines the constants.

Compatibility notes:
  - Works on ``dict[str, Any]``, ``dict[str, str]``, and other dict subtypes.
    The validator only walks keys, so the value-type annotation is preserved
    by Pydantic without interference.
  - For ``Optional[SafeJSONB]`` fields, the validator short-circuits on
    ``None``. This matches ``AssetUpdateRequest.content``, which is
    ``dict[str, Any] | None`` in the Week 6a tree.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import AfterValidator

from src.secrets.denylist import check_no_secrets


def _validate_safe_jsonb(v: Any) -> Any:
    """Pydantic AfterValidator: scan a dict (or pass through None) for denylisted keys.

    Pydantic has already coerced ``v`` to the field's declared type by the time
    we run, so for a ``dict[str, Any]`` field ``v`` is a dict, and for an
    ``Optional[dict]`` field ``v`` is either a dict or ``None``.
    """
    if v is None:
        return v
    check_no_secrets(v)
    return v


# The exported Annotated type. Field declarations use it like:
#
#     content: SafeJSONB = Field(default_factory=dict)
#
# Pydantic resolves ``SafeJSONB`` to ``dict[str, Any]`` for storage and runs
# ``_validate_safe_jsonb`` on the resulting dict after dict-ness validation.
SafeJSONB = Annotated[dict[str, Any], AfterValidator(_validate_safe_jsonb)]
