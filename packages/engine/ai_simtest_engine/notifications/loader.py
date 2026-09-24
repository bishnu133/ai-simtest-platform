"""
Notification Config Loader — P4 #22

Loads NotificationConfig from YAML with:
  - Environment variable interpolation (${VAR_NAME})
  - Secret redaction in logs and error messages (Review #9)
  - Validation and lint mode (Review #16)

Secret handling rules (Review #9):
  - URLs are masked to scheme + host only in logs
  - SMTP passwords never logged
  - Bearer tokens show first 4 chars only
  - Config error messages describe the problem without echoing values
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from ai_simtest_engine.notifications.models import NotificationConfig

logger = logging.getLogger(__name__)

# Pattern for ${VAR_NAME} interpolation
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate_env_vars(value: str) -> str:
    """
    Replace ${VAR_NAME} placeholders with environment variable values.
    Raises ValueError if a referenced variable is not set.
    """
    def _replace(match):
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            raise ValueError(
                f"Environment variable '{var_name}' is not set "
                f"(referenced in notification config)"
            )
        return env_val
    return _ENV_PATTERN.sub(_replace, value)


def _interpolate_dict(data: dict) -> dict:
    """Recursively interpolate env vars in all string values."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _interpolate_env_vars(value)
        elif isinstance(value, dict):
            result[key] = _interpolate_dict(value)
        elif isinstance(value, list):
            result[key] = [
                _interpolate_dict(item) if isinstance(item, dict)
                else _interpolate_env_vars(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def redact_url(url: str) -> str:
    """Redact URL to scheme + host only for safe logging. Review #9."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}/***"
    except Exception:
        return "***redacted***"


def redact_token(token: str) -> str:
    """Show first 4 chars of token for safe logging. Review #9."""
    if not token or len(token) <= 4:
        return "***"
    return token[:4] + "***"


def load_notification_config(
    path: str | Path,
    interpolate: bool = True,
) -> NotificationConfig:
    """
    Load notification config from a YAML file.

    Args:
        path: Path to notify.yaml
        interpolate: If True, resolve ${VAR_NAME} from environment

    Returns:
        Validated NotificationConfig

    Raises:
        FileNotFoundError: If path doesn't exist
        ValueError: If YAML is invalid or env vars are missing
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Notification config not found: {path}")

    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML is required for notification config (pip install pyyaml)")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    if not raw_data or not isinstance(raw_data, dict):
        raise ValueError(f"Invalid notification config: expected YAML dict, got {type(raw_data).__name__}")

    # Interpolate environment variables
    if interpolate:
        try:
            raw_data = _interpolate_dict(raw_data)
        except ValueError as e:
            raise ValueError(str(e))

    config = NotificationConfig.from_dict(raw_data)
    return config


def lint_notification_config(
    path: str | Path,
) -> tuple[bool, list[str], list[str]]:
    """
    Validate notification config without sending anything.
    Review #16: simtest notify-lint

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        config = load_notification_config(path, interpolate=True)
    except FileNotFoundError as e:
        return False, [str(e)], []
    except ImportError as e:
        return False, [str(e)], []
    except ValueError as e:
        return False, [str(e)], []
    except Exception as e:
        return False, [f"Unexpected error loading config: {type(e).__name__}: {e}"], []

    # Structural validation
    validation_errors = config.validate()
    errors.extend(validation_errors)

    # Warnings
    if not config.enabled:
        warnings.append("Notifications are disabled (enabled: false)")

    for ch in config.channels:
        if not ch.events:
            warnings.append(f"Channel '{ch.name}' has no event filter — will receive ALL events")
        if ch.fallback_channel:
            fallback = config.get_channel(ch.fallback_channel)
            if fallback and fallback.type == ch.type:
                warnings.append(
                    f"Channel '{ch.name}' fallback '{ch.fallback_channel}' uses same channel type"
                )

    is_valid = len(errors) == 0
    return is_valid, errors, warnings
