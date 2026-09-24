"""
JSON / JSONL parser for conversation imports.

Supports two common structures:

1. Single JSON file with array of conversations:
   [{"conversation_id": "...", "messages": [{"role": "user", "content": "..."}]}]

2. JSONL file with one conversation per line:
   {"conversation_id": "...", "messages": [{"role": "user", "content": "..."}]}

Also handles flat message arrays (auto-groups by conversation_id field).
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_simtest_engine.replay.models import ConversationSource, ImportedConversation, ReplayConfig
from ai_simtest_engine.replay.parsers.base import ConversationParser


class JSONParser(ConversationParser):
    """Parse JSON files containing conversation data."""

    format_name = "json"
    file_extensions = ["json"]

    def parse_file(
        self,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        text = file_path.read_text(encoding="utf-8")
        data = json.loads(text)

        if isinstance(data, list):
            return self._parse_array(data, file_path, config)
        elif isinstance(data, dict):
            # Single conversation object
            return self._parse_single(data, file_path, config, index=0)
        else:
            raise ValueError(f"Unexpected JSON root type: {type(data)}")

    def _parse_array(
        self,
        items: list,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        results = []
        for idx, item in enumerate(items):
            if isinstance(item, dict):
                results.extend(self._parse_single(item, file_path, config, index=idx))
        return results

    def _parse_single(
        self,
        obj: dict,
        file_path: Path,
        config: ReplayConfig,
        index: int,
    ) -> list[ImportedConversation]:
        messages = self._extract_messages(obj, config)
        if not messages:
            return []

        conv_id = (
            obj.get("conversation_id")
            or obj.get("id")
            or obj.get("conv_id")
            or f"conv_{index}"
        )
        timestamp = (
            obj.get("timestamp")
            or obj.get("created_at")
            or obj.get("date")
        )
        # Collect any extra metadata
        meta_keys = set(obj.keys()) - {"messages", "conversation_id", "id", "conv_id", "timestamp", "created_at", "date"}
        metadata = {k: obj[k] for k in meta_keys if not isinstance(obj[k], (list, dict))}

        source = ConversationSource(
            file_path=str(file_path),
            conversation_index=index,
            original_id=str(conv_id),
            original_timestamp=str(timestamp) if timestamp else None,
            source_metadata=metadata,
        )

        return [ImportedConversation(
            source=source,
            messages=messages,
            metadata=metadata,
        )]

    def _extract_messages(self, obj: dict, config: ReplayConfig) -> list[dict[str, str]]:
        """Extract messages from various JSON structures."""
        raw_messages = (
            obj.get("messages")
            or obj.get("turns")
            or obj.get("conversation")
            or obj.get("chat")
        )

        if not raw_messages or not isinstance(raw_messages, list):
            return []

        normalized = []
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or msg.get("speaker") or msg.get("from") or ""
            # Use explicit None check — empty string "" is valid content
            # (bot responded with nothing, which quality scorer should detect)
            content = msg.get("content")
            if content is None:
                content = msg.get("message")
            if content is None:
                content = msg.get("text")
            if content is None:
                content = ""
            if role:
                normalized.append({
                    "role": self._normalize_role(str(role), config),
                    "content": str(content),
                })

        return normalized


class JSONLParser(ConversationParser):
    """Parse JSONL files (one JSON object per line)."""

    format_name = "jsonl"
    file_extensions = ["jsonl", "ndjson"]

    def parse_file(
        self,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        json_parser = JSONParser()
        results = []

        with open(file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        convos = json_parser._parse_single(obj, file_path, config, index=line_num - 1)
                        # Update source with line number
                        for c in convos:
                            c.source.line_number = line_num
                        results.extend(convos)
                except json.JSONDecodeError:
                    continue  # Skip malformed lines

        return results