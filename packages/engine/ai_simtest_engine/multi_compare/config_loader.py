"""
Multi-Model Comparison — Config Loader.

Parses models.yaml, resolves environment variables for API keys,
validates endpoint connectivity, enforces endpoint allowlist,
and produces a validated MultiCompareConfig.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

import yaml

from ai_simtest_engine.multi_compare.models import (
    AdapterType,
    MultiCompareConfig,
    ModelSpec,
    ComparisonSettings,
    RunManifest,
)


# ─────────────────────────────────────────────────────────────
# Secret Patterns for Redaction
# ─────────────────────────────────────────────────────────────

SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),        # OpenAI
    re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),   # Anthropic
    re.compile(r"claude-[a-zA-Z0-9]{20,}"),      # Anthropic alt
    re.compile(r"AKIA[A-Z0-9]{16}"),             # AWS
    re.compile(r"xoxb-[a-zA-Z0-9\-]+"),          # Slack
    re.compile(r"Bearer\s+[a-zA-Z0-9\-_.]+"),    # Bearer tokens
    re.compile(r"gsk_[a-zA-Z0-9]{20,}"),         # Groq
    re.compile(r"AIza[a-zA-Z0-9\-_]{30,}"),      # Google
]

ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class ConfigLoadError(Exception):
    """Raised when config loading fails."""
    pass


class EndpointNotAllowedError(ConfigLoadError):
    """Raised when an endpoint violates the allowlist."""
    pass


class SecretResolutionError(ConfigLoadError):
    """Raised when an environment variable for an API key is not set."""
    pass


class ConfigLoadResult:
    """
    Structured result from config loading.

    Fixes the contract: load_multi_compare_config() always returns
    this object, never a bare tuple.
    """

    def __init__(
        self,
        config: MultiCompareConfig,
        warnings: list[str],
        config_hash: str = "",
    ):
        self.config = config
        self.warnings = warnings
        self.config_hash = config_hash

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0



# ─────────────────────────────────────────────────────────────
# Config Loader
# ─────────────────────────────────────────────────────────────

def load_multi_compare_config(path: str | Path) -> ConfigLoadResult:
    """
    Load and validate a multi-model comparison config from YAML.

    This function validates config SHAPE only — it does NOT test
    endpoint connectivity. Use ``preflight_check_endpoints()``
    separately before execution to verify endpoints are reachable.

    Steps:
    1. Parse YAML file
    2. Resolve environment variables in headers
    3. Resolve api_key_env to api_key (runtime, never serialized)
    4. Validate endpoint allowlist
    5. Validate adapter + response_path consistency
    6. Emit adapter warnings
    7. Return ConfigLoadResult

    Args:
        path: Path to models.yaml config file.

    Returns:
        ConfigLoadResult with config, warnings, and config_hash.

    Raises:
        ConfigLoadError: On invalid config.
        FileNotFoundError: If config file doesn't exist.
        SecretResolutionError: If required env vars are missing.
        EndpointNotAllowedError: If endpoint violates allowlist.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_content = config_path.read_text(encoding="utf-8")

    try:
        raw = yaml.safe_load(raw_content)
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Invalid YAML in {config_path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigLoadError(
            f"Config file must be a YAML mapping, got {type(raw).__name__}"
        )

    warnings: list[str] = []

    # ── Parse top-level fields ────────────────────────────────
    comparison = raw.get("comparison", {})
    if not isinstance(comparison, dict):
        comparison = {}

    raw_models = raw.get("models", [])
    if not isinstance(raw_models, list) or len(raw_models) < 2:
        raise ConfigLoadError(
            f"Config must define at least 2 models under 'models:' key. "
            f"Found {len(raw_models) if isinstance(raw_models, list) else 0}."
        )

    raw_settings = raw.get("settings", {})
    if not isinstance(raw_settings, dict):
        raw_settings = {}

    # ── Parse model specs ─────────────────────────────────────
    model_specs: list[ModelSpec] = []
    for i, rm in enumerate(raw_models):
        if not isinstance(rm, dict):
            raise ConfigLoadError(f"Model #{i+1} must be a YAML mapping")

        if "id" not in rm:
            raise ConfigLoadError(f"Model #{i+1} is missing required 'id' field")

        # Resolve env vars in headers
        headers = rm.get("headers", {})
        if isinstance(headers, dict):
            headers = _resolve_env_vars_in_dict(headers)

        spec = ModelSpec(
            id=rm["id"],
            name=rm.get("name", rm["id"]),
            endpoint=rm.get("endpoint", ""),
            adapter=AdapterType(rm.get("adapter", "auto")),
            api_key_env=rm.get("api_key_env"),
            headers=headers,
            request_format=rm.get("request_format"),
            response_path=rm.get("response_path"),
            token_usage_path=rm.get("token_usage_path"),
            request_template=rm.get("request_template"),
            timeout_seconds=rm.get("timeout_seconds", 30),
            verify_ssl=rm.get("verify_ssl", True),
            tags=rm.get("tags", []),
            metadata=rm.get("metadata", {}),
        )

        # Resolve API key from environment
        if spec.api_key_env:
            key_value = os.environ.get(spec.api_key_env)
            if not key_value:
                raise SecretResolutionError(
                    f"Model '{spec.id}': Environment variable '{spec.api_key_env}' "
                    f"is not set. Set it before running multi-compare."
                )
            spec.api_key = key_value

        # Adapter warnings
        if spec.adapter == AdapterType.AUTO:
            warnings.append(
                f"Model '{spec.id}': No explicit adapter specified. "
                f"Defaulting to auto-detect. For production use, set "
                f"'adapter: openai|anthropic|generic' explicitly."
            )

        # Generic adapter validation
        if spec.adapter == AdapterType.GENERIC and not spec.response_path:
            raise ConfigLoadError(
                f"Model '{spec.id}' uses generic adapter but no 'response_path' "
                f"is configured. Set response_path to a JSONPath expression "
                f"pointing to the bot's reply text (e.g., 'data.response.text')."
            )

        if not spec.endpoint:
            raise ConfigLoadError(f"Model '{spec.id}' is missing 'endpoint' URL.")

        model_specs.append(spec)

    # ── Parse settings ────────────────────────────────────────
    settings = ComparisonSettings(**{
        k: v for k, v in raw_settings.items()
        if k in ComparisonSettings.model_fields
    })

    # ── Parse allowlist ───────────────────────────────────────
    allowed_endpoints = raw.get("allowed_endpoints") or comparison.get("allowed_endpoints")

    # ── Build config ──────────────────────────────────────────
    config = MultiCompareConfig(
        name=comparison.get("name", "Multi-Model Comparison"),
        description=comparison.get("description", ""),
        mode=comparison.get("mode", "champion_challenger"),
        champion=comparison.get("champion") or comparison.get("baseline"),
        models=model_specs,
        settings=settings,
        decision_profile=comparison.get("decision_profile", "balanced"),
        decision_weights=comparison.get("decision_weights"),
        output_dir=comparison.get("output_dir", raw.get("output_dir", "./reports/multi_compare")),
        allowed_endpoints=allowed_endpoints,
        version=comparison.get("version", "1.0"),
    )

    # ── Validate endpoint allowlist ───────────────────────────
    if config.allowed_endpoints:
        _validate_endpoint_allowlist(config.models, config.allowed_endpoints)

    return ConfigLoadResult(
        config=config,
        warnings=warnings,
        config_hash=RunManifest.compute_hash(raw_content),
    )


def _resolve_env_vars_in_dict(d: dict[str, str]) -> dict[str, str]:
    """Resolve ${ENV_VAR} patterns in header values."""
    resolved = {}
    for key, value in d.items():
        if isinstance(value, str):
            resolved[key] = ENV_VAR_PATTERN.sub(
                lambda m: os.environ.get(m.group(1), m.group(0)),
                value,
            )
        else:
            resolved[key] = value
    return resolved


def _validate_endpoint_allowlist(
    models: list[ModelSpec],
    allowed: list[str],
) -> None:
    """
    Check that every model's endpoint matches at least one allowlist pattern.

    Patterns support glob-style matching:
    - "*.example.com" matches "api.example.com"
    - "api.openai.com" matches exactly
    """
    for model in models:
        from urllib.parse import urlparse
        parsed = urlparse(model.endpoint)
        hostname = parsed.hostname or ""

        matched = any(
            fnmatch.fnmatch(hostname, pattern.lstrip("*.") if pattern.startswith("*.") else pattern)
            or fnmatch.fnmatch(hostname, pattern)
            for pattern in allowed
        )

        if not matched:
            raise EndpointNotAllowedError(
                f"Model '{model.id}': Endpoint '{model.endpoint}' "
                f"(hostname: {hostname}) is not in the allowed endpoints list: "
                f"{allowed}. Add it to 'allowed_endpoints' in your config, "
                f"or remove the allowlist to allow all endpoints."
            )


# ─────────────────────────────────────────────────────────────
# Security Utilities
# ─────────────────────────────────────────────────────────────

def redact_secrets(text: str) -> str:
    """
    Replace any detected secret patterns with [REDACTED].

    Used by SafeSerializer and RedactingFormatter.
    """
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def contains_secrets(text: str) -> bool:
    """Check if text contains any known secret patterns."""
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def redact_model_spec(spec: ModelSpec) -> dict[str, Any]:
    """
    Serialize a ModelSpec with all secrets removed.

    Used for run_manifest.json and audit exports.
    """
    data = spec.model_dump(exclude={"api_key"})
    # Also redact api_key_env value (keep the env var name)
    # and any secrets that may be in headers
    if "headers" in data and isinstance(data["headers"], dict):
        for key, value in data["headers"].items():
            if isinstance(value, str) and contains_secrets(value):
                data["headers"][key] = "[REDACTED]"
    return data


def compute_config_hash(config_path: str | Path) -> str:
    """Compute SHA-256 hash of the raw config file content."""
    content = Path(config_path).read_text(encoding="utf-8")
    return RunManifest.compute_hash(content)


# ─────────────────────────────────────────────────────────────
# Preflight Endpoint Connectivity Check
# ─────────────────────────────────────────────────────────────

class PreflightResult:
    """Result of preflight endpoint connectivity checks."""

    def __init__(self):
        self.results: dict[str, dict] = {}  # model_id → {reachable, status_code, error, latency_ms}

    def add(self, model_id: str, reachable: bool, status_code: int | None = None,
            error: str = "", latency_ms: float = 0.0) -> None:
        self.results[model_id] = {
            "reachable": reachable,
            "status_code": status_code,
            "error": error,
            "latency_ms": round(latency_ms, 1),
        }

    @property
    def all_reachable(self) -> bool:
        return all(r["reachable"] for r in self.results.values())

    @property
    def unreachable_models(self) -> list[str]:
        return [mid for mid, r in self.results.items() if not r["reachable"]]

    def summary(self) -> str:
        ok = sum(1 for r in self.results.values() if r["reachable"])
        total = len(self.results)
        return f"{ok}/{total} endpoints reachable"


async def preflight_check_endpoints(
    config: MultiCompareConfig,
    timeout_seconds: float = 10.0,
) -> PreflightResult:
    """
    Test connectivity to all model endpoints before starting execution.

    Sends a lightweight request (POST with minimal body) to each endpoint
    to verify reachability. This is NOT a full API validation — it only
    checks that the endpoint responds to HTTP requests.

    Called AFTER config loading, BEFORE model execution.

    Args:
        config: Validated MultiCompareConfig.
        timeout_seconds: Timeout for each connectivity check.

    Returns:
        PreflightResult with per-model reachability status.
    """
    import httpx
    import time

    result = PreflightResult()

    for model in config.models:
        start = time.monotonic()
        try:
            headers = {"Content-Type": "application/json", **model.headers}
            if model.api_key:
                headers["Authorization"] = f"Bearer {model.api_key}"

            async with httpx.AsyncClient(
                verify=model.verify_ssl,
                timeout=httpx.Timeout(timeout_seconds),
            ) as client:
                # Send minimal probe request
                response = await client.post(
                    model.endpoint,
                    json={"messages": [{"role": "user", "content": "ping"}]},
                    headers=headers,
                )
                latency = (time.monotonic() - start) * 1000

                # Any response (even 4xx) means the endpoint is reachable
                # Only connection failures / timeouts mean unreachable
                result.add(
                    model_id=model.id,
                    reachable=True,
                    status_code=response.status_code,
                    latency_ms=latency,
                )

        except httpx.TimeoutException:
            latency = (time.monotonic() - start) * 1000
            result.add(
                model_id=model.id,
                reachable=False,
                error=f"Timeout after {timeout_seconds}s. Check endpoint URL and network.",
                latency_ms=latency,
            )
        except httpx.ConnectError as e:
            latency = (time.monotonic() - start) * 1000
            result.add(
                model_id=model.id,
                reachable=False,
                error=f"Connection failed: {str(e)[:200]}. Check endpoint URL and network.",
                latency_ms=latency,
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            result.add(
                model_id=model.id,
                reachable=False,
                error=f"Preflight error: {str(e)[:200]}",
                latency_ms=latency,
            )

    return result
