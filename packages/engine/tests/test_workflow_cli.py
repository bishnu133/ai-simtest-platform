"""
Tests for P3 #17: Workflow Judge CLI Integration

Tests the `simtest workflows` command and `--workflow` flag on `simtest run`.
Follows the same pattern as test_scenarios_cli.py.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml
from click.testing import CliRunner

from ai_simtest_engine.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def sample_workflow_yaml(tmp_path):
    """Create a temp YAML workflow file."""
    data = {
        "name": "Test Custom Workflow",
        "domain": "testing",
        "description": "A test workflow for CLI testing",
        "steps": [
            {"id": "s1", "name": "Step One", "required": True, "detection_hints": ["hello"]},
            {"id": "s2", "name": "Step Two", "required": False, "detection_hints": ["world"]},
        ],
        "hard_rules": [
            {"id": "r1", "name": "No Bad", "rule_type": "forbidden_phrase", "values": ["bad word"], "severity": "high"},
        ],
        "success_conditions": [
            {"id": "c1", "description": "User is helped"},
        ],
    }
    path = tmp_path / "test_workflow.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)


# ============================================================================
# simtest workflows — List command
# ============================================================================

class TestWorkflowsListCommand:

    def test_list_all_workflows(self, runner):
        result = runner.invoke(main, ["workflows"])
        assert result.exit_code == 0
        assert "banking_account_opening" in result.output
        assert "healthcare" in result.output
        assert "ecommerce_refund" in result.output
        assert "password_reset" in result.output
        assert "banking_card_block" in result.output

    def test_list_filter_by_domain(self, runner):
        result = runner.invoke(main, ["workflows", "--domain", "banking"])
        assert result.exit_code == 0
        assert "banking_account_opening" in result.output
        assert "banking_card_block" in result.output
        # Healthcare should NOT appear
        assert "healthcare_appointment" not in result.output

    def test_list_verbose(self, runner):
        result = runner.invoke(main, ["workflows", "-v"])
        assert result.exit_code == 0
        assert "Steps:" in result.output
        assert "Hard Rules:" in result.output
        assert "CRITICAL" in result.output or "critical" in result.output

    def test_list_verbose_with_domain(self, runner):
        result = runner.invoke(main, ["workflows", "--domain", "healthcare", "-v"])
        assert result.exit_code == 0
        assert "healthcare" in result.output
        assert "Activation" in result.output or "appointment" in result.output

    def test_list_shows_usage_hint(self, runner):
        result = runner.invoke(main, ["workflows"])
        assert result.exit_code == 0
        assert "--workflow" in result.output


# ============================================================================
# simtest workflows --validate
# ============================================================================

class TestWorkflowsValidateCommand:

    def test_validate_valid_yaml(self, runner, sample_workflow_yaml):
        result = runner.invoke(main, ["workflows", "--validate", sample_workflow_yaml])
        assert result.exit_code == 0
        assert "Valid workflow" in result.output or "✅" in result.output
        assert "Test Custom Workflow" in result.output

    def test_validate_nonexistent_file(self, runner):
        result = runner.invoke(main, ["workflows", "--validate", "/tmp/ghost_workflow.yaml"])
        assert result.exit_code == 0  # Click doesn't fail, but message shows error
        assert "Invalid" in result.output or "not found" in result.output

    def test_validate_bad_yaml(self, runner, tmp_path):
        bad_path = tmp_path / "bad.yaml"
        with open(bad_path, "w") as f:
            f.write("name: 'Missing closing quote")
        result = runner.invoke(main, ["workflows", "--validate", str(bad_path)])
        assert result.exit_code == 0
        assert "Invalid" in result.output or "error" in result.output.lower()

    def test_validate_workflow_with_bad_weights(self, runner, tmp_path):
        data = {
            "name": "Bad Weights",
            "steps": [{"id": "s1", "name": "S1"}],
            "step_weight": 0.6,
            "rule_weight": 0.6,
            "condition_weight": 0.6,
        }
        path = tmp_path / "bad_weights.yaml"
        with open(path, "w") as f:
            yaml.dump(data, f)
        result = runner.invoke(main, ["workflows", "--validate", str(path)])
        assert result.exit_code == 0
        assert "Warning" in result.output or "weights" in result.output.lower()


# ============================================================================
# simtest workflows --export
# ============================================================================

class TestWorkflowsExportCommand:

    def test_export_built_in(self, runner):
        result = runner.invoke(main, ["workflows", "--export", "banking_account_opening"])
        assert result.exit_code == 0
        # Output should be valid YAML
        assert "banking" in result.output.lower()
        assert "steps:" in result.output or "steps" in result.output

    def test_export_unknown_workflow(self, runner):
        result = runner.invoke(main, ["workflows", "--export", "nonexistent_workflow"])
        assert result.exit_code == 0
        assert "Unknown" in result.output or "unknown" in result.output.lower()


# ============================================================================
# simtest run --workflow (flag exists and is accepted)
# ============================================================================

class TestWorkflowRunFlag:

    def test_workflow_flag_accepted(self, runner):
        """Verify --workflow flag is recognized by Click."""
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--workflow" in result.output

    def test_workflow_flag_in_help_text(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert "banking_account_opening" in result.output or "workflow" in result.output.lower()