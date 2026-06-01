"""
SimTest output parser — re-imports AI SimTest's own conversation JSONL
for re-evaluation with different judges, thresholds, or policies.

This enables workflows like:
  1. Run simulation with default judges
  2. Add a new workflow definition
  3. Re-evaluate the same conversations: simtest evaluate --input ./reports/conversations.jsonl --format simtest
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_simtest_engine.replay.models import ConversationSource, ImportedConversation, ReplayConfig
from ai_simtest_engine.replay.parsers.base import ConversationParser


class SimTestParser(ConversationParser):
    """Parse AI SimTest's own output files (conversations.jsonl)."""

    format_name = "simtest"
    file_extensions = ["jsonl"]  # SimTest outputs JSONL

    def parse_file(
        self,
        file_path: Path,
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        results = []

        with open(file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    conv = self._parse_simtest_record(obj, file_path, line_num)
                    if conv:
                        results.append(conv)
                except json.JSONDecodeError:
                    continue

        return results

    def _parse_simtest_record(
        self,
        obj: dict,
        file_path: Path,
        line_num: int,
    ) -> ImportedConversation | None:
        """Parse a SimTest JSONL record which has conversation + judgments."""
        # SimTest JSONL format includes 'conversation' with 'turns'
        conv_data = obj.get("conversation", obj)
        turns_data = conv_data.get("turns", [])

        if not turns_data:
            return None

        messages = []
        for turn in turns_data:
            speaker = turn.get("speaker", "")
            message = turn.get("message", "")
            if speaker and message:
                role = "user" if speaker == "user" else "bot"
                messages.append({"role": role, "content": message})

        if not messages:
            return None

        conv_id = conv_data.get("id") or conv_data.get("conversation_id") or f"simtest_{line_num}"
        persona_id = conv_data.get("persona_id", "")

        # Preserve original judgment data in metadata for comparison
        original_judgments = {}
        if "judged_turns" in obj:
            original_judgments["judged_turns_count"] = len(obj["judged_turns"])
        if "overall_score" in obj:
            original_judgments["original_score"] = obj["overall_score"]
        if "failure_modes" in obj:
            original_judgments["original_failures"] = obj["failure_modes"]

        persona_data = obj.get("persona", {})
        if persona_data:
            original_judgments["persona_name"] = persona_data.get("name", "")
            original_judgments["persona_type"] = persona_data.get("persona_type", "")

        source = ConversationSource(
            file_path=str(file_path),
            line_number=line_num,
            conversation_index=line_num - 1,
            original_id=conv_id,
            source_metadata={
                "persona_id": persona_id,
                "format": "simtest",
                **original_judgments,
            },
        )

        return ImportedConversation(
            source=source,
            messages=messages,
            metadata=original_judgments,
        )
