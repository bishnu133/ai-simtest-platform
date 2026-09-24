"""
Multi-Model Comparison — Security & Governance.

Provides:
- RedactingFormatter: Log formatter that strips secrets
- SafeSerializer: JSON/dict serializer that strips secrets before export
- AuditLogger: Structured audit trail for comparison runs
- Artifact validation: Scan-before-write defense-in-depth
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_simtest_engine.multi_compare.config_loader import (
    SECRET_PATTERNS,
    contains_secrets,
    redact_secrets,
)


# ─────────────────────────────────────────────────────────────
# Redacting Log Formatter
# ─────────────────────────────────────────────────────────────

class RedactingFormatter(logging.Formatter):
    """
    Log formatter that replaces known API key patterns with [REDACTED].

    Attach to any logger used by multi-compare modules:
        handler.setFormatter(RedactingFormatter(...))
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
    ):
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_secrets(original)


# ─────────────────────────────────────────────────────────────
# Safe Serializer
# ─────────────────────────────────────────────────────────────

class SafeSerializer:
    """
    Serializer that strips secrets from dicts/JSON before writing.

    Defense-in-depth: even if a secret somehow reaches the output
    stage, SafeSerializer will catch and redact it.
    """

    # Field names that should never appear in exports
    FORBIDDEN_FIELDS = frozenset({
        "api_key", "api_secret", "secret_key", "password",
        "token", "access_token", "refresh_token",
    })

    @classmethod
    def sanitize_dict(cls, data: dict[str, Any]) -> dict[str, Any]:
        """
        Recursively sanitize a dictionary:
        1. Remove forbidden field names
        2. Redact string values matching secret patterns
        """
        if not isinstance(data, dict):
            return data

        result = {}
        for key, value in data.items():
            # Skip forbidden fields entirely
            if key.lower() in cls.FORBIDDEN_FIELDS:
                continue

            if isinstance(value, dict):
                result[key] = cls.sanitize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    cls.sanitize_dict(item) if isinstance(item, dict)
                    else (redact_secrets(item) if isinstance(item, str) else item)
                    for item in value
                ]
            elif isinstance(value, str):
                result[key] = redact_secrets(value)
            else:
                result[key] = value

        return result

    @classmethod
    def to_json(cls, data: dict[str, Any], **kwargs) -> str:
        """Serialize to JSON with secret redaction."""
        sanitized = cls.sanitize_dict(data)
        return json.dumps(sanitized, indent=2, default=str, **kwargs)

    @classmethod
    def write_json(cls, data: dict[str, Any], path: str | Path) -> Path:
        """
        Write sanitized JSON to file.

        Performs scan-before-write: validates the final output
        contains no secrets before committing to disk.

        Raises:
            SecurityError: If secrets are detected after sanitization
                          (should never happen, but defense-in-depth).
        """
        sanitized = cls.sanitize_dict(data)
        json_str = json.dumps(sanitized, indent=2, default=str)

        # Scan-before-write: final defense
        if contains_secrets(json_str):
            raise SecurityError(
                f"SECURITY: Secrets detected in output after sanitization. "
                f"File NOT written to {path}. This is a bug — please report it."
            )

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_str, encoding="utf-8")
        return output_path


class SecurityError(Exception):
    """Raised when a security invariant is violated."""
    pass


# ─────────────────────────────────────────────────────────────
# Artifact Validator
# ─────────────────────────────────────────────────────────────

def validate_artifact(content: str, artifact_name: str = "artifact") -> bool:
    """
    Validate that an artifact (HTML, JSON, text) contains no secrets.

    Args:
        content: The text content to validate.
        artifact_name: Name for error messages.

    Returns:
        True if clean, raises SecurityError if secrets found.
    """
    if contains_secrets(content):
        raise SecurityError(
            f"SECURITY: Secret patterns detected in {artifact_name}. "
            f"Content will not be written. This is a bug — please report it."
        )
    return True


# ─────────────────────────────────────────────────────────────
# Audit Logger
# ─────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Structured audit trail for multi-model comparison runs.

    Records key events with timestamps for compliance reporting.
    """

    def __init__(self):
        self._entries: list[dict[str, Any]] = []

    def log(self, event: str, details: dict[str, Any] | None = None) -> None:
        """Record an audit event."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "event": event,
        }
        if details:
            # Sanitize details before logging
            entry["details"] = SafeSerializer.sanitize_dict(details)
        self._entries.append(entry)

    def get_entries(self) -> list[dict[str, Any]]:
        """Return all audit entries."""
        return list(self._entries)

    def to_dict(self) -> dict[str, Any]:
        """Export as serializable dict."""
        return {
            "audit_trail": self._entries,
            "entry_count": len(self._entries),
        }


# ─────────────────────────────────────────────────────────────
# Notification Payload Sanitizer
# ─────────────────────────────────────────────────────────────

def sanitize_notification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize a notification payload before sending.

    Ensures no API keys, raw responses, or full conversation
    transcripts appear in webhook/Slack/Teams/email payloads.
    """
    sanitized = SafeSerializer.sanitize_dict(payload)

    # Additional notification-specific stripping
    for key in list(sanitized.keys()):
        lower_key = key.lower()
        if any(term in lower_key for term in [
            "raw_response", "full_transcript", "conversation_text",
            "response_body", "request_body",
        ]):
            sanitized.pop(key, None)

    return sanitized
