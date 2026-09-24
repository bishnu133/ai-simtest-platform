"""
Conversation Loader — the main entry point for importing conversations.

Orchestrates:
  1. File discovery (single file, directory with rglob, glob patterns)
  2. Format detection or explicit selection
  3. Parsing via the appropriate parser
  4. Optional PII masking via Presidio
  5. Normalization into src.models.Conversation objects

Review fixes applied:
  #2 — True recursive discovery via rglob
  #3 — Parse error reporting in LoadSummary
  #6 — PII report with full transparency
  #10 — Unknown roles tracked in metadata, not silently dropped
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_simtest_engine.core.logging import get_logger
from ai_simtest_engine.models import Conversation, Persona, PersonaType, Turn
from ai_simtest_engine.replay.models import (
    ConversationSource,
    ImportedConversation,
    InputFormat,
    LoadSummary,
    PIIMaskingStrategy,
    PIIReport,
    ReplayConfig,
)
from ai_simtest_engine.replay.parsers import detect_format, get_parser

logger = get_logger(__name__)


class ConversationLoader:
    """
    Loads conversations from external sources, masks PII, and normalizes
    them into AI SimTest's internal Conversation + Persona models.
    """

    def load(
        self,
        config: ReplayConfig,
    ) -> tuple[list[Conversation], dict[str, Persona], list[ConversationSource], LoadSummary, PIIReport]:
        """
        Load and normalize conversations from configured input paths.

        Returns:
            - List of normalized Conversation objects
            - Dict mapping persona_id → synthetic Persona
            - List of ConversationSource objects for traceability
            - LoadSummary with file discovery / parse error stats
            - PIIReport with masking transparency
        """
        load_summary = LoadSummary()

        # 1. Discover files
        files = self._discover_files(config.input_paths)
        load_summary.files_discovered = len(files)

        if not files:
            raise ValueError(
                f"No input files found at: {config.input_paths}"
            )

        logger.info("files_discovered", count=len(files), paths=[str(f) for f in files])

        # 2. Parse all files — track successes and failures (#3)
        imported: list[ImportedConversation] = []
        for file_path in files:
            fmt = self._resolve_format(file_path, config.input_format)
            parser = get_parser(fmt)
            try:
                convos = parser.parse_file(file_path, config)
                imported.extend(convos)
                load_summary.files_parsed_ok += 1
                logger.info("file_parsed", path=str(file_path), conversations=len(convos))
            except Exception as e:
                load_summary.files_failed += 1
                load_summary.parse_errors.append({
                    "file": str(file_path),
                    "error": str(e),
                })
                logger.error("parse_error", path=str(file_path), error=str(e))

        # Strict mode: abort if any parse failures (#3)
        if config.fail_on_parse_error and load_summary.files_failed > 0:
            raise ValueError(
                f"Parse errors in {load_summary.files_failed} file(s) "
                f"(--fail-on-parse-error is set): "
                f"{[e['file'] for e in load_summary.parse_errors]}"
            )

        if not imported:
            raise ValueError(
                f"No conversations found in {len(files)} file(s). "
                f"Check input format and file contents."
            )

        load_summary.conversations_loaded = len(imported)
        logger.info("total_imported", conversations=len(imported))

        # 3. Quality scoring (#R1) — assess each conversation
        imported = self._score_quality(imported)
        load_summary.quality_complete = sum(1 for ic in imported if ic.quality.value == "complete")
        load_summary.quality_partial = sum(1 for ic in imported if ic.quality.value == "partial")
        load_summary.quality_low = sum(1 for ic in imported if ic.quality.value == "low")

        # 4. Conversation filtering (#R5)
        imported = self._apply_filters(imported, config)
        load_summary.conversations_after_filter = len(imported)

        if not imported:
            raise ValueError(
                f"No conversations remaining after filtering "
                f"(loaded {load_summary.conversations_loaded}, "
                f"all filtered out). Check --min-turns, --max-turns, --contains, --sample."
            )

        # 5. PII masking
        pii_report = PIIReport()
        if config.pii_masking.strategy != PIIMaskingStrategy.NONE:
            pii_report.masking_enabled = True
            pii_report.masking_strategy = config.pii_masking.strategy.value
            imported, pii_report = self._apply_pii_masking(imported, config, pii_report)

        # 6. Normalize to Conversation + Persona
        conversations, personas, sources, unknown_roles = self._normalize(imported)
        load_summary.unknown_roles_found = unknown_roles

        logger.info(
            "normalization_complete",
            conversations=len(conversations),
            personas=len(personas),
            pii_detections=pii_report.total_entities_detected,
            unknown_roles=len(unknown_roles),
        )

        return conversations, personas, sources, load_summary, pii_report

    def _discover_files(self, input_paths: list[str]) -> list[Path]:
        """Find all input files from paths (files, directories, globs).

        Fix #2: Uses rglob for truly recursive directory scanning.
        """
        files = []
        for path_str in input_paths:
            path = Path(path_str)
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                # Truly recursive scan (#2)
                for ext in (".json", ".jsonl", ".csv", ".tsv", ".txt", ".md", ".log"):
                    files.extend(sorted(path.rglob(f"*{ext}")))
            else:
                # Try glob pattern
                parent = path.parent if path.parent.exists() else Path(".")
                matches = sorted(parent.glob(path.name))
                files.extend(matches)
        return files

    def _score_quality(
        self,
        conversations: list[ImportedConversation],
    ) -> list[ImportedConversation]:
        """Score each conversation's parse quality (#R1).

        Quality levels:
          - COMPLETE: both user and bot messages present, ≥2 turns
          - PARTIAL: only one side, or very few messages, or empty bot responses
          - LOW_CONFIDENCE: unknown roles, no valid messages, malformed
        """
        from ai_simtest_engine.replay.models import ConversationQuality

        for conv in conversations:
            warnings = []
            roles = set(m["role"] for m in conv.messages)
            contents = [m["content"] for m in conv.messages]

            has_user = "user" in roles
            has_bot = "bot" in roles
            unknown_roles = roles - {"user", "bot"}
            empty_msgs = sum(1 for c in contents if not c.strip())
            total = len(conv.messages)

            # Check for issues
            if not has_user:
                warnings.append("No user messages found")
            if not has_bot:
                warnings.append("No bot messages found")
            if unknown_roles:
                warnings.append(f"Unknown roles: {', '.join(unknown_roles)}")
            if empty_msgs > 0:
                warnings.append(f"{empty_msgs} empty message(s)")
            if total < 2:
                warnings.append("Fewer than 2 messages")

            # Assign quality level
            if not has_user or not has_bot:
                conv.quality = ConversationQuality.LOW_CONFIDENCE
            elif unknown_roles or empty_msgs > 0 or total < 2:
                conv.quality = ConversationQuality.PARTIAL
            else:
                conv.quality = ConversationQuality.COMPLETE

            conv.quality_warnings = warnings

        return conversations

    def _apply_filters(
        self,
        conversations: list[ImportedConversation],
        config: ReplayConfig,
    ) -> list[ImportedConversation]:
        """Apply conversation-level filters (#R5).

        Supports: --sample N, --filter-min-turns, --filter-max-turns, --filter-contains
        """
        import random

        filtered = conversations

        # Filter by turn count
        if config.filter_min_turns is not None:
            filtered = [c for c in filtered if len(c.messages) >= config.filter_min_turns]

        if config.filter_max_turns is not None:
            filtered = [c for c in filtered if len(c.messages) <= config.filter_max_turns]

        # Filter by content
        if config.filter_contains is not None:
            keyword = config.filter_contains.lower()
            filtered = [
                c for c in filtered
                if any(keyword in m["content"].lower() for m in c.messages)
            ]

        # Random sample
        if config.sample_size is not None and len(filtered) > config.sample_size:
            filtered = random.sample(filtered, config.sample_size)

        logger.info(
            "filtering_applied",
            before=len(conversations),
            after=len(filtered),
            sample=config.sample_size,
            min_turns=config.filter_min_turns,
            max_turns=config.filter_max_turns,
            contains=config.filter_contains,
        )

        return filtered

    def _resolve_format(self, file_path: Path, configured_format: InputFormat) -> str:
        """Determine the format to use for a file."""
        if configured_format != InputFormat.AUTO:
            return configured_format.value
        return detect_format(file_path)

    def _apply_pii_masking(
        self,
        conversations: list[ImportedConversation],
        config: ReplayConfig,
        pii_report: PIIReport,
    ) -> tuple[list[ImportedConversation], PIIReport]:
        """Apply PII detection/masking to all conversations (#6 — full transparency)."""
        pii_config = config.pii_masking

        try:
            from presidio_analyzer import AnalyzerEngine
            analyzer = AnalyzerEngine()
        except ImportError:
            logger.warning("presidio_not_available", hint="PII masking requires presidio-analyzer")
            return conversations, pii_report

        mask_format = pii_config.mask_format

        for conv in conversations:
            conv_had_pii = False
            for msg in conv.messages:
                text = msg["content"]
                results = analyzer.analyze(
                    text=text,
                    language="en",
                    entities=pii_config.entities,
                )

                # Filter by confidence
                detections = [r for r in results if r.score >= pii_config.confidence_threshold]

                if detections:
                    conv_had_pii = True
                    pii_report.total_entities_detected += len(detections)

                    for d in detections:
                        entity_type = d.entity_type
                        pii_report.detections_by_type[entity_type] = (
                            pii_report.detections_by_type.get(entity_type, 0) + 1
                        )
                        conv.source.pii_detections.append({
                            "entity_type": entity_type,
                            "score": round(d.score, 2),
                            "start": d.start,
                            "end": d.end,
                        })

                    # Mask if configured
                    if pii_config.strategy == PIIMaskingStrategy.MASK:
                        sorted_detections = sorted(detections, key=lambda d: d.start, reverse=True)
                        for d in sorted_detections:
                            replacement = mask_format.format(entity_type=d.entity_type)
                            text = text[:d.start] + replacement + text[d.end:]
                            pii_report.total_fields_redacted += 1
                        msg["content"] = text

            if conv_had_pii:
                pii_report.conversations_with_pii += 1
            else:
                pii_report.conversations_clean += 1

        return conversations, pii_report

    def _normalize(
        self,
        imported: list[ImportedConversation],
    ) -> tuple[list[Conversation], dict[str, Persona], list[ConversationSource], list[str]]:
        """Convert imported conversations to AI SimTest internal models.

        Fix #10: Unknown roles are recorded in conversation metadata,
        not silently dropped. User and bot turns are still the only
        ones judges evaluate, but the full transcript is preserved.
        """
        conversations = []
        personas = {}
        sources = []
        all_unknown_roles: set[str] = set()

        for idx, ic in enumerate(imported):
            conv_id = f"replay_{ic.source.original_id or idx}"
            persona_id = f"replay_persona_{idx}"

            # Build turns — keep user/bot, track unknowns (#10)
            turns = []
            unknown_in_conv = []
            for msg in ic.messages:
                role = msg["role"]
                if role in ("user", "bot"):
                    turns.append(Turn(speaker=role, message=msg["content"]))
                else:
                    # Record unknown role in metadata instead of dropping (#10)
                    unknown_in_conv.append({"role": role, "content": msg["content"]})
                    all_unknown_roles.add(role)

            if not turns:
                continue

            # Store unknown roles in conversation metadata
            conv_metadata = {
                "source_file": ic.source.file_path,
                "source_line": ic.source.line_number,
                "original_id": ic.source.original_id,
                "replay_mode": True,
            }
            if unknown_in_conv:
                conv_metadata["unknown_role_messages"] = unknown_in_conv
                conv_metadata["unknown_roles_count"] = len(unknown_in_conv)

            conversation = Conversation(
                id=conv_id,
                persona_id=persona_id,
                turns=turns,
                metadata=conv_metadata,
            )
            conversations.append(conversation)

            # Create synthetic persona
            persona_name = ic.metadata.get("persona_name") or f"Real User #{idx + 1}"
            persona = Persona(
                id=persona_id,
                name=persona_name,
                role="real_user",
                goals=["imported_conversation"],
                persona_type=PersonaType.STANDARD,
                tone="unknown",
                system_prompt="[Real production conversation — imported for evaluation]",
            )
            personas[persona_id] = persona

            # Attach quality info to source metadata for evaluator access (#R1)
            ic.source.source_metadata["quality"] = ic.quality.value
            ic.source.source_metadata["quality_warnings"] = ic.quality_warnings
            sources.append(ic.source)

        return conversations, personas, sources, sorted(all_unknown_roles)