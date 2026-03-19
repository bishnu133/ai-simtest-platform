"""
Bot Discovery Engine — Extracts bot context through exploratory conversations.

Used in Fully Autonomous mode when no documentation is provided.
The engine conducts strategic conversations with the target bot to discover:
  - What the bot does (domain, purpose)
  - What it knows (knowledge base, capabilities)
  - What its boundaries are (guardrails, limitations)
  - How it behaves (tone, personality, response patterns)
"""

from src.discovery.bot_discovery import BotDiscoveryEngine
from src.discovery.strategy import DiscoveryStrategy, DiscoveryPhase
from src.discovery.synthesizer import ContextSynthesizer, DiscoveredContext

__all__ = [
    "BotDiscoveryEngine",
    "DiscoveryStrategy",
    "DiscoveryPhase",
    "ContextSynthesizer",
    "DiscoveredContext",
]
