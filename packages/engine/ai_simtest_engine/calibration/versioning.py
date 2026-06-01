"""
Judge Version Tracker — tracks judge prompt/config versions over time.

Enables comparing calibration accuracy across versions:
  "Was quality judge v2.0 more accurate than v1.0?"

Stores version history as a JSON file alongside calibration results.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ai_simtest_engine.calibration.models import JudgeVersion


class JudgeVersionTracker:
    """Tracks judge configuration versions over time."""

    def __init__(self):
        self._versions: dict[str, list[JudgeVersion]] = {}

    def register_version(
        self,
        judge_name: str,
        version: str,
        description: str = "",
        config_data: str = "",
    ) -> JudgeVersion:
        """Register a new version of a judge's configuration."""
        config_hash = hashlib.sha256(config_data.encode()).hexdigest()[:12]
        timestamp = datetime.now(timezone.utc).isoformat()

        jv = JudgeVersion(
            judge_name=judge_name,
            version=version,
            description=description,
            config_hash=config_hash,
            timestamp=timestamp,
        )

        self._versions.setdefault(judge_name, []).append(jv)
        return jv

    def get_versions(self, judge_name: str) -> list[JudgeVersion]:
        """Get version history for a judge."""
        return self._versions.get(judge_name, [])

    def get_latest(self, judge_name: str) -> JudgeVersion | None:
        """Get the latest version for a judge."""
        versions = self._versions.get(judge_name, [])
        return versions[-1] if versions else None

    def get_all_latest(self) -> dict[str, JudgeVersion]:
        """Get latest version for every tracked judge."""
        return {
            name: versions[-1]
            for name, versions in self._versions.items()
            if versions
        }

    def update_accuracy(
        self,
        judge_name: str,
        version: str,
        accuracy: float,
    ) -> bool:
        """Update accuracy for a specific version. Returns True if found."""
        for jv in self._versions.get(judge_name, []):
            if jv.version == version:
                jv.accuracy = accuracy
                return True
        return False

    def save_to_file(self, path: str | Path) -> None:
        """Save version history to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        for judge_name, versions in self._versions.items():
            data[judge_name] = [v.to_dict() for v in versions]

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_from_file(self, path: str | Path) -> int:
        """Load version history from JSON file. Returns total versions loaded."""
        path = Path(path)
        if not path.exists():
            return 0

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        total = 0
        for judge_name, versions_data in data.items():
            for v_data in versions_data:
                jv = JudgeVersion.from_dict(v_data)
                self._versions.setdefault(judge_name, []).append(jv)
                total += 1

        return total

    def compare_versions(
        self,
        judge_name: str,
        version_a: str,
        version_b: str,
    ) -> dict | None:
        """Compare accuracy between two versions."""
        va = vb = None
        for jv in self._versions.get(judge_name, []):
            if jv.version == version_a:
                va = jv
            if jv.version == version_b:
                vb = jv

        if not va or not vb:
            return None

        return {
            "judge": judge_name,
            "version_a": {"version": va.version, "accuracy": va.accuracy},
            "version_b": {"version": vb.version, "accuracy": vb.accuracy},
            "delta": round(vb.accuracy - va.accuracy, 4),
            "improved": vb.accuracy > va.accuracy,
        }
