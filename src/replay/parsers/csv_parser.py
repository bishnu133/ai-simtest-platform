"""
CSV / TSV parser for conversation imports.

Expects tabular data with columns for conversation_id, role, message.
Groups rows by conversation_id to reconstruct conversations.

Example CSV:
    conversation_id,role,message,timestamp
    conv_001,user,"How do I open an account?",2026-03-15T10:00:00
    conv_001,bot,"You can open an account online...",2026-03-15T10:00:05
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from src.replay.models import ConversationSource, ImportedConversation, ReplayConfig
from src.replay.parsers.base import ConversationParser


class CSVParser(ConversationParser):
    """Parse CSV/TSV files with conversation data."""

    format_name = "csv"
    file_extensions = ["csv", "tsv"]

    def parse_file(
        self,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        delimiter = "\t" if file_path.suffix.lower() == ".tsv" else config.csv_delimiter

        # Read all rows
        rows_by_conv: dict[str, list[tuple[int, dict]]] = defaultdict(list)

        with open(file_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)

            if not reader.fieldnames:
                return []

            # Auto-detect columns if defaults don't match
            fields_lower = {fn.lower().strip(): fn for fn in reader.fieldnames}
            conv_id_col = self._find_column(
                fields_lower, config.csv_conversation_id_col,
                ["conversation_id", "conv_id", "id", "session_id", "chat_id"],
            )
            role_col = self._find_column(
                fields_lower, config.csv_role_col,
                ["role", "speaker", "from", "sender", "type"],
            )
            message_col = self._find_column(
                fields_lower, config.csv_message_col,
                ["message", "content", "text", "body", "response"],
            )
            timestamp_col = self._find_column(
                fields_lower, config.csv_timestamp_col or "timestamp",
                ["timestamp", "time", "created_at", "date", "datetime"],
            )

            if not role_col or not message_col:
                raise ValueError(
                    f"Cannot find required columns in {file_path}. "
                    f"Available: {list(reader.fieldnames)}. "
                    f"Need at least a role column and a message column."
                )

            for line_num, row in enumerate(reader, start=2):  # +2 for header + 0-index
                conv_id = row.get(conv_id_col, f"conv_{line_num}") if conv_id_col else f"conv_{line_num}"
                rows_by_conv[conv_id].append((line_num, row))

        # Build conversations
        results = []
        for idx, (conv_id, rows) in enumerate(rows_by_conv.items()):
            messages = []
            first_line = rows[0][0]
            timestamp = None

            for line_num, row in rows:
                role = row.get(role_col, "")
                content = row.get(message_col, "")
                if not role or not content:
                    continue
                messages.append({
                    "role": self._normalize_role(role, config),
                    "content": content,
                })
                if timestamp_col and not timestamp:
                    timestamp = row.get(timestamp_col)

            if not messages:
                continue

            source = ConversationSource(
                file_path=str(file_path),
                line_number=first_line,
                conversation_index=idx,
                original_id=conv_id,
                original_timestamp=timestamp,
            )

            results.append(ImportedConversation(
                source=source,
                messages=messages,
            ))

        return results

    def _find_column(
        self,
        fields_lower: dict[str, str],
        preferred: str,
        alternatives: list[str],
    ) -> str | None:
        """Find a column by preferred name or common alternatives."""
        # Try exact match first
        if preferred.lower().strip() in fields_lower:
            return fields_lower[preferred.lower().strip()]
        # Try alternatives
        for alt in alternatives:
            if alt.lower() in fields_lower:
                return fields_lower[alt.lower()]
        return None
