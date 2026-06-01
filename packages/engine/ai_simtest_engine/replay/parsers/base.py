"""
Base parser interface for conversation importers.

Each format (JSON, CSV, text, etc.) implements this ABC.
New parsers can be added without modifying existing code —
just create a new file in parsers/ and register it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ai_simtest_engine.replay.models import ConversationSource, ImportedConversation, ReplayConfig


class ConversationParser(ABC):
    """Abstract base class for conversation parsers."""

    # Subclasses set these
    format_name: str = "base"
    file_extensions: list[str] = []

    @abstractmethod
    def parse_file(
        self,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        """
        Parse a single file into a list of imported conversations.

        Args:
            file_path: Path to the input file
            config: Replay configuration with column mappings, speaker patterns, etc.

        Returns:
            List of ImportedConversation objects with source tracking
        """
        ...

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the given file based on extension."""
        return file_path.suffix.lower().lstrip(".") in self.file_extensions

    def _normalize_role(self, role: str, config: ReplayConfig) -> str:
        """Normalize a speaker role to 'user' or 'bot'."""
        role_lower = role.lower().strip()
        if role_lower in [n.lower() for n in config.user_speaker_names]:
            return "user"
        if role_lower in [n.lower() for n in config.bot_speaker_names]:
            return "bot"
        # Fallback: check common patterns
        if any(kw in role_lower for kw in ("user", "human", "customer", "client")):
            return "user"
        if any(kw in role_lower for kw in ("bot", "assistant", "agent", "ai")):
            return "bot"
        return role_lower  # Unknown role — let the loader handle it
