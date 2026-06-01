"""
Version Comparator — diffs two RunFingerprints to explain score changes.

Categorizes changes by impact level:
  HIGH — likely to cause score changes (model, prompt, threshold changes)
  MEDIUM — may affect scores (persona count, turn count, scenarios)
  LOW — unlikely to affect scores (tags, name, timestamp)
"""

from __future__ import annotations

from src.versioning.models import (
    RunFingerprint,
    ConfigChange,
    VersionComparison,
)


# Field definitions: (display_name, category, impact_level)
TRACKED_FIELDS: dict[str, tuple[str, str, str]] = {
    # Bot fields (HIGH impact)
    "bot_endpoint": ("Bot Endpoint", "bot", "high"),
    "bot_format": ("Bot Format", "bot", "medium"),
    "bot_model": ("Bot Model", "bot", "high"),
    "bot_prompt_hash": ("Bot Prompt", "bot", "high"),
    # Config fields
    "num_personas": ("Persona Count", "config", "medium"),
    "max_turns": ("Max Turns", "config", "medium"),
    "min_turns": ("Min Turns", "config", "low"),
    "pass_threshold": ("Pass Threshold", "config", "high"),
    "warn_threshold": ("Warn Threshold", "config", "high"),
    "stress_enabled": ("Stress Testing", "config", "medium"),
    # LLM fields
    "persona_model": ("Persona LLM", "llm", "high"),
    "simulator_model": ("Simulator LLM", "llm", "high"),
    "judge_model": ("Quality Judge LLM", "llm", "high"),
    # Judge fields
    "grounding_threshold": ("Grounding Threshold", "judges", "high"),
    "safety_toxicity_threshold": ("Toxicity Threshold", "judges", "high"),
    # Tool version
    "simtest_version": ("SimTest Version", "tool", "medium"),
}


class VersionComparator:
    """Compares two RunFingerprints and produces a structured diff."""

    def compare(
        self,
        baseline: RunFingerprint,
        current: RunFingerprint,
    ) -> VersionComparison:
        """
        Compare two fingerprints and identify all changes.

        Returns VersionComparison with categorized, impact-rated changes.
        """
        result = VersionComparison(
            baseline_id=baseline.simulation_id,
            current_id=current.simulation_id,
            baseline_hash=baseline.fingerprint_hash,
            current_hash=current.fingerprint_hash,
        )

        if baseline.fingerprint_hash == current.fingerprint_hash:
            # Config hash matches, but still check tags (not in hash)
            old_tags = baseline.tags or {}
            new_tags = current.tags or {}
            if old_tags == new_tags:
                result.configs_identical = True
                result.change_summary = "Configurations are identical."
                return result

        changes: list[ConfigChange] = []

        # Compare scalar fields
        for field_name, (display, category, impact) in TRACKED_FIELDS.items():
            old_val = getattr(baseline, field_name, None)
            new_val = getattr(current, field_name, None)

            if old_val != new_val:
                changes.append(ConfigChange(
                    field=display,
                    old_value=str(old_val) if old_val else "(empty)",
                    new_value=str(new_val) if new_val else "(empty)",
                    category=category,
                    impact=impact,
                ))

        # Compare list fields
        for field_name, display, category in [
            ("scenarios_used", "Scenarios", "config"),
            ("topics", "Topics", "config"),
            ("judges_enabled", "Judges Enabled", "judges"),
        ]:
            old_list = sorted(getattr(baseline, field_name, []))
            new_list = sorted(getattr(current, field_name, []))
            if old_list != new_list:
                changes.append(ConfigChange(
                    field=display,
                    old_value=", ".join(old_list) if old_list else "(none)",
                    new_value=", ".join(new_list) if new_list else "(none)",
                    category=category,
                    impact="medium",
                ))

        # Compare tags
        old_tags = baseline.tags or {}
        new_tags = current.tags or {}
        if old_tags != new_tags:
            all_keys = set(old_tags.keys()) | set(new_tags.keys())
            for key in sorted(all_keys):
                old_v = old_tags.get(key, "(not set)")
                new_v = new_tags.get(key, "(removed)")
                if old_v != new_v:
                    changes.append(ConfigChange(
                        field=f"Tag: {key}",
                        old_value=old_v,
                        new_value=new_v,
                        category="tags",
                        impact="low",
                    ))

        result.changes = changes
        result.configs_identical = len(changes) == 0

        # Generate summary
        result.change_summary = self._build_summary(changes)

        return result

    def _build_summary(self, changes: list[ConfigChange]) -> str:
        """Build a human-readable change summary."""
        if not changes:
            return "No configuration changes detected."

        high = [c for c in changes if c.impact == "high"]
        medium = [c for c in changes if c.impact == "medium"]
        low = [c for c in changes if c.impact == "low"]

        parts = []
        if high:
            names = ", ".join(c.field for c in high)
            parts.append(f"{len(high)} high-impact change(s): {names}")
        if medium:
            names = ", ".join(c.field for c in medium)
            parts.append(f"{len(medium)} medium-impact change(s): {names}")
        if low:
            parts.append(f"{len(low)} low-impact change(s)")

        return ". ".join(parts) + "."
