"""
Parser registry — manages available parsers and auto-detects format.
"""

from __future__ import annotations

from pathlib import Path

from src.replay.models import InputFormat
from src.replay.parsers.base import ConversationParser
from src.replay.parsers.csv_parser import CSVParser
from src.replay.parsers.json_parser import JSONLParser, JSONParser
from src.replay.parsers.simtest_parser import SimTestParser
from src.replay.parsers.text_parser import TextParser


# ============================================================
# Parser Registry
# ============================================================

_PARSERS: dict[str, ConversationParser] = {
    "json": JSONParser(),
    "jsonl": JSONLParser(),
    "csv": CSVParser(),
    "tsv": CSVParser(),          # TSV uses same parser (auto-detects delimiter)
    "text": TextParser(),
    "markdown": TextParser(),    # Markdown uses same parser
    "simtest": SimTestParser(),
}

# Extension → format mapping for auto-detect
_EXT_MAP: dict[str, str] = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".csv": "csv",
    ".tsv": "tsv",
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".log": "text",
    ".chat": "text",
}


def get_parser(format_name: str) -> ConversationParser:
    """Get a parser by format name."""
    parser = _PARSERS.get(format_name.lower())
    if not parser:
        available = sorted(_PARSERS.keys())
        raise ValueError(
            f"Unknown format: '{format_name}'. Available: {available}"
        )
    return parser


def detect_format(file_path: Path) -> str:
    """Auto-detect format from file extension."""
    ext = file_path.suffix.lower()
    fmt = _EXT_MAP.get(ext)
    if not fmt:
        raise ValueError(
            f"Cannot auto-detect format for '{file_path.name}'. "
            f"Supported extensions: {sorted(_EXT_MAP.keys())}. "
            f"Use --format to specify explicitly."
        )
    return fmt


def register_parser(format_name: str, parser: ConversationParser, extensions: list[str] | None = None) -> None:
    """Register a custom parser (for enterprise/plugin use)."""
    _PARSERS[format_name.lower()] = parser
    if extensions:
        for ext in extensions:
            _EXT_MAP[ext if ext.startswith(".") else f".{ext}"] = format_name.lower()
