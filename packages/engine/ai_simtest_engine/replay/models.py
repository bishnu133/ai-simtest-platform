"""
Conversation Replay Models — Data structures for importing, evaluating,
and reporting on real production conversations.

Loosely coupled: these models reference src.models types but don't
inherit from or modify them. The replay module can be updated or
replaced independently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# Enums
# ============================================================

class ReplayMode(str, Enum):
    """How to process imported conversations."""
    EVALUATE = "evaluate"       # Run judges on existing bot responses (default)
    RETEST = "retest"           # Send user messages to a bot, get new responses, judge those
    HYBRID = "hybrid"           # Evaluate existing + generate variations + test variations


class InputFormat(str, Enum):
    """Supported input file formats."""
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"
    TSV = "tsv"
    TEXT = "text"
    MARKDOWN = "markdown"
    SIMTEST = "simtest"         # AI SimTest output (conversations.jsonl)
    AUTO = "auto"               # Auto-detect from file extension


class PIIMaskingStrategy(str, Enum):
    """How to handle PII in imported conversations."""
    NONE = "none"               # No masking (trust the user)
    DETECT_ONLY = "detect"      # Detect and report PII but don't modify
    MASK = "mask"               # Replace PII with placeholders ([NAME], [EMAIL], etc.)


class ConversationQuality(str, Enum):
    """Parse confidence / data quality rating per conversation (#R1)."""
    COMPLETE = "complete"       # Both sides present, well-formed
    PARTIAL = "partial"         # Missing one side, or very short
    LOW_CONFIDENCE = "low"      # Parsing issues, unknown roles, malformed


# ============================================================
# Configuration
# ============================================================

class PIIMaskingConfig(BaseModel):
    """Configuration for PII masking on imported conversations."""
    strategy: PIIMaskingStrategy = PIIMaskingStrategy.NONE
    entities: list[str] = Field(default_factory=lambda: [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD",
        "US_SSN", "IP_ADDRESS", "IBAN_CODE",
    ])
    confidence_threshold: float = 0.7
    mask_format: str = "[{entity_type}]"   # e.g. [NAME], [EMAIL]


class ReplayConfig(BaseModel):
    """Complete configuration for a conversation replay evaluation."""
    # Input
    input_paths: list[str]
    input_format: InputFormat = InputFormat.AUTO
    # Speaker identification (for text/csv formats)
    user_speaker_names: list[str] = Field(default_factory=lambda: [
        "user", "customer", "human", "client",
    ])
    bot_speaker_names: list[str] = Field(default_factory=lambda: [
        "bot", "assistant", "agent", "ai", "system",
    ])
    speaker_pattern: str | None = None   # e.g. "Customer:|Bot:" for text parsing
    # CSV-specific column mapping
    csv_conversation_id_col: str = "conversation_id"
    csv_role_col: str = "role"
    csv_message_col: str = "message"
    csv_timestamp_col: str | None = "timestamp"
    csv_delimiter: str = ","

    # Evaluation
    mode: ReplayMode = ReplayMode.EVALUATE
    documentation: str = ""
    pass_threshold: float = 0.7
    warn_threshold: float = 0.5

    # PII
    pii_masking: PIIMaskingConfig = Field(default_factory=PIIMaskingConfig)

    # Re-test mode (send to bot)
    bot_endpoint: str | None = None
    bot_api_key: str | None = None
    bot_format: str = "openai"

    # Hybrid mode
    num_variations: int = 5

    # CI/CD gates
    fail_thresholds: dict[str, float] = Field(default_factory=dict)
    # e.g. {"safety": 0.95, "quality": 0.70}

    # Output
    output_dir: str = "./reports/evaluate"
    export_formats: list[str] = Field(default_factory=lambda: [
        "jsonl", "csv", "summary", "html",
    ])

    # Policy / workflow
    policy: str | None = None
    workflow: str | None = None
    no_workflow: bool = False

    # Strict mode: fail if any parse errors (#3)
    fail_on_parse_error: bool = False

    # Configurable judge selection (#9)
    judges: list[str] | None = None  # None = all, or ["safety", "quality"]

    # Minimum sample gate protection (#R2)
    min_conversations_for_gate: int = 1   # Don't enforce gates below this count
    min_turns_for_gate: int = 1           # Don't enforce gates below this turn count

    # Conversation filtering (#R5)
    sample_size: int | None = None        # Random sample N conversations (None = all)
    filter_min_turns: int | None = None   # Skip conversations with fewer turns
    filter_max_turns: int | None = None   # Skip conversations with more turns
    filter_contains: str | None = None    # Only include conversations containing this text


# ============================================================
# Source tracking
# ============================================================

class ConversationSource(BaseModel):
    """Tracks where an imported conversation came from."""
    file_path: str
    line_number: int | None = None     # For JSONL/CSV
    conversation_index: int = 0         # Position within the file
    original_id: str | None = None      # ID from the source system
    original_timestamp: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    pii_detections: list[dict[str, Any]] = Field(default_factory=list)


class ImportedConversation(BaseModel):
    """A conversation loaded from an external source, before normalization."""
    source: ConversationSource
    messages: list[dict[str, str]]     # [{"role": "user", "content": "..."}, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality: ConversationQuality = ConversationQuality.COMPLETE
    quality_warnings: list[str] = Field(default_factory=list)


# ============================================================
# Replay results
# ============================================================

class ConversationEvalResult(BaseModel):
    """Evaluation result for a single imported conversation."""
    source: ConversationSource
    conversation_id: str
    overall_score: float = 0.0
    pass_rate: float = 0.0
    total_turns: int = 0
    issues: list[str] = Field(default_factory=list)
    judge_scores: dict[str, float] = Field(default_factory=dict)
    pii_masked: bool = False
    pii_findings: list[str] = Field(default_factory=list)
    quality: ConversationQuality = ConversationQuality.COMPLETE
    quality_warnings: list[str] = Field(default_factory=list)


class LoadSummary(BaseModel):
    """Summary of the file loading phase — surfaces parse failures and quality."""
    files_discovered: int = 0
    files_parsed_ok: int = 0
    files_failed: int = 0
    parse_errors: list[dict[str, str]] = Field(default_factory=list)
    conversations_loaded: int = 0
    conversations_after_filter: int = 0   # After filtering (#R5)
    unknown_roles_found: list[str] = Field(default_factory=list)
    # Quality distribution (#R1)
    quality_complete: int = 0
    quality_partial: int = 0
    quality_low: int = 0


class PIIReport(BaseModel):
    """PII masking transparency report (#6)."""
    masking_enabled: bool = False
    masking_strategy: str = "none"
    total_entities_detected: int = 0
    total_fields_redacted: int = 0
    detections_by_type: dict[str, int] = Field(default_factory=dict)
    conversations_with_pii: int = 0
    conversations_clean: int = 0


class ReplayResult(BaseModel):
    """Complete result of a conversation replay evaluation."""
    config: ReplayConfig
    total_conversations: int = 0
    total_turns_evaluated: int = 0
    overall_pass_rate: float = 0.0
    overall_avg_score: float = 0.0
    score_by_judge: dict[str, float] = Field(default_factory=dict)
    conversation_results: list[ConversationEvalResult] = Field(default_factory=list)
    pii_summary: dict[str, int] = Field(default_factory=dict)
    pii_report: PIIReport = Field(default_factory=PIIReport)
    load_summary: LoadSummary = Field(default_factory=LoadSummary)
    gate_results: dict[str, bool] = Field(default_factory=dict)
    gate_passed: bool = True
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    execution_time_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)