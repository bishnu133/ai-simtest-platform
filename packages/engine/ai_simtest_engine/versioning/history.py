"""
Version History Manager — persistent log of all simulation runs.

Append-only: every simulation run adds an entry with its fingerprint
and results. Enables trend analysis across runs.

Storage: Simple JSON file (version_history.json) in the output directory.
Designed to be upgraded to PostgreSQL later (P4 #17) without API changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_simtest_engine.versioning.models import (
    RunFingerprint,
    VersionHistoryEntry,
)


class VersionHistoryManager:
    """Manages the append-only version history log."""

    DEFAULT_FILENAME = "version_history.json"

    def __init__(self, history_dir: str | Path = "reports"):
        self.history_dir = Path(history_dir)
        self._entries: list[VersionHistoryEntry] = []

    @property
    def entries(self) -> list[VersionHistoryEntry]:
        return list(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def add_entry(
        self,
        fingerprint: RunFingerprint,
        pass_rate: float = 0.0,
        avg_score: float = 0.0,
        critical_failures: int = 0,
        summary_path: str = "",
    ) -> VersionHistoryEntry:
        """Add a new entry to the history."""
        entry = VersionHistoryEntry(
            fingerprint=fingerprint,
            pass_rate=pass_rate,
            avg_score=avg_score,
            critical_failures=critical_failures,
            summary_path=summary_path,
        )
        self._entries.append(entry)
        return entry

    def get_latest(self, n: int = 1) -> list[VersionHistoryEntry]:
        """Get the N most recent entries."""
        return self._entries[-n:]

    def get_by_simulation_id(self, sim_id: str) -> VersionHistoryEntry | None:
        """Find an entry by simulation ID."""
        for entry in reversed(self._entries):
            if entry.fingerprint.simulation_id == sim_id:
                return entry
        return None

    def get_by_tag(self, key: str, value: str) -> list[VersionHistoryEntry]:
        """Find entries matching a specific tag."""
        return [
            e for e in self._entries
            if e.fingerprint.tags.get(key) == value
        ]

    def get_trend(self, last_n: int = 10) -> list[dict]:
        """Get pass_rate and avg_score trend for the last N runs."""
        entries = self._entries[-last_n:]
        return [
            {
                "simulation_id": e.fingerprint.simulation_id,
                "timestamp": e.fingerprint.timestamp,
                "pass_rate": round(e.pass_rate, 4),
                "avg_score": round(e.avg_score, 4),
                "critical_failures": e.critical_failures,
                "fingerprint_hash": e.fingerprint.fingerprint_hash,
            }
            for e in entries
        ]

    def save(self, filename: str | None = None) -> Path:
        """Save history to JSON file. Returns the file path."""
        path = self.history_dir / (filename or self.DEFAULT_FILENAME)
        self.history_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "version": "1.0",
            "total_entries": len(self._entries),
            "entries": [e.to_dict() for e in self._entries],
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return path

    def load(self, filename: str | None = None) -> int:
        """Load history from JSON file. Returns count loaded."""
        path = self.history_dir / (filename or self.DEFAULT_FILENAME)
        if not path.exists():
            return 0

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        entries_data = data.get("entries", [])
        for item in entries_data:
            try:
                entry = VersionHistoryEntry.from_dict(item)
                self._entries.append(entry)
            except (KeyError, ValueError):
                continue

        return len(entries_data)

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
