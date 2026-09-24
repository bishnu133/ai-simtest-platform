#!/usr/bin/env python3
"""
AI SimTest - Setup Verification Script
Run this after installation to verify everything is configured correctly.

Usage:
    python scripts/verify_setup.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_ok(text: str):
    print(f"  ✅ {text}")


def print_fail(text: str):
    print(f"  ❌ {text}")


def print_warn(text: str):
    print(f"  ⚠️  {text}")


def print_info(text: str):
    print(f"  ℹ️  {text}")


async def main():
    print_header("AI SimTest - Setup Verification")
    all_ok = True

    # 1. Check imports
    print_header("Step 1: Core Imports")
    try:
        from src.models import Persona, Conversation, Turn, SimulationConfig, BotConfig
        print_ok("Data models imported successfully")
    except ImportError as e:
        print_fail(f"Model import failed: {e}")
        all_ok = False

    try:
        from src.core.config import settings
        print_ok(f"Settings loaded (env: {settings.app_env})")
    except Exception as e:
        print_fail(f"Settings failed: {e}")
        all_ok = False

    try:
        from src.core.llm_client import LLMClient, LLMClientFactory, LLMProviderManager
        print_ok("LLM client imported successfully")
    except ImportError as e:
        print_fail(f"LLM client import failed: {e}")
        all_ok = False

    try:
        from src.generators.persona_generator import PersonaGenerator
        from src.simulators.conversation_simulator import ConversationSimulator
        from src.judges import JudgeEngine
        from src.core.orchestrator import SimulationOrchestrator
        from src.exporters.dataset_exporter import DatasetExporter
        print_ok("All components imported successfully")
    except ImportError as e:
        print_fail(f"Component import failed: {e}")
        all_ok = False

    # 2. Check LLM providers
    print_header("Step 2: LLM Provider Availability")
    try:
        mgr = LLMProviderManager()
        statuses = await mgr.check_all_providers()

        available_count = 0
        for provider, status in statuses.items():
            if status.available:
                extra = ""
                if status.models:
                    extra = f" (models: {', '.join(status.models[:3])})"
                if status.latency_ms:
                    extra += f" [{status.latency_ms:.0f}ms]"
                print_ok(f"{provider.value}: Available{extra}")
                available_count += 1
            else:
                print_warn(f"{provider.value}: Not available - {status.error}")

        if available_count == 0:
            print_fail("No LLM providers available! Configure at least one in .env")
            all_ok = False
        else:
            print_info(f"{available_count} provider(s) available")

    except Exception as e:
        print_fail(f"Provider check failed: {e}")
        all_ok = False

    # 3. Validate configured models
    print_header("Step 3: Configured Models")
    try:
        from src.core.config import settings

        models = {
            "Persona Generator": settings.persona_generator_model,
            "User Simulator": settings.user_simulator_model,
            "Quality Judge": settings.quality_judge_model,
        }

        from src.core.llm_client import LLMProviderManager, ProviderType
        mgr = LLMProviderManager()

        for role, model in models.items():
            provider = mgr.detect_provider(model)
            valid = await mgr.validate_model(model)
            if valid:
                print_ok(f"{role}: {model} ({provider.value})")
            else:
                print_fail(f"{role}: {model} ({provider.value}) - NOT AVAILABLE")
                suggestion = mgr._suggest_alternative(provider)
                print_info(f"  Suggestion: {suggestion}")
                all_ok = False

    except Exception as e:
        print_fail(f"Model validation failed: {e}")
        all_ok = False

    # 4. Check evaluation models (local, free)
    print_header("Step 4: Local Evaluation Models")

    try:
        from sentence_transformers import SentenceTransformer
        print_ok("sentence-transformers installed")
    except ImportError:
        print_fail("sentence-transformers not installed (needed for Grounding Judge)")
        print_info("  Fix: pip install sentence-transformers")
        all_ok = False

    try:
        import detoxify
        print_ok("detoxify installed")
    except ImportError:
        print_warn("detoxify not installed (optional, for toxicity detection)")
        print_info("  Fix: pip install detoxify")

    try:
        from presidio_analyzer import AnalyzerEngine
        print_ok("presidio-analyzer installed")
    except ImportError:
        print_warn("presidio-analyzer not installed (optional, for PII detection)")
        print_info("  Fix: pip install presidio-analyzer presidio-anonymizer")

    # 5. Quick model test
    print_header("Step 5: Data Model Validation")
    try:
        persona = Persona(name="Test User", role="customer", goals=["get help"])
        assert persona.id.startswith("persona_")
        assert persona.persona_type.value == "standard"
        print_ok("Persona model works")

        conv = Conversation(persona_id=persona.id, turns=[
            Turn(speaker="user", message="Hello"),
            Turn(speaker="bot", message="Hi!"),
        ])
        assert conv.turn_count == 2
        assert len(conv.bot_turns) == 1
        print_ok("Conversation model works")

        config = SimulationConfig(bot=BotConfig(api_endpoint="http://localhost:8080"))
        assert config.num_personas == 20
        assert len(config.judges) == 4
        print_ok("SimulationConfig model works")

    except Exception as e:
        print_fail(f"Model validation failed: {e}")
        all_ok = False

    # Summary
    print_header("RESULT")
    if all_ok:
        print_ok("All checks passed! You're ready to run simulations.")
        print()
        print("  Quick start:")
        print("    # Start mock bot for testing (no cost):")
        print("    python -m tests.mock_bot_server &")
        print()
        print("    # Run a test simulation:")
        print("    simtest run --bot-endpoint http://localhost:9999/v1/chat/completions --personas 5")
        print()
    else:
        print_fail("Some checks failed. Fix the issues above before running simulations.")
        print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
