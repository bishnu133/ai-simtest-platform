"""
Version Tracking Module — P3 #16

Tracks what was tested per simulation run so compare mode
can answer "why did scores change?" not just "scores changed."

Place under: src/versioning/

Usage:
    from ai_simtest_engine.versioning import FingerprintBuilder, VersionComparator, VersionHistoryManager

    # Capture fingerprint at run start
    builder = FingerprintBuilder()
    fingerprint = builder.build(bot_endpoint="http://...", num_personas=20, ...)

    # Compare two runs
    comparator = VersionComparator()
    diff = comparator.compare(old_fingerprint, new_fingerprint)
    print(diff.change_summary)

    # Track history
    history = VersionHistoryManager("reports")
    history.load()
    history.add_entry(fingerprint, pass_rate=0.85, avg_score=0.72)
    history.save()
"""

from ai_simtest_engine.versioning.models import (
    RunFingerprint,
    ConfigChange,
    VersionComparison,
    VersionHistoryEntry,
)
from ai_simtest_engine.versioning.builder import FingerprintBuilder
from ai_simtest_engine.versioning.comparator import VersionComparator
from ai_simtest_engine.versioning.history import VersionHistoryManager

__all__ = [
    "RunFingerprint",
    "ConfigChange",
    "VersionComparison",
    "VersionHistoryEntry",
    "FingerprintBuilder",
    "VersionComparator",
    "VersionHistoryManager",
]
