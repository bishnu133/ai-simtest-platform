"""
Text / Markdown parser for conversation imports.

Handles plain chat transcripts where speakers are identified by prefixes.

Conversation splitting rules (#11 — clarified):
  - Explicit separators (---, ===, ___) always split conversations
  - TWO or more consecutive blank lines split conversations
  - A single blank line does NOT split (allows breathing room in transcripts)
  - If no separators found, entire file is treated as one conversation

Example formats:
    Customer: I need to cancel my flight
    Bot: I can help with that.

    User: Hello
    Assistant: Hi! How can I help?

    [10:30] John: What's my balance?
    [10:31] BankBot: Your balance is $500.

Supports custom speaker patterns via config.speaker_pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.replay.models import ConversationSource, ImportedConversation, ReplayConfig
from src.replay.parsers.base import ConversationParser


class TextParser(ConversationParser):
    """Parse plain text/markdown chat transcripts."""

    format_name = "text"
    file_extensions = ["txt", "md", "markdown", "log", "chat"]

    # Default speaker patterns (order matters — first match wins)
    DEFAULT_PATTERNS = [
        # "Speaker: message"
        r"^(?:\[[\d:/ -]+\]\s*)?(?P<role>[A-Za-z_\- ]+?):\s+(?P<content>.+)$",
    ]

    # Conversation separators
    SEPARATOR_PATTERN = re.compile(r"^\s*(?:---+|===+|___+|\*\*\*+)\s*$")
    BLANK_LINE = re.compile(r"^\s*$")

    def parse_file(
        self,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Determine speaker pattern
        if config.speaker_pattern:
            # User provided explicit pattern like "Customer:|Bot:"
            parts = [p.strip() for p in config.speaker_pattern.split("|")]
            speaker_re = self._build_custom_pattern(parts)
        else:
            speaker_re = re.compile(self.DEFAULT_PATTERNS[0], re.MULTILINE)

        # Split into conversations (separated by --- or blank line gaps)
        conversations = self._split_into_conversations(lines)

        results = []
        for idx, conv_lines in enumerate(conversations):
            messages = self._parse_messages(conv_lines, speaker_re, config)
            if not messages:
                continue

            source = ConversationSource(
                file_path=str(file_path),
                line_number=conv_lines[0][0] if conv_lines else None,
                conversation_index=idx,
                original_id=f"text_conv_{idx}",
            )

            results.append(ImportedConversation(
                source=source,
                messages=messages,
            ))

        return results

    def _build_custom_pattern(self, speaker_labels: list[str]) -> re.Pattern:
        """Build a regex from user-provided speaker labels like ['Customer:', 'Bot:']."""
        # Remove trailing colon from labels
        clean = [s.rstrip(":").strip() for s in speaker_labels]
        escaped = [re.escape(s) for s in clean if s]
        group = "|".join(escaped)
        return re.compile(rf"^(?:\[[\d:/ -]+\]\s*)?(?P<role>{group}):\s+(?P<content>.+)$", re.MULTILINE)

    def _split_into_conversations(
        self,
        lines: list[str],
    ) -> list[list[tuple[int, str]]]:
        """Split lines into conversation groups using separators."""
        conversations: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        blank_count = 0

        for line_num, line in enumerate(lines, start=1):
            if self.SEPARATOR_PATTERN.match(line):
                if current:
                    conversations.append(current)
                    current = []
                blank_count = 0
                continue

            if self.BLANK_LINE.match(line):
                blank_count += 1
                if blank_count >= 2 and current:
                    # Two consecutive blank lines = conversation break
                    conversations.append(current)
                    current = []
                    blank_count = 0
                continue

            blank_count = 0
            current.append((line_num, line))

        if current:
            conversations.append(current)

        # If no separators found, treat entire file as one conversation
        if not conversations and lines:
            conversations = [[(i + 1, l) for i, l in enumerate(lines) if l.strip()]]

        return conversations

    def _parse_messages(
        self,
        numbered_lines: list[tuple[int, str]],
        speaker_re: re.Pattern,
        config: ReplayConfig,
    ) -> list[dict[str, str]]:
        """Parse lines into messages using the speaker pattern."""
        messages = []
        current_role = None
        current_content_parts: list[str] = []

        for _, line in numbered_lines:
            match = speaker_re.match(line.strip())
            if match:
                # Save previous message
                if current_role and current_content_parts:
                    messages.append({
                        "role": self._normalize_role(current_role, config),
                        "content": " ".join(current_content_parts).strip(),
                    })
                current_role = match.group("role")
                current_content_parts = [match.group("content")]
            elif current_role:
                # Continuation of previous message (multi-line)
                current_content_parts.append(line.strip())

        # Don't forget the last message
        if current_role and current_content_parts:
            messages.append({
                "role": self._normalize_role(current_role, config),
                "content": " ".join(current_content_parts).strip(),
            })

        return messages
