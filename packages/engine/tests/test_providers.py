"""
Tests for LLM Provider detection and health checks.
Phase 1 Test Gate: Verify provider system works for local + cloud.

Run: pytest tests/test_providers.py -v
"""

import pytest
from src.core.llm_client import LLMProviderManager, ProviderType, LLMClient, LLMClientFactory


# ============================================================
# Provider Detection Tests (no network needed)
# ============================================================

class TestProviderDetection:
    """Verify that model strings are correctly mapped to providers."""

    def test_ollama_models(self):
        assert LLMProviderManager.detect_provider("ollama/llama3.1:8b") == ProviderType.OLLAMA
        assert LLMProviderManager.detect_provider("ollama/mistral:7b") == ProviderType.OLLAMA
        assert LLMProviderManager.detect_provider("ollama_chat/llama3.1:8b") == ProviderType.OLLAMA

    def test_lm_studio_models(self):
        assert LLMProviderManager.detect_provider("lm_studio/local-model") == ProviderType.LM_STUDIO

    def test_openai_models(self):
        assert LLMProviderManager.detect_provider("gpt-4-turbo") == ProviderType.OPENAI
        assert LLMProviderManager.detect_provider("gpt-3.5-turbo") == ProviderType.OPENAI
        assert LLMProviderManager.detect_provider("gpt-4o") == ProviderType.OPENAI
        assert LLMProviderManager.detect_provider("o1-preview") == ProviderType.OPENAI
        assert LLMProviderManager.detect_provider("o3-mini") == ProviderType.OPENAI

    def test_anthropic_models(self):
        assert LLMProviderManager.detect_provider("claude-sonnet-4-20250514") == ProviderType.ANTHROPIC
        assert LLMProviderManager.detect_provider("claude-3-opus-20240229") == ProviderType.ANTHROPIC
        assert LLMProviderManager.detect_provider("claude-3-haiku-20240307") == ProviderType.ANTHROPIC

    def test_google_models(self):
        assert LLMProviderManager.detect_provider("gemini/gemini-pro") == ProviderType.GOOGLE
        assert LLMProviderManager.detect_provider("google/gemini-1.5-flash") == ProviderType.GOOGLE

    def test_huggingface_models(self):
        assert LLMProviderManager.detect_provider("huggingface/mistral-7b") == ProviderType.HUGGINGFACE

    def test_unknown_models(self):
        assert LLMProviderManager.detect_provider("some-random-model") == ProviderType.UNKNOWN


# ============================================================
# LLMClient Properties Tests
# ============================================================

class TestLLMClientProperties:
    """Verify client correctly identifies local vs cloud."""

    def test_ollama_is_local(self):
        client = LLMClient("ollama/llama3.1:8b")
        assert client.is_local is True
        assert client.is_cloud is False
        assert client.provider == ProviderType.OLLAMA

    def test_lm_studio_is_local(self):
        client = LLMClient("lm_studio/local-model")
        assert client.is_local is True
        assert client.is_cloud is False

    def test_openai_is_cloud(self):
        client = LLMClient("gpt-4-turbo")
        assert client.is_local is False
        assert client.is_cloud is True
        assert client.provider == ProviderType.OPENAI

    def test_anthropic_is_cloud(self):
        client = LLMClient("claude-sonnet-4-20250514")
        assert client.is_cloud is True
        assert client.provider == ProviderType.ANTHROPIC

    def test_default_model(self):
        client = LLMClient()
        assert client.model == "gpt-4-turbo"
        assert client.is_cloud is True

    def test_custom_parameters(self):
        client = LLMClient("ollama/llama3.1:8b", temperature=0.5, max_tokens=1000, timeout=30)
        assert client.temperature == 0.5
        assert client.max_tokens == 1000
        assert client.timeout == 30


# ============================================================
# LLMClientFactory Tests
# ============================================================

class TestLLMClientFactory:
    """Verify factory creates correct clients from settings."""

    def test_factory_caches_clients(self):
        LLMClientFactory.clear_cache()
        c1 = LLMClientFactory.get_client("gpt-4-turbo", temperature=0.7)
        c2 = LLMClientFactory.get_client("gpt-4-turbo", temperature=0.7)
        assert c1 is c2  # Same instance

    def test_factory_different_params_different_clients(self):
        LLMClientFactory.clear_cache()
        c1 = LLMClientFactory.get_client("gpt-4-turbo", temperature=0.7)
        c2 = LLMClientFactory.get_client("gpt-4-turbo", temperature=0.1)
        assert c1 is not c2  # Different instances

    def test_persona_generator_client(self):
        client = LLMClientFactory.persona_generator()
        assert client.temperature == 0.9
        assert client.max_tokens == 4000

    def test_user_simulator_client(self):
        client = LLMClientFactory.user_simulator()
        assert client.temperature == 0.8
        assert client.max_tokens == 500

    def test_quality_judge_client(self):
        client = LLMClientFactory.quality_judge()
        assert client.temperature == 0.1
        assert client.max_tokens == 2000

    def test_clear_cache(self):
        LLMClientFactory.clear_cache()
        c1 = LLMClientFactory.get_client("gpt-4-turbo")
        LLMClientFactory.clear_cache()
        c2 = LLMClientFactory.get_client("gpt-4-turbo")
        assert c1 is not c2


# ============================================================
# Provider Health Check Tests (async, may need network)
# ============================================================

class TestProviderHealthCheck:
    """
    Provider connectivity tests.
    These test the health check logic itself, not actual provider availability.
    """

    @pytest.mark.asyncio
    async def test_check_returns_provider_status(self):
        mgr = LLMProviderManager()
        # Ollama may or may not be running, but check should not crash
        status = await mgr.check_provider(ProviderType.OLLAMA)
        assert status.provider == ProviderType.OLLAMA
        assert isinstance(status.available, bool)

    @pytest.mark.asyncio
    async def test_check_openai_without_key(self, monkeypatch):
        """OpenAI should be unavailable when no API key is set."""
        monkeypatch.setattr("src.core.config.settings.openai_api_key", None)
        mgr = LLMProviderManager()
        status = await mgr.check_provider(ProviderType.OPENAI)
        assert status.available is False
        assert "not set" in status.error

    @pytest.mark.asyncio
    async def test_check_openai_with_key(self, monkeypatch):
        """OpenAI should be available when API key is set."""
        monkeypatch.setattr("src.core.config.settings.openai_api_key", "sk-test-key")
        mgr = LLMProviderManager()
        status = await mgr.check_provider(ProviderType.OPENAI)
        assert status.available is True

    @pytest.mark.asyncio
    async def test_check_anthropic_without_key(self, monkeypatch):
        monkeypatch.setattr("src.core.config.settings.anthropic_api_key", None)
        mgr = LLMProviderManager()
        status = await mgr.check_provider(ProviderType.ANTHROPIC)
        assert status.available is False

    @pytest.mark.asyncio
    async def test_check_all_returns_dict(self):
        mgr = LLMProviderManager()
        results = await mgr.check_all_providers()
        assert isinstance(results, dict)
        assert ProviderType.OPENAI in results
        assert ProviderType.OLLAMA in results

    @pytest.mark.asyncio
    async def test_suggest_alternative_for_ollama(self):
        mgr = LLMProviderManager()
        suggestion = mgr._suggest_alternative(ProviderType.OLLAMA)
        assert "ollama" in suggestion.lower()

    @pytest.mark.asyncio
    async def test_suggest_alternative_for_openai(self):
        mgr = LLMProviderManager()
        suggestion = mgr._suggest_alternative(ProviderType.OPENAI)
        assert "OPENAI_API_KEY" in suggestion or "ollama" in suggestion.lower()

    @pytest.mark.asyncio
    async def test_validate_model_checks_provider(self, monkeypatch):
        monkeypatch.setattr("src.core.config.settings.openai_api_key", "sk-test")
        mgr = LLMProviderManager()
        valid = await mgr.validate_model("gpt-4-turbo")
        assert valid is True

    @pytest.mark.asyncio
    async def test_validate_model_fails_without_key(self, monkeypatch):
        monkeypatch.setattr("src.core.config.settings.openai_api_key", None)
        mgr = LLMProviderManager()
        valid = await mgr.validate_model("gpt-4-turbo")
        assert valid is False