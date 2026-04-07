"""
Multi-Model Comparison — Provider Adapter Layer.

Normalizes request/response handling across different API providers.
Each adapter extracts: response text, token counts, latency, errors,
and metadata into a CanonicalBotResponse.

Architecture:
- BaseProviderAdapter (ABC) defines the interface
- OpenAIAdapter handles /v1/chat/completions format
- AnthropicAdapter handles /v1/messages format
- GenericHTTPAdapter handles any custom API with configurable paths
- AdapterFactory selects the right adapter from ModelSpec

Integration:
- Adapters wrap the existing TargetBotClient — zero changes to
  ConversationSimulator. The orchestrator uses adapters to collect
  metadata that TargetBotClient doesn't capture (tokens, provider info).
"""

from __future__ import annotations

import time
import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

import httpx

from src.multi_compare.models import (
    AdapterType,
    CanonicalBotResponse,
    NormalizedError,
    ErrorCategory,
    ModelSpec,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Base Adapter
# ─────────────────────────────────────────────────────────────

class BaseProviderAdapter(ABC):
    """
    Abstract interface for provider adapters.

    Each adapter knows how to:
    1. Build a request body for the provider
    2. Parse the response into CanonicalBotResponse
    3. Extract token usage, latency, and error information
    """

    def __init__(self, model_spec: ModelSpec):
        self.model_spec = model_spec

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return provider identifier (e.g., 'openai', 'anthropic')."""
        ...

    @abstractmethod
    def build_request_body(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Build the HTTP request body for this provider."""
        ...

    @abstractmethod
    def parse_response(
        self,
        status_code: int,
        response_body: dict[str, Any] | str,
        latency_ms: float,
    ) -> CanonicalBotResponse:
        """Parse provider response into CanonicalBotResponse."""
        ...

    def normalize_error(
        self,
        status_code: int,
        response_body: Any,
        exception: Exception | None = None,
    ) -> NormalizedError:
        """Map HTTP errors to standardized NormalizedError."""
        if status_code == 429:
            return NormalizedError(
                category=ErrorCategory.RATE_LIMIT,
                message="Rate limited by provider",
                raw_status_code=status_code,
                retryable=True,
            )
        elif status_code in (401, 403):
            return NormalizedError(
                category=ErrorCategory.AUTH_ERROR,
                message=f"Authentication failed (HTTP {status_code})",
                raw_status_code=status_code,
                retryable=False,
            )
        elif status_code == 408 or (exception and "timeout" in str(exception).lower()):
            return NormalizedError(
                category=ErrorCategory.TIMEOUT,
                message="Request timed out",
                raw_status_code=status_code,
                retryable=True,
            )
        elif status_code >= 500:
            return NormalizedError(
                category=ErrorCategory.SERVER_ERROR,
                message=f"Server error (HTTP {status_code})",
                raw_status_code=status_code,
                retryable=True,
            )
        elif exception and "connect" in str(exception).lower():
            return NormalizedError(
                category=ErrorCategory.CONNECTION_ERROR,
                message=str(exception),
                retryable=True,
            )
        else:
            msg = ""
            if isinstance(response_body, dict):
                msg = response_body.get("error", {}).get("message", str(response_body)[:200])
            elif isinstance(response_body, str):
                msg = response_body[:200]
            return NormalizedError(
                category=ErrorCategory.UNKNOWN,
                message=msg or f"HTTP {status_code}",
                raw_status_code=status_code,
                retryable=False,
            )

    async def send_request(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> CanonicalBotResponse:
        """
        Send a request to the provider and return a normalized response.

        This is the primary method called by the orchestrator.
        """
        body = self.build_request_body(message, conversation_history)
        headers = {
            "Content-Type": "application/json",
            **self.model_spec.headers,
        }

        # Add auth header if API key available
        if self.model_spec.api_key:
            auth_header = self._get_auth_header()
            if auth_header:
                headers.update(auth_header)

        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(
                verify=self.model_spec.verify_ssl,
                timeout=httpx.Timeout(self.model_spec.timeout_seconds),
            ) as client:
                response = await client.post(
                    self.model_spec.endpoint,
                    json=body,
                    headers=headers,
                )
                latency_ms = (time.monotonic() - start_time) * 1000

                if response.status_code >= 400:
                    try:
                        resp_body = response.json()
                    except Exception:
                        resp_body = response.text
                    return CanonicalBotResponse(
                        content="",
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        error=self.normalize_error(response.status_code, resp_body),
                        provider_name=self.get_provider_name(),
                        raw_response=resp_body if isinstance(resp_body, dict) else {"text": str(resp_body)},
                    )

                try:
                    resp_json = response.json()
                except Exception:
                    return CanonicalBotResponse(
                        content="",
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        error=NormalizedError(
                            category=ErrorCategory.MALFORMED_RESPONSE,
                            message="Response is not valid JSON",
                            raw_status_code=response.status_code,
                        ),
                        provider_name=self.get_provider_name(),
                    )

                return self.parse_response(
                    status_code=response.status_code,
                    response_body=resp_json,
                    latency_ms=latency_ms,
                )

        except httpx.TimeoutException as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            return CanonicalBotResponse(
                content="",
                latency_ms=latency_ms,
                error=self.normalize_error(408, None, e),
                provider_name=self.get_provider_name(),
            )
        except httpx.ConnectError as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            return CanonicalBotResponse(
                content="",
                latency_ms=latency_ms,
                error=self.normalize_error(0, None, e),
                provider_name=self.get_provider_name(),
            )
        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            return CanonicalBotResponse(
                content="",
                latency_ms=latency_ms,
                error=NormalizedError(
                    category=ErrorCategory.UNKNOWN,
                    message=str(e)[:300],
                    retryable=False,
                ),
                provider_name=self.get_provider_name(),
            )

    def _get_auth_header(self) -> dict[str, str] | None:
        """Build provider-specific auth header."""
        return {"Authorization": f"Bearer {self.model_spec.api_key}"}


# ─────────────────────────────────────────────────────────────
# OpenAI Adapter
# ─────────────────────────────────────────────────────────────

class OpenAIAdapter(BaseProviderAdapter):
    """
    Adapter for OpenAI-compatible APIs.

    Handles /v1/chat/completions format.
    Works with: OpenAI, Azure OpenAI, Ollama (OpenAI-compatible),
    vLLM, LiteLLM proxy, any OpenAI-compatible endpoint.
    """

    def get_provider_name(self) -> str:
        return "openai"

    def build_request_body(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages = []
        if conversation_history:
            for turn in conversation_history:
                role = "assistant" if turn.get("speaker") == "bot" else "user"
                messages.append({"role": role, "content": turn.get("message", "")})
        messages.append({"role": "user", "content": message})

        body: dict[str, Any] = {"messages": messages}

        # Add model if specified in metadata
        model = self.model_spec.metadata.get("model")
        if model:
            body["model"] = model

        return body

    def parse_response(
        self,
        status_code: int,
        response_body: dict[str, Any] | str,
        latency_ms: float,
    ) -> CanonicalBotResponse:
        if isinstance(response_body, str):
            return CanonicalBotResponse(
                content="",
                status_code=status_code,
                latency_ms=latency_ms,
                error=NormalizedError(
                    category=ErrorCategory.MALFORMED_RESPONSE,
                    message="Expected JSON object, got string",
                    raw_status_code=status_code,
                ),
                provider_name=self.get_provider_name(),
            )

        # Extract content from OpenAI format
        content = ""
        try:
            choices = response_body.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
        except (IndexError, KeyError, TypeError):
            pass

        if not content and "error" in response_body:
            return CanonicalBotResponse(
                content="",
                status_code=status_code,
                latency_ms=latency_ms,
                error=NormalizedError(
                    category=ErrorCategory.MALFORMED_RESPONSE,
                    message=response_body["error"].get("message", "Unknown error")
                            if isinstance(response_body["error"], dict)
                            else str(response_body["error"]),
                    raw_status_code=status_code,
                ),
                provider_name=self.get_provider_name(),
                raw_response=response_body,
            )

        # Extract token usage
        usage = response_body.get("usage", {})
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")

        # Extract metadata
        metadata: dict[str, Any] = {}
        if choices:
            finish_reason = choices[0].get("finish_reason")
            if finish_reason:
                metadata["finish_reason"] = finish_reason
            tool_calls = choices[0].get("message", {}).get("tool_calls")
            if tool_calls:
                metadata["tool_calls"] = tool_calls

        model_id = response_body.get("model")

        return CanonicalBotResponse(
            content=content,
            raw_response=response_body,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status_code=status_code,
            provider_name=self.get_provider_name(),
            model_identifier=model_id,
            metadata=metadata,
        )


# ─────────────────────────────────────────────────────────────
# Anthropic Adapter
# ─────────────────────────────────────────────────────────────

class AnthropicAdapter(BaseProviderAdapter):
    """
    Adapter for Anthropic API.

    Handles /v1/messages format.
    """

    def get_provider_name(self) -> str:
        return "anthropic"

    def build_request_body(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages = []
        if conversation_history:
            for turn in conversation_history:
                role = "assistant" if turn.get("speaker") == "bot" else "user"
                messages.append({"role": role, "content": turn.get("message", "")})
        messages.append({"role": "user", "content": message})

        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": 1024,
        }

        model = self.model_spec.metadata.get("model", "claude-sonnet-4-20250514")
        body["model"] = model

        return body

    def _get_auth_header(self) -> dict[str, str] | None:
        """Anthropic uses x-api-key header."""
        if self.model_spec.api_key:
            return {
                "x-api-key": self.model_spec.api_key,
                "anthropic-version": "2023-06-01",
            }
        return None

    def parse_response(
        self,
        status_code: int,
        response_body: dict[str, Any] | str,
        latency_ms: float,
    ) -> CanonicalBotResponse:
        if isinstance(response_body, str):
            return CanonicalBotResponse(
                content="",
                status_code=status_code,
                latency_ms=latency_ms,
                error=NormalizedError(
                    category=ErrorCategory.MALFORMED_RESPONSE,
                    message="Expected JSON object, got string",
                    raw_status_code=status_code,
                ),
                provider_name=self.get_provider_name(),
            )

        # Extract content from Anthropic format
        content = ""
        try:
            content_blocks = response_body.get("content", [])
            if content_blocks:
                text_blocks = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                content = "\n".join(text_blocks)
        except (KeyError, TypeError, IndexError):
            pass

        if not content and "error" in response_body:
            error_msg = response_body["error"]
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", str(error_msg))
            return CanonicalBotResponse(
                content="",
                status_code=status_code,
                latency_ms=latency_ms,
                error=NormalizedError(
                    category=ErrorCategory.MALFORMED_RESPONSE,
                    message=str(error_msg),
                    raw_status_code=status_code,
                ),
                provider_name=self.get_provider_name(),
                raw_response=response_body,
            )

        # Extract token usage
        usage = response_body.get("usage", {})
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")

        # Metadata
        metadata: dict[str, Any] = {}
        stop_reason = response_body.get("stop_reason")
        if stop_reason:
            metadata["finish_reason"] = stop_reason

        model_id = response_body.get("model")

        return CanonicalBotResponse(
            content=content,
            raw_response=response_body,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status_code=status_code,
            provider_name=self.get_provider_name(),
            model_identifier=model_id,
            metadata=metadata,
        )


# ─────────────────────────────────────────────────────────────
# Generic HTTP Adapter
# ─────────────────────────────────────────────────────────────

class GenericHTTPAdapter(BaseProviderAdapter):
    """
    Adapter for custom/enterprise bot APIs.

    Enterprise transport features:
    - Configurable HTTP method (POST, PUT, PATCH) via request_template.method
    - Configurable content type via request_template.content_type
    - Configurable latency source (measured or from response header)
    - Rich request template expansion with {message}, {conversation_id},
      {history_json}, {turn_count} placeholders
    - Configurable response_path (dot-separated JSONPath)
    - Optional token_usage_path for cost tracking
    """

    def get_provider_name(self) -> str:
        return "generic"

    @property
    def _http_method(self) -> str:
        """HTTP method from request_template or default POST."""
        if self.model_spec.request_template:
            return self.model_spec.request_template.get("method", "POST").upper()
        return "POST"

    @property
    def _content_type(self) -> str:
        """Content-Type from request_template or default application/json."""
        if self.model_spec.request_template:
            return self.model_spec.request_template.get(
                "content_type", "application/json"
            )
        return "application/json"

    @property
    def _latency_source(self) -> str:
        """How to measure latency: 'measured' (default) or 'header'."""
        if self.model_spec.request_template:
            return self.model_spec.request_template.get("latency_source", "measured")
        return "measured"

    async def send_request(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> CanonicalBotResponse:
        """
        Send request with enterprise transport support.

        Overrides base send_request to support custom HTTP methods
        and content types for enterprise internal APIs.
        """
        body = self.build_request_body(message, conversation_history)
        headers = {
            "Content-Type": self._content_type,
            **self.model_spec.headers,
        }

        if self.model_spec.api_key:
            auth_header = self._get_auth_header()
            if auth_header:
                headers.update(auth_header)

        method = self._http_method
        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(
                verify=self.model_spec.verify_ssl,
                timeout=httpx.Timeout(self.model_spec.timeout_seconds),
            ) as client:
                response = await client.request(
                    method=method,
                    url=self.model_spec.endpoint,
                    json=body if self._content_type == "application/json" else None,
                    content=str(body).encode() if self._content_type != "application/json" else None,
                    headers=headers,
                )
                measured_latency_ms = (time.monotonic() - start_time) * 1000

                # Choose latency source
                latency_ms = measured_latency_ms
                if self._latency_source == "header":
                    header_latency = response.headers.get("X-Response-Time")
                    if header_latency:
                        try:
                            latency_ms = float(header_latency.rstrip("ms"))
                        except ValueError:
                            pass  # Fall back to measured

                if response.status_code >= 400:
                    try:
                        resp_body = response.json()
                    except Exception:
                        resp_body = response.text
                    return CanonicalBotResponse(
                        content="",
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        error=self.normalize_error(response.status_code, resp_body),
                        provider_name=self.get_provider_name(),
                        raw_response=resp_body if isinstance(resp_body, dict) else {"text": str(resp_body)},
                    )

                try:
                    resp_json = response.json()
                except Exception:
                    # Non-JSON response — treat as plain text
                    return CanonicalBotResponse(
                        content=response.text,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        provider_name=self.get_provider_name(),
                    )

                return self.parse_response(
                    status_code=response.status_code,
                    response_body=resp_json,
                    latency_ms=latency_ms,
                )

        except httpx.TimeoutException as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            return CanonicalBotResponse(
                content="",
                latency_ms=latency_ms,
                error=self.normalize_error(408, None, e),
                provider_name=self.get_provider_name(),
            )
        except httpx.ConnectError as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            return CanonicalBotResponse(
                content="",
                latency_ms=latency_ms,
                error=self.normalize_error(0, None, e),
                provider_name=self.get_provider_name(),
            )
        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            return CanonicalBotResponse(
                content="",
                latency_ms=latency_ms,
                error=NormalizedError(
                    category=ErrorCategory.UNKNOWN,
                    message=str(e)[:300],
                    retryable=False,
                ),
                provider_name=self.get_provider_name(),
            )

    def build_request_body(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        # If request_format is openai_compatible, use OpenAI format
        if self.model_spec.request_format == "openai_compatible":
            messages = []
            if conversation_history:
                for turn in conversation_history:
                    role = "assistant" if turn.get("speaker") == "bot" else "user"
                    messages.append({"role": role, "content": turn.get("message", "")})
            messages.append({"role": "user", "content": message})
            return {"messages": messages}

        # If request_template is provided, use rich template expansion
        if self.model_spec.request_template:
            return self._build_from_template(message, conversation_history)

        # Default: simple message format
        return {"message": message}

    def _build_from_template(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Build request body from rich request_template config.

        Supports template variables:
        - {message} → current user message
        - {conversation_id} → conversation tracking ID (if available)
        - {history_json} → JSON-serialized conversation history
        - {turn_count} → number of turns so far

        Template can be:
        - A string (JSON body with {var} placeholders)
        - A dict with 'body' key containing the template
        """
        import json as _json

        template = self.model_spec.request_template.copy()
        body_template = template.get("body", '{"message": "{message}"}')

        # Prepare substitution variables
        history_json = "[]"
        turn_count = "0"
        if conversation_history:
            history_json = _json.dumps(conversation_history)
            turn_count = str(len(conversation_history))

        conversation_id = template.get("conversation_id", "")

        if isinstance(body_template, str):
            # String template — substitute variables
            expanded = body_template
            expanded = expanded.replace("{message}", message)
            expanded = expanded.replace("{conversation_id}", conversation_id)
            expanded = expanded.replace("{history_json}", history_json)
            expanded = expanded.replace("{turn_count}", turn_count)
            try:
                return _json.loads(expanded)
            except _json.JSONDecodeError:
                logger.warning(
                    f"Failed to parse request template body as JSON for "
                    f"model '{self.model_spec.id}'. Falling back to simple format."
                )
                return {"message": message}
        elif isinstance(body_template, dict):
            # Dict template — substitute in string values recursively
            return self._substitute_dict(body_template, {
                "{message}": message,
                "{conversation_id}": conversation_id,
                "{history_json}": history_json,
                "{turn_count}": turn_count,
            })
        else:
            return {"message": message}

    @staticmethod
    def _substitute_dict(d: dict, subs: dict[str, str]) -> dict:
        """Recursively substitute placeholders in dict string values."""
        result = {}
        for key, value in d.items():
            if isinstance(value, str):
                for placeholder, replacement in subs.items():
                    value = value.replace(placeholder, replacement)
                result[key] = value
            elif isinstance(value, dict):
                result[key] = GenericHTTPAdapter._substitute_dict(value, subs)
            else:
                result[key] = value
        return result

    def parse_response(
        self,
        status_code: int,
        response_body: dict[str, Any] | str,
        latency_ms: float,
    ) -> CanonicalBotResponse:
        if isinstance(response_body, str):
            # If it's a plain string, treat it as the content
            return CanonicalBotResponse(
                content=response_body,
                status_code=status_code,
                latency_ms=latency_ms,
                provider_name=self.get_provider_name(),
            )

        # Extract content using response_path
        content = ""
        if self.model_spec.response_path:
            content = _extract_by_path(response_body, self.model_spec.response_path)
        else:
            # Fallback: try common patterns
            for path in ["response", "reply", "answer", "text", "output", "message",
                         "data.response", "data.reply", "result.answer", "result.text"]:
                content = _extract_by_path(response_body, path)
                if content:
                    break

        if not content:
            # Last resort: convert entire response to string
            content = str(response_body)
            if len(content) > 2000:
                content = content[:2000] + "..."

        # Extract token usage if path configured
        input_tokens = None
        output_tokens = None
        if self.model_spec.token_usage_path:
            total = _extract_by_path(response_body, self.model_spec.token_usage_path)
            if total and isinstance(total, (int, float)):
                output_tokens = int(total)

        return CanonicalBotResponse(
            content=content if isinstance(content, str) else str(content),
            raw_response=response_body,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status_code=status_code,
            provider_name=self.get_provider_name(),
        )


# ─────────────────────────────────────────────────────────────
# Path Extraction Utility
# ─────────────────────────────────────────────────────────────

def _extract_by_path(data: dict[str, Any], path: str) -> Any:
    """
    Extract a value from a nested dict using dot-separated path.

    Supports: "choices[0].message.content" and "data.response.text"
    """
    if not path or not isinstance(data, dict):
        return None

    current = data
    parts = path.replace("[", ".[").split(".")

    for part in parts:
        if not part:
            continue

        try:
            if part.startswith("[") and part.endswith("]"):
                # Array index
                idx = int(part[1:-1])
                if isinstance(current, (list, tuple)) and idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return None
            else:
                return None
        except (ValueError, IndexError, TypeError):
            return None

    return current


# ─────────────────────────────────────────────────────────────
# Adapter Factory
# ─────────────────────────────────────────────────────────────

class AdapterFactory:
    """
    Creates the appropriate adapter for a ModelSpec.

    Recommended path: explicit adapter in ModelSpec.
    Fallback: auto-detection based on endpoint patterns.
    """

    # Known endpoint patterns for auto-detection
    _OPENAI_PATTERNS = [
        "api.openai.com", "openai.azure.com",
        "localhost:11434",  # Ollama
    ]
    _ANTHROPIC_PATTERNS = [
        "api.anthropic.com",
    ]

    @classmethod
    def create(cls, model_spec: ModelSpec) -> BaseProviderAdapter:
        """
        Create an adapter for the given model spec.

        Uses explicit adapter type if configured.
        Falls back to auto-detection with warning.
        """
        adapter_type = model_spec.adapter

        if adapter_type == AdapterType.OPENAI:
            return OpenAIAdapter(model_spec)
        elif adapter_type == AdapterType.ANTHROPIC:
            return AnthropicAdapter(model_spec)
        elif adapter_type == AdapterType.GENERIC:
            return GenericHTTPAdapter(model_spec)
        elif adapter_type == AdapterType.AUTO:
            return cls._auto_detect(model_spec)
        else:
            logger.warning(
                f"Unknown adapter type '{adapter_type}' for model '{model_spec.id}'. "
                f"Falling back to generic adapter."
            )
            return GenericHTTPAdapter(model_spec)

    @classmethod
    def _auto_detect(cls, model_spec: ModelSpec) -> BaseProviderAdapter:
        """
        Attempt to detect the provider from endpoint URL patterns.

        This is convenience mode — explicit adapter is recommended.
        """
        endpoint = model_spec.endpoint.lower()
        parsed = urlparse(endpoint)
        hostname = parsed.hostname or ""
        netloc = parsed.netloc or ""  # includes port

        # Check OpenAI patterns (match against hostname, netloc, and full endpoint)
        for pattern in cls._OPENAI_PATTERNS:
            if pattern in hostname or pattern in netloc or pattern in endpoint:
                logger.info(
                    f"Auto-detected OpenAI-compatible adapter for '{model_spec.id}' "
                    f"based on endpoint pattern '{pattern}'"
                )
                return OpenAIAdapter(model_spec)

        # Check Anthropic patterns
        for pattern in cls._ANTHROPIC_PATTERNS:
            if pattern in hostname or pattern in netloc or pattern in endpoint:
                logger.info(
                    f"Auto-detected Anthropic adapter for '{model_spec.id}' "
                    f"based on endpoint pattern '{pattern}'"
                )
                return AnthropicAdapter(model_spec)

        # Check request_format hint
        if model_spec.request_format:
            fmt = model_spec.request_format.lower()
            if fmt in ("openai", "openai_compatible"):
                return OpenAIAdapter(model_spec)
            elif fmt == "anthropic":
                return AnthropicAdapter(model_spec)

        # Default to generic
        logger.warning(
            f"Could not auto-detect provider for '{model_spec.id}' "
            f"(endpoint: {model_spec.endpoint}). Using generic adapter. "
            f"For production use, set 'adapter: openai|anthropic|generic' "
            f"explicitly in your config."
        )
        return GenericHTTPAdapter(model_spec)
