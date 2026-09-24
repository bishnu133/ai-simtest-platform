"""
LLM Client - Multi-provider LLM wrapper using LiteLLM.
Supports Local (Ollama, LM Studio) and Cloud (OpenAI, Anthropic, Google).

Testers choose per-component whether to use local or cloud LLMs via .env:
  PERSONA_GENERATOR_MODEL=ollama/llama3.1:8b   # Free, local
  USER_SIMULATOR_MODEL=gpt-4-turbo              # Cloud, realistic
  QUALITY_JUDGE_MODEL=claude-sonnet-4-20250514           # Cloud, accurate
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_simtest_engine.core.config import settings
from ai_simtest_engine.core.logging import get_logger

logger = get_logger(__name__)

litellm.drop_params = True
litellm.set_verbose = False


class ProviderType(str, Enum):
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    HUGGINGFACE = "huggingface"
    UNKNOWN = "unknown"


@dataclass
class ProviderStatus:
    provider: ProviderType
    available: bool
    latency_ms: float | None = None
    error: str | None = None
    models: list[str] = field(default_factory=list)


class LLMProviderManager:
    """Detects available LLM providers, validates models, suggests alternatives."""

    def __init__(self):
        self._status_cache: dict[ProviderType, ProviderStatus] = {}

    @staticmethod
    def detect_provider(model_string: str) -> ProviderType:
        m = model_string.lower()
        if m.startswith("ollama/") or m.startswith("ollama_chat/"):
            return ProviderType.OLLAMA
        elif m.startswith("lm_studio/"):
            return ProviderType.LM_STUDIO
        elif m.startswith("gemini/") or m.startswith("google/"):
            return ProviderType.GOOGLE
        elif m.startswith("huggingface/"):
            return ProviderType.HUGGINGFACE
        elif "claude" in m:
            return ProviderType.ANTHROPIC
        elif "gpt" in m or "o1" in m or "o3" in m:
            return ProviderType.OPENAI
        return ProviderType.UNKNOWN

    async def check_provider(self, provider: ProviderType) -> ProviderStatus:
        import httpx
        try:
            start = time.perf_counter()
            if provider == ProviderType.OLLAMA:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                    resp.raise_for_status()
                    models = [m["name"] for m in resp.json().get("models", [])]
                    ms = (time.perf_counter() - start) * 1000
                    return ProviderStatus(provider=provider, available=True, latency_ms=round(ms, 1), models=models)
            elif provider == ProviderType.LM_STUDIO:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get("http://localhost:1234/v1/models")
                    resp.raise_for_status()
                    ms = (time.perf_counter() - start) * 1000
                    return ProviderStatus(provider=provider, available=True, latency_ms=round(ms, 1))
            elif provider == ProviderType.OPENAI:
                ok = bool(settings.openai_api_key)
                return ProviderStatus(provider=provider, available=ok, error=None if ok else "OPENAI_API_KEY not set")
            elif provider == ProviderType.ANTHROPIC:
                ok = bool(settings.anthropic_api_key)
                return ProviderStatus(provider=provider, available=ok, error=None if ok else "ANTHROPIC_API_KEY not set")
            elif provider == ProviderType.GOOGLE:
                ok = bool(settings.google_api_key)
                return ProviderStatus(provider=provider, available=ok, error=None if ok else "GOOGLE_API_KEY not set")
            return ProviderStatus(provider=provider, available=False, error="Unknown provider")
        except Exception as e:
            return ProviderStatus(provider=provider, available=False, error=str(e))

    async def check_all_providers(self) -> dict[ProviderType, ProviderStatus]:
        providers = [ProviderType.OLLAMA, ProviderType.OPENAI, ProviderType.ANTHROPIC, ProviderType.GOOGLE]
        results = await asyncio.gather(*[self.check_provider(p) for p in providers])
        for s in results:
            self._status_cache[s.provider] = s
            if s.available:
                extra = f" (models: {', '.join(s.models[:5])})" if s.models else ""
                extra += f" [{s.latency_ms:.0f}ms]" if s.latency_ms else ""
                logger.info("provider_available", provider=s.provider.value, details=extra)
            else:
                logger.warning("provider_unavailable", provider=s.provider.value, reason=s.error)
        return {s.provider: s for s in results}

    async def validate_model(self, model_string: str) -> bool:
        provider = self.detect_provider(model_string)
        status = await self.check_provider(provider)
        if not status.available:
            logger.error("model_unavailable", model=model_string, provider=provider.value,
                         suggestion=self._suggest_alternative(provider))
            return False
        if provider == ProviderType.OLLAMA:
            name = model_string.replace("ollama/", "").replace("ollama_chat/", "")
            if status.models and name not in status.models:
                logger.warning("ollama_model_not_pulled", model=name, suggestion=f"Run: ollama pull {name}")
                return False
        return True

    def _suggest_alternative(self, provider: ProviderType) -> str:
        return {
            ProviderType.OLLAMA: "Install Ollama then: ollama pull llama3.1:8b",
            ProviderType.OPENAI: "Set OPENAI_API_KEY in .env, or use ollama/llama3.1:8b",
            ProviderType.ANTHROPIC: "Set ANTHROPIC_API_KEY in .env, or use gpt-4-turbo",
            ProviderType.GOOGLE: "Set GOOGLE_API_KEY in .env",
            ProviderType.LM_STUDIO: "Start LM Studio, or use Ollama instead",
        }.get(provider, "Check provider configuration")


class LLMClient:
    """
    Async LLM client. Works identically for local and cloud models.

    Local:  LLMClient("ollama/llama3.1:8b")
    Cloud:  LLMClient("gpt-4-turbo")
    """

    def __init__(self, model: str = "gpt-4-turbo", temperature: float = 0.7,
                 max_tokens: int = 2000, timeout: int = 60):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.provider = LLMProviderManager.detect_provider(model)
        self._semaphore = asyncio.Semaphore(settings.max_parallel_conversations)
        if self.provider == ProviderType.OLLAMA:
            litellm.api_base = settings.ollama_base_url

    @property
    def is_local(self) -> bool:
        return self.provider in (ProviderType.OLLAMA, ProviderType.LM_STUDIO)

    @property
    def is_cloud(self) -> bool:
        return not self.is_local

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
    async def generate(self, prompt: str, system_prompt: str | None = None,
                       temperature: float | None = None, max_tokens: int | None = None,
                       response_format: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, temperature, max_tokens, response_format)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
    async def chat(self, messages: list[dict[str, str]], temperature: float | None = None,
                   max_tokens: int | None = None, response_format: str | None = None) -> str:
        async with self._semaphore:
            kwargs: dict[str, Any] = {
                "model": self.model, "messages": messages,
                "temperature": temperature or self.temperature,
                "max_tokens": max_tokens or self.max_tokens, "timeout": self.timeout,
            }
            if response_format == "json" and self.provider != ProviderType.OLLAMA:
                kwargs["response_format"] = {"type": "json_object"}

            logger.debug("llm_request", model=self.model, provider=self.provider.value, n=len(messages))
            response = await litellm.acompletion(**kwargs)

            # ── Cost tracking (P4 #27) ────────────────────────────
            try:
                from ai_simtest_engine.cost.tracker import get_cost_tracker
                from ai_simtest_engine.cost.pricing import estimate_cost_from_usage
                cost_tracker = get_cost_tracker()
                if cost_tracker is not None and not cost_tracker.is_finalized:
                    usage = getattr(response, 'usage', None)
                    has_usage = usage is not None and getattr(usage, 'total_tokens', 0) > 0
                    pt, ct, tt, est_cost = estimate_cost_from_usage(self.model, usage)
                    component = getattr(self, '_cost_component', 'other')
                    subcomponent = getattr(self, '_cost_subcomponent', '')
                    cost_tracker.record(
                        model=self.model,
                        prompt_tokens=pt,
                        completion_tokens=ct,
                        total_tokens=tt,
                        component=component,
                        subcomponent=subcomponent,
                        provider=self.provider.value,
                        has_usage=has_usage,
                    )
            except Exception:
                pass  # Cost tracking must never break LLM calls

            content = response.choices[0].message.content
            logger.debug("llm_response", model=self.model, provider=self.provider.value,
                         tokens=response.usage.total_tokens if response.usage else None)
            return content

    async def generate_json(self, prompt: str, system_prompt: str | None = None) -> dict | list:
        enhanced = prompt
        if self.is_local:
            enhanced = prompt + "\n\nRespond with ONLY valid JSON. No markdown, no explanation."
        response = await self.generate(enhanced, system_prompt, response_format="json")
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())


class LLMClientFactory:
    """
    Factory creating LLM clients from .env settings.
    Each component can use a different provider:

      .env:
        PERSONA_GENERATOR_MODEL=ollama/llama3.1:8b   # Free
        USER_SIMULATOR_MODEL=gpt-4-turbo              # Cloud
        QUALITY_JUDGE_MODEL=claude-sonnet-4-20250514           # Cloud
    """
    _clients: dict[str, LLMClient] = {}

    @classmethod
    def get_client(cls, model: str, temperature: float = 0.7, max_tokens: int = 2000) -> LLMClient:
        key = f"{model}_{temperature}_{max_tokens}"
        if key not in cls._clients:
            cls._clients[key] = LLMClient(model=model, temperature=temperature, max_tokens=max_tokens)
        return cls._clients[key]

    @classmethod
    def persona_generator(cls) -> LLMClient:
        client = cls.get_client(settings.persona_generator_model, temperature=0.9, max_tokens=4000)
        client._cost_component = "persona_generator"
        return client

    @classmethod
    def user_simulator(cls) -> LLMClient:
        client = cls.get_client(settings.user_simulator_model, temperature=0.8, max_tokens=500)
        client._cost_component = "user_simulator"
        return client

    @classmethod
    def quality_judge(cls) -> LLMClient:
        client = cls.get_client(settings.quality_judge_model, temperature=0.1, max_tokens=2000)
        client._cost_component = "quality_judge"
        return client

    @classmethod
    def clear_cache(cls) -> None:
        cls._clients.clear()

    @classmethod
    async def validate_all_configured_models(cls) -> dict[str, bool]:
        mgr = LLMProviderManager()
        models = {
            "persona_generator": settings.persona_generator_model,
            "user_simulator": settings.user_simulator_model,
            "quality_judge": settings.quality_judge_model,
        }
        results = {}
        for role, model in models.items():
            valid = await mgr.validate_model(model)
            results[role] = valid
            provider = LLMProviderManager.detect_provider(model)
            if valid:
                logger.info("model_ok", role=role, model=model, provider=provider.value)
            else:
                logger.error("model_fail", role=role, model=model)
        return results