"""
Tests for Multi-Model Comparison — Phase 2.

Covers: adapters.py, manifest.py
Target: ~28-32 tests

Test categories:
- OpenAI adapter (request building, response parsing, error handling)
- Anthropic adapter (request building, response parsing, auth header)
- Generic adapter (path extraction, template, fallback)
- Adapter factory (explicit selection, auto-detection, warnings)
- Manifest (build, save, load, validate, hash capture)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import httpx

from ai_simtest_engine.multi_compare.models import (
    AdapterType,
    ModelSpec,
    CanonicalBotResponse,
    NormalizedError,
    ErrorCategory,
    RunManifest,
    EvaluatorVersions,
    MultiCompareConfig,
    ComparisonMode,
)
from ai_simtest_engine.multi_compare.adapters import (
    BaseProviderAdapter,
    OpenAIAdapter,
    AnthropicAdapter,
    GenericHTTPAdapter,
    AdapterFactory,
    _extract_by_path,
)
from ai_simtest_engine.multi_compare.manifest import (
    build_manifest,
    detect_evaluator_versions,
    capture_quality_judge_prompt_hash,
    save_manifest,
    load_manifest,
    validate_manifest_match,
    finalize_manifest,
)


# ═══════════════════════════════════════════════════════════
# Helper: build a ModelSpec quickly
# ═══════════════════════════════════════════════════════════

def _spec(adapter="openai", **kwargs) -> ModelSpec:
    return ModelSpec(
        id=kwargs.get("id", "test-model"),
        name=kwargs.get("name", "Test Model"),
        endpoint=kwargs.get("endpoint", "http://localhost:8000/v1/chat/completions"),
        adapter=AdapterType(adapter),
        api_key=kwargs.get("api_key"),
        response_path=kwargs.get("response_path"),
        token_usage_path=kwargs.get("token_usage_path"),
        request_format=kwargs.get("request_format"),
        request_template=kwargs.get("request_template"),
        metadata=kwargs.get("metadata", {}),
    )


# ═══════════════════════════════════════════════════════════
# 1. OPENAI ADAPTER TESTS
# ═══════════════════════════════════════════════════════════

class TestOpenAIAdapter:
    """Tests for OpenAI-compatible adapter."""

    def test_provider_name(self):
        adapter = OpenAIAdapter(_spec())
        assert adapter.get_provider_name() == "openai"

    def test_build_request_simple(self):
        adapter = OpenAIAdapter(_spec())
        body = adapter.build_request_body("Hello")
        assert body["messages"][-1] == {"role": "user", "content": "Hello"}

    def test_build_request_with_history(self):
        adapter = OpenAIAdapter(_spec())
        history = [
            {"speaker": "user", "message": "Hi"},
            {"speaker": "bot", "message": "Hello!"},
        ]
        body = adapter.build_request_body("Follow up", history)
        assert len(body["messages"]) == 3
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][1]["role"] == "assistant"
        assert body["messages"][2]["content"] == "Follow up"

    def test_build_request_with_model_metadata(self):
        spec = _spec(metadata={"model": "gpt-4o"})
        adapter = OpenAIAdapter(spec)
        body = adapter.build_request_body("Test")
        assert body.get("model") == "gpt-4o"

    def test_parse_valid_response(self):
        adapter = OpenAIAdapter(_spec())
        response = {
            "choices": [{"message": {"content": "Hello there!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "gpt-4o",
        }
        result = adapter.parse_response(200, response, 150.0)
        assert result.content == "Hello there!"
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.latency_ms == 150.0
        assert result.model_identifier == "gpt-4o"
        assert result.metadata.get("finish_reason") == "stop"
        assert result.is_error is False

    def test_parse_response_with_tool_calls(self):
        adapter = OpenAIAdapter(_spec())
        response = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{"id": "call_1", "function": {"name": "search"}}],
                },
                "finish_reason": "tool_calls",
            }],
        }
        result = adapter.parse_response(200, response, 100.0)
        assert "tool_calls" in result.metadata

    def test_parse_error_response(self):
        adapter = OpenAIAdapter(_spec())
        response = {"error": {"message": "Rate limit exceeded"}}
        result = adapter.parse_response(200, response, 50.0)
        assert result.is_error is True
        assert "Rate limit" in result.error.message

    def test_parse_string_response(self):
        adapter = OpenAIAdapter(_spec())
        result = adapter.parse_response(200, "not json", 50.0)
        assert result.is_error is True
        assert result.error.category == ErrorCategory.MALFORMED_RESPONSE

    def test_parse_empty_choices(self):
        adapter = OpenAIAdapter(_spec())
        response = {"choices": []}
        result = adapter.parse_response(200, response, 50.0)
        assert result.content == ""
        assert result.is_error is False


# ═══════════════════════════════════════════════════════════
# 2. ANTHROPIC ADAPTER TESTS
# ═══════════════════════════════════════════════════════════

class TestAnthropicAdapter:
    """Tests for Anthropic adapter."""

    def test_provider_name(self):
        adapter = AnthropicAdapter(_spec(adapter="anthropic"))
        assert adapter.get_provider_name() == "anthropic"

    def test_build_request(self):
        adapter = AnthropicAdapter(_spec(adapter="anthropic"))
        body = adapter.build_request_body("Hello")
        assert body["messages"][-1] == {"role": "user", "content": "Hello"}
        assert "max_tokens" in body
        assert "model" in body

    def test_auth_header(self):
        spec = _spec(adapter="anthropic", api_key="sk-ant-test123")
        adapter = AnthropicAdapter(spec)
        headers = adapter._get_auth_header()
        assert headers["x-api-key"] == "sk-ant-test123"
        assert "anthropic-version" in headers

    def test_parse_valid_response(self):
        adapter = AnthropicAdapter(_spec(adapter="anthropic"))
        response = {
            "content": [{"type": "text", "text": "I can help with that."}],
            "usage": {"input_tokens": 15, "output_tokens": 8},
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
        }
        result = adapter.parse_response(200, response, 200.0)
        assert result.content == "I can help with that."
        assert result.input_tokens == 15
        assert result.output_tokens == 8
        assert result.model_identifier == "claude-sonnet-4-20250514"

    def test_parse_multi_block_response(self):
        adapter = AnthropicAdapter(_spec(adapter="anthropic"))
        response = {
            "content": [
                {"type": "text", "text": "Part 1."},
                {"type": "text", "text": "Part 2."},
            ],
        }
        result = adapter.parse_response(200, response, 100.0)
        assert "Part 1." in result.content
        assert "Part 2." in result.content

    def test_parse_error_response(self):
        adapter = AnthropicAdapter(_spec(adapter="anthropic"))
        response = {"error": {"message": "Invalid API key"}}
        result = adapter.parse_response(200, response, 50.0)
        assert result.is_error is True


# ═══════════════════════════════════════════════════════════
# 3. GENERIC ADAPTER TESTS
# ═══════════════════════════════════════════════════════════

class TestGenericHTTPAdapter:
    """Tests for generic/custom API adapter."""

    def test_provider_name(self):
        spec = _spec(adapter="generic", response_path="data.reply")
        adapter = GenericHTTPAdapter(spec)
        assert adapter.get_provider_name() == "generic"

    def test_default_request_body(self):
        spec = _spec(adapter="generic", response_path="data.reply")
        adapter = GenericHTTPAdapter(spec)
        body = adapter.build_request_body("Hello")
        assert body == {"message": "Hello"}

    def test_openai_compatible_request(self):
        spec = _spec(adapter="generic", response_path="data.reply",
                      request_format="openai_compatible")
        adapter = GenericHTTPAdapter(spec)
        body = adapter.build_request_body("Hello")
        assert "messages" in body

    def test_parse_with_response_path(self):
        spec = _spec(adapter="generic", response_path="data.reply")
        adapter = GenericHTTPAdapter(spec)
        response = {"data": {"reply": "Bot says hello"}}
        result = adapter.parse_response(200, response, 80.0)
        assert result.content == "Bot says hello"

    def test_parse_with_deep_path(self):
        spec = _spec(adapter="generic", response_path="result.answer.text")
        adapter = GenericHTTPAdapter(spec)
        response = {"result": {"answer": {"text": "Deep reply"}}}
        result = adapter.parse_response(200, response, 80.0)
        assert result.content == "Deep reply"

    def test_parse_with_token_usage_path(self):
        spec = _spec(adapter="generic", response_path="reply",
                      token_usage_path="usage.total")
        adapter = GenericHTTPAdapter(spec)
        response = {"reply": "Hello", "usage": {"total": 42}}
        result = adapter.parse_response(200, response, 50.0)
        assert result.output_tokens == 42

    def test_parse_string_response(self):
        spec = _spec(adapter="generic", response_path="reply")
        adapter = GenericHTTPAdapter(spec)
        result = adapter.parse_response(200, "Plain text reply", 30.0)
        assert result.content == "Plain text reply"

    def test_fallback_paths(self):
        """Test that generic adapter tries common fallback paths."""
        spec = _spec(adapter="generic")  # no response_path
        adapter = GenericHTTPAdapter(spec)
        response = {"response": "Fallback content"}
        result = adapter.parse_response(200, response, 50.0)
        assert result.content == "Fallback content"


# ═══════════════════════════════════════════════════════════
# 4. PATH EXTRACTION TESTS
# ═══════════════════════════════════════════════════════════

class TestPathExtraction:
    """Tests for JSONPath-like extraction utility."""

    def test_simple_path(self):
        assert _extract_by_path({"a": "value"}, "a") == "value"

    def test_nested_path(self):
        data = {"a": {"b": {"c": "deep"}}}
        assert _extract_by_path(data, "a.b.c") == "deep"

    def test_array_index(self):
        data = {"items": [{"name": "first"}, {"name": "second"}]}
        assert _extract_by_path(data, "items.[0].name") == "first"
        assert _extract_by_path(data, "items.[1].name") == "second"

    def test_missing_key_returns_none(self):
        assert _extract_by_path({"a": 1}, "b") is None

    def test_empty_path_returns_none(self):
        assert _extract_by_path({"a": 1}, "") is None

    def test_non_dict_returns_none(self):
        assert _extract_by_path("not a dict", "a") is None


# ═══════════════════════════════════════════════════════════
# 5. ERROR NORMALIZATION TESTS
# ═══════════════════════════════════════════════════════════

class TestErrorNormalization:
    """Tests for error normalization across adapters."""

    def test_rate_limit_429(self):
        adapter = OpenAIAdapter(_spec())
        error = adapter.normalize_error(429, {})
        assert error.category == ErrorCategory.RATE_LIMIT
        assert error.retryable is True

    def test_auth_error_401(self):
        adapter = OpenAIAdapter(_spec())
        error = adapter.normalize_error(401, {})
        assert error.category == ErrorCategory.AUTH_ERROR
        assert error.retryable is False

    def test_auth_error_403(self):
        adapter = OpenAIAdapter(_spec())
        error = adapter.normalize_error(403, {})
        assert error.category == ErrorCategory.AUTH_ERROR

    def test_timeout_408(self):
        adapter = OpenAIAdapter(_spec())
        error = adapter.normalize_error(408, {})
        assert error.category == ErrorCategory.TIMEOUT
        assert error.retryable is True

    def test_server_error_500(self):
        adapter = OpenAIAdapter(_spec())
        error = adapter.normalize_error(500, {})
        assert error.category == ErrorCategory.SERVER_ERROR

    def test_connection_error(self):
        adapter = OpenAIAdapter(_spec())
        error = adapter.normalize_error(0, None, ConnectionError("Connection refused"))
        assert error.category == ErrorCategory.CONNECTION_ERROR


# ═══════════════════════════════════════════════════════════
# 6. ADAPTER FACTORY TESTS
# ═══════════════════════════════════════════════════════════

class TestAdapterFactory:
    """Tests for adapter factory selection logic."""

    def test_explicit_openai(self):
        spec = _spec(adapter="openai")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, OpenAIAdapter)

    def test_explicit_anthropic(self):
        spec = _spec(adapter="anthropic")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, AnthropicAdapter)

    def test_explicit_generic(self):
        spec = _spec(adapter="generic", response_path="reply")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, GenericHTTPAdapter)

    def test_auto_detect_openai_endpoint(self):
        spec = _spec(adapter="auto",
                      endpoint="https://api.openai.com/v1/chat/completions")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, OpenAIAdapter)

    def test_auto_detect_anthropic_endpoint(self):
        spec = _spec(adapter="auto",
                      endpoint="https://api.anthropic.com/v1/messages")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, AnthropicAdapter)

    def test_auto_detect_unknown_falls_back_to_generic(self):
        spec = _spec(adapter="auto",
                      endpoint="https://custom-bot.internal.corp.com/chat")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, GenericHTTPAdapter)

    def test_auto_detect_with_request_format_hint(self):
        spec = _spec(adapter="auto", request_format="openai",
                      endpoint="https://custom.example.com/api")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, OpenAIAdapter)

    def test_auto_detect_ollama(self):
        spec = _spec(adapter="auto",
                      endpoint="http://localhost:11434/v1/chat/completions")
        adapter = AdapterFactory.create(spec)
        assert isinstance(adapter, OpenAIAdapter)


# ═══════════════════════════════════════════════════════════
# 7. MANIFEST TESTS
# ═══════════════════════════════════════════════════════════

class TestManifest:
    """Tests for run manifest generation and validation."""

    def _make_config(self) -> tuple[MultiCompareConfig, Path]:
        yaml_content = """
comparison:
  mode: head_to_head
models:
  - id: a
    name: A
    endpoint: http://localhost:8001
  - id: b
    name: B
    endpoint: http://localhost:8002
"""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(yaml_content)
        f.close()
        path = Path(f.name)

        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=[
                ModelSpec(id="a", name="A", endpoint="http://localhost:8001"),
                ModelSpec(id="b", name="B", endpoint="http://localhost:8002"),
            ],
        )
        return config, path

    def test_build_manifest(self):
        config, path = self._make_config()
        manifest = build_manifest(
            config=config,
            config_path=path,
            personas_json='[{"id": "p001"}]',
            documentation_text="Test docs",
        )
        assert manifest.config_hash != ""
        assert manifest.persona_set_id != ""
        assert manifest.documentation_hash != ""
        assert len(manifest.manifest_id) == 36
        assert manifest.python_version != ""
        assert len(manifest.model_specs_redacted) == 2
        os.unlink(path)

    def test_build_manifest_no_api_key_in_specs(self):
        config, path = self._make_config()
        config.models[0].api_key = "sk-secretkey123456789012"
        manifest = build_manifest(config=config, config_path=path)
        # Verify no api_key in redacted specs
        for spec_dict in manifest.model_specs_redacted:
            assert "api_key" not in spec_dict
        os.unlink(path)

    def test_save_and_load_manifest(self):
        config, path = self._make_config()
        manifest = build_manifest(config=config, config_path=path)
        with tempfile.TemporaryDirectory() as tmpdir:
            save_manifest(manifest, tmpdir)
            loaded = load_manifest(tmpdir)
            assert loaded is not None
            assert loaded.manifest_id == manifest.manifest_id
            assert loaded.config_hash == manifest.config_hash
        os.unlink(path)

    def test_load_manifest_missing(self):
        result = load_manifest("/nonexistent/path")
        assert result is None

    def test_validate_manifest_match_success(self):
        m1 = RunManifest(config_hash="abc", persona_set_id="def", simtest_version="1.3.0")
        m2 = RunManifest(config_hash="abc", persona_set_id="def", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is True
        assert len(reasons) == 0

    def test_validate_manifest_config_changed(self):
        m1 = RunManifest(config_hash="abc", simtest_version="1.3.0")
        m2 = RunManifest(config_hash="xyz", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is False
        assert any("Config file changed" in r for r in reasons)

    def test_validate_manifest_major_version_mismatch(self):
        m1 = RunManifest(config_hash="abc", simtest_version="2.0.0")
        m2 = RunManifest(config_hash="abc", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is False
        assert any("Major version" in r for r in reasons)

    def test_validate_manifest_minor_version_ok(self):
        m1 = RunManifest(config_hash="abc", simtest_version="1.4.0")
        m2 = RunManifest(config_hash="abc", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is True

    def test_finalize_manifest(self):
        manifest = RunManifest()
        assert manifest.timestamp_end is None
        finalized = finalize_manifest(manifest)
        assert finalized.timestamp_end is not None

    def test_quality_judge_prompt_hash(self):
        prompt1 = "Rate the response on helpfulness, clarity, completeness."
        prompt2 = "Rate the response on helpfulness, clarity, completeness, tone."
        h1 = capture_quality_judge_prompt_hash(prompt1)
        h2 = capture_quality_judge_prompt_hash(prompt2)
        h3 = capture_quality_judge_prompt_hash(prompt1)
        assert h1 == h3  # Same prompt = same hash
        assert h1 != h2  # Different prompt = different hash

    def test_detect_evaluator_versions(self):
        versions = detect_evaluator_versions()
        assert versions.grounding["model"] == "all-MiniLM-L6-v2"
        assert versions.relevance["mode"] == "keyword"
        # Safety versions should be detected (or "not_installed")
        assert versions.safety["presidio"] in ("not_installed", "installed") or versions.safety["presidio"]


# ═══════════════════════════════════════════════════════════
# 8. REVIEW FIX TESTS (Review #2, #3, #4, #5)
# ═══════════════════════════════════════════════════════════

class TestReviewFix2_PromptHashWiring:
    """Review #2: QualityJudge prompt hash wired into manifest."""

    def test_prompt_hash_injected_into_manifest(self):
        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=[
                ModelSpec(id="a", name="A", endpoint="http://localhost:1"),
                ModelSpec(id="b", name="B", endpoint="http://localhost:2"),
            ],
        )
        import tempfile
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("test: content")
        f.close()

        prompt = "Rate this response on helpfulness, clarity, and completeness."
        manifest = build_manifest(
            config=config,
            config_path=f.name,
            quality_prompt_template=prompt,
        )
        assert manifest.evaluator_versions.quality["prompt_hash"] != "unknown"
        assert len(manifest.evaluator_versions.quality["prompt_hash"]) == 64
        os.unlink(f.name)

    def test_prompt_hash_not_set_without_template(self):
        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=[
                ModelSpec(id="a", name="A", endpoint="http://localhost:1"),
                ModelSpec(id="b", name="B", endpoint="http://localhost:2"),
            ],
        )
        import tempfile
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("test: content")
        f.close()

        manifest = build_manifest(config=config, config_path=f.name)
        assert manifest.evaluator_versions.quality["prompt_hash"] == "unknown"
        os.unlink(f.name)


class TestReviewFix3_ManifestValidationStrength:
    """Review #3: Manifest validation checks all critical fields."""

    def test_scenario_assignment_mismatch_blocks(self):
        m1 = RunManifest(config_hash="a", scenario_assignment_id="s1", simtest_version="1.3.0")
        m2 = RunManifest(config_hash="a", scenario_assignment_id="s2", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is False
        assert any("Scenario" in r for r in reasons)

    def test_documentation_hash_mismatch_blocks(self):
        m1 = RunManifest(config_hash="a", documentation_hash="d1", simtest_version="1.3.0")
        m2 = RunManifest(config_hash="a", documentation_hash="d2", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is False
        assert any("Documentation" in r for r in reasons)

    def test_policy_version_mismatch_blocks(self):
        m1 = RunManifest(config_hash="a", policy_version="healthcare", simtest_version="1.3.0")
        m2 = RunManifest(config_hash="a", policy_version="finance", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is False
        assert any("Policy" in r for r in reasons)

    def test_workflow_version_mismatch_blocks(self):
        m1 = RunManifest(config_hash="a", workflow_version="banking", simtest_version="1.3.0")
        m2 = RunManifest(config_hash="a", workflow_version=None, simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is False
        assert any("Workflow" in r for r in reasons)

    def test_minor_version_produces_warning_not_block(self):
        m1 = RunManifest(config_hash="a", simtest_version="1.4.0")
        m2 = RunManifest(config_hash="a", simtest_version="1.3.0")
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is True
        assert any("Minor" in w or "minor" in w.lower() for w in warnings)

    def test_evaluator_drift_warning(self):
        m1 = RunManifest(config_hash="a", simtest_version="1.3.0")
        m1.evaluator_versions.quality["prompt_hash"] = "hash_a"
        m2 = RunManifest(config_hash="a", simtest_version="1.3.0")
        m2.evaluator_versions.quality["prompt_hash"] = "hash_b"
        valid, reasons, warnings = validate_manifest_match(m1, m2)
        assert valid is True  # Not blocking
        assert any("prompt hash" in w.lower() for w in warnings)


class TestReviewFix4_RichTemplateExpansion:
    """Review #4: Generic adapter supports rich request templates."""

    def test_template_with_conversation_id(self):
        spec = _spec(
            adapter="generic",
            response_path="reply",
            request_template={
                "body": '{"query": "{message}", "session": "{conversation_id}"}',
                "conversation_id": "sess_123",
            },
        )
        adapter = GenericHTTPAdapter(spec)
        body = adapter.build_request_body("Hello")
        assert body["query"] == "Hello"
        assert body["session"] == "sess_123"

    def test_template_with_history_json(self):
        spec = _spec(
            adapter="generic",
            response_path="reply",
            request_template={
                "body": '{"msg": "{message}", "context": {history_json}}',
            },
        )
        adapter = GenericHTTPAdapter(spec)
        history = [{"speaker": "user", "message": "Hi"}]
        body = adapter.build_request_body("Follow up", history)
        assert body["msg"] == "Follow up"
        assert isinstance(body["context"], list)

    def test_template_dict_body(self):
        spec = _spec(
            adapter="generic",
            response_path="reply",
            request_template={
                "body": {"query": "{message}", "meta": {"turns": "{turn_count}"}},
            },
        )
        adapter = GenericHTTPAdapter(spec)
        body = adapter.build_request_body("Test", [{"speaker": "user", "message": "Hi"}])
        assert body["query"] == "Test"
        assert body["meta"]["turns"] == "1"

    def test_template_invalid_json_falls_back(self):
        spec = _spec(
            adapter="generic",
            response_path="reply",
            request_template={"body": "not {valid json {message}"},
        )
        adapter = GenericHTTPAdapter(spec)
        body = adapter.build_request_body("Hello")
        assert body == {"message": "Hello"}  # Fallback


# ═══════════════════════════════════════════════════════════
# 9. RUNTIME ADAPTER TESTS (Review #5)
# ═══════════════════════════════════════════════════════════

class TestAdapterSendRequest:
    """Runtime tests for adapter send_request() with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_send_request_success(self):
        """Successful request returns CanonicalBotResponse with content."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        mock_response = httpx.Response(
            status_code=200,
            json={
                "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            request=httpx.Request("POST", spec.endpoint),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.send_request("Hi")
            assert result.content == "Hello!"
            assert result.input_tokens == 10
            assert result.output_tokens == 5
            assert result.is_error is False

    @pytest.mark.asyncio
    async def test_send_request_timeout(self):
        """Timeout produces TIMEOUT error category."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock,
                   side_effect=httpx.TimeoutException("Read timed out")):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.TIMEOUT
            assert result.error.retryable is True

    @pytest.mark.asyncio
    async def test_send_request_connection_error(self):
        """Connection failure produces CONNECTION_ERROR category."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock,
                   side_effect=httpx.ConnectError("Connection refused")):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_send_request_429_rate_limit(self):
        """429 response produces RATE_LIMIT error with retryable=True."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        mock_response = httpx.Response(
            status_code=429,
            json={"error": {"message": "Rate limit exceeded"}},
            request=httpx.Request("POST", spec.endpoint),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.RATE_LIMIT
            assert result.error.retryable is True

    @pytest.mark.asyncio
    async def test_send_request_500_server_error(self):
        """500 response produces SERVER_ERROR category."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        mock_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", spec.endpoint),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.SERVER_ERROR
            assert result.error.retryable is True

    @pytest.mark.asyncio
    async def test_send_request_malformed_json_response(self):
        """Non-JSON 200 response produces MALFORMED_RESPONSE error."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        mock_response = httpx.Response(
            status_code=200,
            text="<html>Not JSON</html>",
            request=httpx.Request("POST", spec.endpoint),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.MALFORMED_RESPONSE

    @pytest.mark.asyncio
    async def test_send_request_401_auth_error(self):
        """401 response produces AUTH_ERROR, not retryable."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        mock_response = httpx.Response(
            status_code=401,
            json={"error": {"message": "Invalid API key"}},
            request=httpx.Request("POST", spec.endpoint),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.AUTH_ERROR
            assert result.error.retryable is False

    @pytest.mark.asyncio
    async def test_send_request_latency_captured(self):
        """Latency is always measured even on success."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        mock_response = httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": "Ok"}}]},
            request=httpx.Request("POST", spec.endpoint),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.send_request("Hi")
            assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_send_request_unexpected_exception(self):
        """Unexpected exceptions produce UNKNOWN error."""
        spec = _spec(adapter="openai")
        adapter = OpenAIAdapter(spec)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock,
                   side_effect=RuntimeError("Something unexpected")):
            result = await adapter.send_request("Hi")
            assert result.is_error is True
            assert result.error.category == ErrorCategory.UNKNOWN
            assert "unexpected" in result.error.message.lower()


# ═══════════════════════════════════════════════════════════
# 10. GENERIC ADAPTER TRANSPORT TESTS (Review #2)
# ═══════════════════════════════════════════════════════════

class TestGenericAdapterTransport:
    """Tests for enterprise transport features in GenericHTTPAdapter."""

    def test_custom_http_method(self):
        spec = _spec(adapter="generic", response_path="reply",
                      request_template={"method": "PUT", "body": '{"msg": "{message}"}'})
        adapter = GenericHTTPAdapter(spec)
        assert adapter._http_method == "PUT"

    def test_default_http_method_is_post(self):
        spec = _spec(adapter="generic", response_path="reply")
        adapter = GenericHTTPAdapter(spec)
        assert adapter._http_method == "POST"

    def test_custom_content_type(self):
        spec = _spec(adapter="generic", response_path="reply",
                      request_template={"content_type": "text/plain"})
        adapter = GenericHTTPAdapter(spec)
        assert adapter._content_type == "text/plain"

    def test_default_content_type_is_json(self):
        spec = _spec(adapter="generic", response_path="reply")
        adapter = GenericHTTPAdapter(spec)
        assert adapter._content_type == "application/json"

    def test_latency_source_default(self):
        spec = _spec(adapter="generic", response_path="reply")
        adapter = GenericHTTPAdapter(spec)
        assert adapter._latency_source == "measured"

    def test_latency_source_header(self):
        spec = _spec(adapter="generic", response_path="reply",
                      request_template={"latency_source": "header"})
        adapter = GenericHTTPAdapter(spec)
        assert adapter._latency_source == "header"


# ═══════════════════════════════════════════════════════════
# 11. PREFLIGHT CONNECTIVITY TESTS (Review #1)
# ═══════════════════════════════════════════════════════════

class TestPreflightConnectivity:
    """Tests for preflight endpoint connectivity checks."""

    def test_preflight_result_all_reachable(self):
        from ai_simtest_engine.multi_compare.config_loader import PreflightResult
        result = PreflightResult()
        result.add("model-a", reachable=True, status_code=200, latency_ms=50.0)
        result.add("model-b", reachable=True, status_code=200, latency_ms=80.0)
        assert result.all_reachable is True
        assert result.unreachable_models == []
        assert "2/2" in result.summary()

    def test_preflight_result_partial_failure(self):
        from ai_simtest_engine.multi_compare.config_loader import PreflightResult
        result = PreflightResult()
        result.add("model-a", reachable=True, status_code=200)
        result.add("model-b", reachable=False, error="Connection refused")
        assert result.all_reachable is False
        assert result.unreachable_models == ["model-b"]
        assert "1/2" in result.summary()

    @pytest.mark.asyncio
    async def test_preflight_check_with_timeout(self):
        """Preflight marks unreachable on timeout."""
        from ai_simtest_engine.multi_compare.config_loader import preflight_check_endpoints
        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=[
                ModelSpec(id="a", name="A", endpoint="http://localhost:1"),
                ModelSpec(id="b", name="B", endpoint="http://localhost:2"),
            ],
        )
        # These ports should not have servers, so they'll fail
        result = await preflight_check_endpoints(config, timeout_seconds=1.0)
        # At least one should be unreachable (localhost ports 1 and 2)
        assert len(result.results) == 2
        assert not result.all_reachable
