"""
Test suite for Version Tracking module (P3 #16).

Tests:
- RunFingerprint model (hash, serialization)
- FingerprintBuilder (from params, from config)
- VersionComparator (diff detection, impact rating)
- VersionHistoryManager (add, query, persist, trend)
- Integration (full workflow)
"""

try:
    import pytest
except ImportError:
    pass

import json
import tempfile
from pathlib import Path

from src.versioning import (
    RunFingerprint,
    ConfigChange,
    VersionComparison,
    VersionHistoryEntry,
    FingerprintBuilder,
    VersionComparator,
    VersionHistoryManager,
)


# ━━━ Test: RunFingerprint ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRunFingerprint:

    def test_hash_deterministic(self):
        fp1 = RunFingerprint(bot_endpoint="http://bot", num_personas=10)
        fp2 = RunFingerprint(bot_endpoint="http://bot", num_personas=10)
        assert fp1.fingerprint_hash == fp2.fingerprint_hash

    def test_hash_changes_on_config_change(self):
        fp1 = RunFingerprint(bot_endpoint="http://bot", num_personas=10)
        fp2 = RunFingerprint(bot_endpoint="http://bot", num_personas=20)
        assert fp1.fingerprint_hash != fp2.fingerprint_hash

    def test_hash_changes_on_model_change(self):
        fp1 = RunFingerprint(bot_endpoint="http://bot", persona_model="gpt-3.5")
        fp2 = RunFingerprint(bot_endpoint="http://bot", persona_model="gpt-4")
        assert fp1.fingerprint_hash != fp2.fingerprint_hash

    def test_hash_changes_on_prompt_change(self):
        fp1 = RunFingerprint(bot_prompt_hash="abc123")
        fp2 = RunFingerprint(bot_prompt_hash="def456")
        assert fp1.fingerprint_hash != fp2.fingerprint_hash

    def test_hash_ignores_timestamp(self):
        fp1 = RunFingerprint(bot_endpoint="http://bot", timestamp="2026-01-01")
        fp2 = RunFingerprint(bot_endpoint="http://bot", timestamp="2026-06-01")
        assert fp1.fingerprint_hash == fp2.fingerprint_hash

    def test_serialization_roundtrip(self):
        fp = RunFingerprint(
            simulation_id="sim_001",
            bot_endpoint="http://bot:8080",
            bot_model="gpt-4",
            num_personas=20,
            scenarios_used=["goal_shift", "prompt_injection"],
            tags={"env": "staging"},
        )
        data = fp.to_dict()
        restored = RunFingerprint.from_dict(data)
        assert restored.simulation_id == fp.simulation_id
        assert restored.bot_endpoint == fp.bot_endpoint
        assert restored.bot_model == fp.bot_model
        assert restored.scenarios_used == fp.scenarios_used
        assert restored.tags == fp.tags
        assert restored.fingerprint_hash == fp.fingerprint_hash

    def test_empty_fingerprint(self):
        fp = RunFingerprint()
        assert fp.fingerprint_hash != ""
        data = fp.to_dict()
        assert "bot" in data
        assert "config" in data

    def test_tags_in_serialization(self):
        fp = RunFingerprint(tags={"sprint": "24", "env": "prod"})
        data = fp.to_dict()
        assert data["tags"]["sprint"] == "24"


# ━━━ Test: FingerprintBuilder ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFingerprintBuilder:

    def test_build_basic(self):
        builder = FingerprintBuilder()
        fp = builder.build(
            bot_endpoint="http://bot:8080",
            num_personas=10,
            max_turns=15,
        )
        assert fp.bot_endpoint == "http://bot:8080"
        assert fp.num_personas == 10
        assert fp.timestamp != ""  # Auto-set

    def test_build_with_prompt(self):
        builder = FingerprintBuilder()
        fp = builder.build(
            bot_prompt="You are a helpful assistant for AcmeCorp.",
        )
        assert fp.bot_prompt_hash != ""
        assert len(fp.bot_prompt_hash) == 16
        assert "helpful assistant" in fp.bot_prompt_snippet

    def test_build_with_scenarios(self):
        builder = FingerprintBuilder()
        fp = builder.build(
            scenarios=["goal_shift", "prompt_injection"],
            stress_enabled=True,
        )
        assert fp.scenarios_used == ["goal_shift", "prompt_injection"]
        assert fp.stress_enabled is True

    def test_build_with_tags(self):
        builder = FingerprintBuilder()
        fp = builder.build(tags={"env": "staging", "sprint": "24"})
        assert fp.tags["env"] == "staging"

    def test_simtest_version_set(self):
        builder = FingerprintBuilder()
        fp = builder.build()
        assert fp.simtest_version == "0.2.0"

    def test_prompt_hash_deterministic(self):
        builder = FingerprintBuilder()
        fp1 = builder.build(bot_prompt="You are a helpful bot.")
        fp2 = builder.build(bot_prompt="You are a helpful bot.")
        assert fp1.bot_prompt_hash == fp2.bot_prompt_hash

    def test_different_prompts_different_hash(self):
        builder = FingerprintBuilder()
        fp1 = builder.build(bot_prompt="You are a helpful bot.")
        fp2 = builder.build(bot_prompt="You are a rude bot.")
        assert fp1.bot_prompt_hash != fp2.bot_prompt_hash


# ━━━ Test: VersionComparator ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestVersionComparator:

    def test_identical_configs(self):
        fp1 = RunFingerprint(bot_endpoint="http://bot", num_personas=10)
        fp2 = RunFingerprint(bot_endpoint="http://bot", num_personas=10)
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert result.configs_identical is True
        assert result.total_changes == 0

    def test_detects_model_change(self):
        fp1 = RunFingerprint(bot_model="gpt-3.5")
        fp2 = RunFingerprint(bot_model="gpt-4")
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert result.configs_identical is False
        assert any(c.field == "Bot Model" for c in result.changes)
        model_change = [c for c in result.changes if c.field == "Bot Model"][0]
        assert model_change.impact == "high"

    def test_detects_threshold_change(self):
        fp1 = RunFingerprint(pass_threshold=0.7)
        fp2 = RunFingerprint(pass_threshold=0.8)
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert any(c.field == "Pass Threshold" for c in result.changes)

    def test_detects_scenario_change(self):
        fp1 = RunFingerprint(scenarios_used=["goal_shift"])
        fp2 = RunFingerprint(scenarios_used=["goal_shift", "prompt_injection"])
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert any(c.field == "Scenarios" for c in result.changes)

    def test_detects_tag_change(self):
        fp1 = RunFingerprint(tags={"env": "staging"})
        fp2 = RunFingerprint(tags={"env": "prod"})
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert any("Tag" in c.field for c in result.changes)

    def test_impact_categorization(self):
        fp1 = RunFingerprint(bot_model="gpt-3.5", num_personas=10)
        fp2 = RunFingerprint(bot_model="gpt-4", num_personas=20)
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert len(result.high_impact_changes) >= 1  # model change is high
        assert result.total_changes >= 2

    def test_change_summary_generated(self):
        fp1 = RunFingerprint(bot_model="gpt-3.5")
        fp2 = RunFingerprint(bot_model="gpt-4")
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert "high-impact" in result.change_summary

    def test_serialization(self):
        fp1 = RunFingerprint(bot_model="gpt-3.5")
        fp2 = RunFingerprint(bot_model="gpt-4")
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        data = result.to_dict()
        assert "changes" in data
        assert "change_summary" in data

    def test_prompt_change_detected(self):
        fp1 = RunFingerprint(bot_prompt_hash="abc123")
        fp2 = RunFingerprint(bot_prompt_hash="def456")
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert any(c.field == "Bot Prompt" for c in result.changes)
        prompt_change = [c for c in result.changes if c.field == "Bot Prompt"][0]
        assert prompt_change.impact == "high"

    def test_multiple_changes(self):
        fp1 = RunFingerprint(
            bot_model="gpt-3.5", pass_threshold=0.7,
            persona_model="gpt-3.5", stress_enabled=False,
        )
        fp2 = RunFingerprint(
            bot_model="gpt-4", pass_threshold=0.8,
            persona_model="gpt-4", stress_enabled=True,
        )
        comp = VersionComparator()
        result = comp.compare(fp1, fp2)
        assert result.total_changes >= 4


# ━━━ Test: VersionHistoryManager ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestVersionHistoryManager:

    def test_add_entry(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        fp = RunFingerprint(simulation_id="sim_001")
        mgr.add_entry(fp, pass_rate=0.85, avg_score=0.72)
        assert mgr.count == 1

    def test_get_latest(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        for i in range(5):
            mgr.add_entry(RunFingerprint(simulation_id=f"sim_{i}"), pass_rate=i * 0.1)
        latest = mgr.get_latest(2)
        assert len(latest) == 2
        assert latest[-1].fingerprint.simulation_id == "sim_4"

    def test_get_by_id(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        mgr.add_entry(RunFingerprint(simulation_id="sim_abc"), pass_rate=0.9)
        mgr.add_entry(RunFingerprint(simulation_id="sim_xyz"), pass_rate=0.7)
        found = mgr.get_by_simulation_id("sim_abc")
        assert found is not None
        assert found.pass_rate == 0.9

    def test_get_by_tag(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        mgr.add_entry(RunFingerprint(simulation_id="s1", tags={"env": "staging"}))
        mgr.add_entry(RunFingerprint(simulation_id="s2", tags={"env": "prod"}))
        mgr.add_entry(RunFingerprint(simulation_id="s3", tags={"env": "staging"}))
        staging = mgr.get_by_tag("env", "staging")
        assert len(staging) == 2

    def test_trend(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        for i in range(5):
            mgr.add_entry(
                RunFingerprint(simulation_id=f"sim_{i}", timestamp=f"2026-03-0{i+1}"),
                pass_rate=0.5 + i * 0.1,
            )
        trend = mgr.get_trend(3)
        assert len(trend) == 3
        assert trend[-1]["pass_rate"] == 0.9

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = VersionHistoryManager(tmpdir)
            mgr.add_entry(
                RunFingerprint(simulation_id="sim_001", bot_model="gpt-4"),
                pass_rate=0.85,
                avg_score=0.72,
            )
            mgr.save()

            mgr2 = VersionHistoryManager(tmpdir)
            loaded = mgr2.load()
            assert loaded == 1
            assert mgr2.count == 1
            assert mgr2.entries[0].fingerprint.bot_model == "gpt-4"
            assert mgr2.entries[0].pass_rate == 0.85

    def test_not_found_returns_none(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        assert mgr.get_by_simulation_id("nonexistent") is None

    def test_clear(self):
        mgr = VersionHistoryManager("/tmp/test_history")
        mgr.add_entry(RunFingerprint(simulation_id="s1"))
        mgr.clear()
        assert mgr.count == 0


# ━━━ Test: Integration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIntegration:

    def test_full_workflow(self):
        """Build → Save → Load → Compare workflow."""
        builder = FingerprintBuilder()

        # Run 1: baseline
        fp1 = builder.build(
            simulation_id="run_v1",
            bot_endpoint="http://bot:8080",
            bot_prompt="You are a helpful assistant.",
            num_personas=10,
            scenarios=["goal_shift"],
        )

        # Run 2: updated config
        fp2 = builder.build(
            simulation_id="run_v2",
            bot_endpoint="http://bot:8080",
            bot_prompt="You are a professional customer service agent.",
            num_personas=20,
            scenarios=["goal_shift", "prompt_injection"],
            stress_enabled=True,
        )

        # Compare
        comparator = VersionComparator()
        diff = comparator.compare(fp1, fp2)

        assert diff.configs_identical is False
        assert diff.total_changes > 0
        assert any(c.field == "Bot Prompt" for c in diff.changes)
        assert any(c.field == "Persona Count" for c in diff.changes)
        assert any(c.field == "Scenarios" for c in diff.changes)

    def test_history_with_comparison(self):
        """Track runs in history, then compare latest two."""
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = FingerprintBuilder()
            history = VersionHistoryManager(tmpdir)

            # Run 1
            fp1 = builder.build(simulation_id="v1", bot_model="gpt-3.5", num_personas=5)
            history.add_entry(fp1, pass_rate=0.70, avg_score=0.65)

            # Run 2 (improved)
            fp2 = builder.build(simulation_id="v2", bot_model="gpt-4", num_personas=20)
            history.add_entry(fp2, pass_rate=0.90, avg_score=0.85)

            # Save and reload
            history.save()
            history2 = VersionHistoryManager(tmpdir)
            history2.load()

            # Get latest two and compare
            latest = history2.get_latest(2)
            assert len(latest) == 2

            comparator = VersionComparator()
            diff = comparator.compare(
                latest[0].fingerprint,
                latest[1].fingerprint,
            )
            assert diff.total_changes >= 2  # model + personas changed
            assert len(diff.high_impact_changes) >= 1  # model is high impact

    def test_fingerprint_in_summary_format(self):
        """Verify fingerprint can be embedded in summary.json format."""
        builder = FingerprintBuilder()
        fp = builder.build(
            simulation_id="sim_abc",
            bot_endpoint="http://bot",
            bot_model="gpt-4",
            tags={"sprint": "24"},
        )

        # Simulate embedding in summary.json
        summary = {
            "summary": {"pass_rate": 0.85},
            "version_fingerprint": fp.to_dict(),
        }

        # Simulate reading from summary.json
        data = json.dumps(summary)
        loaded = json.loads(data)
        restored_fp = RunFingerprint.from_dict(loaded["version_fingerprint"])
        assert restored_fp.bot_model == "gpt-4"
        assert restored_fp.fingerprint_hash == fp.fingerprint_hash
