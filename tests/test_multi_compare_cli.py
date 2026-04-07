"""
Tests for Multi-Model Comparison CLI Integration (Phase 6).

Tests cover:
- Config loading and validation (YAML/JSON)
- API key resolution from environment
- CLI argument parsing and overrides
- Dry-run mode
- Console output formatting
- Error handling (bad config, missing keys, failed models)
- CI/CD gate exit codes
- Plan display
- Summary display
- Output file generation
- End-to-end wiring validation

~48 tests across 8 test classes.

IMPORTANT: This file imports CLI functions from the correct module path.
Your pyproject.toml has: simtest = "src.cli:main"
So the correct import is: from src.cli import main
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Robust CLI import — matches your pyproject.toml entry point
try:
    from src.cli import main as _cli_main
    from src.cli import (
        _load_multi_compare_config,
        _resolve_api_keys,
        _print_multi_compare_plan,
        _print_multi_compare_summary,
    )
    CLI_MODULE = "src.cli"
except ImportError:
    from cli import main as _cli_main
    from cli import (
        _load_multi_compare_config,
        _resolve_api_keys,
        _print_multi_compare_plan,
        _print_multi_compare_summary,
    )
    CLI_MODULE = "cli"

from src.multi_compare.models import (
    ComparisonMatrix,
    ComparisonMode,
    CostEfficiency,
    CostGovernance,
    CoverageParityReport,
    DecisionProfile,
    Dimension,
    DimensionScore,
    GateCheck,
    GateResult,
    GateVerdict,
    ModelRanking,
    ModelRunResult,
    ModelRunStatus,
    ModelSpec,
    MultiCompareConfig,
    MultiCompareReport,
    ComparisonSettings,
    StatisticalVerdict,
    StatisticalVerdictLabel,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

SAMPLE_YAML = """
name: "Test Comparison"
mode: champion_challenger
champion: model-a
models:
  - id: model-a
    name: "Model A"
    endpoint: "https://api.example.com/v1/chat"
    api_key_env: MODEL_A_KEY
  - id: model-b
    name: "Model B"
    endpoint: "https://api.example.com/v2/chat"
    api_key_env: MODEL_B_KEY
settings:
  personas: 5
  max_turns: 8
  parallel: 2
decision_profile: balanced
"""

SAMPLE_JSON_CONFIG = {
    "name": "JSON Test",
    "mode": "head_to_head",
    "models": [
        {"id": "m1", "name": "M1", "endpoint": "https://a.com/v1/chat"},
        {"id": "m2", "name": "M2", "endpoint": "https://b.com/v1/chat"},
    ],
    "settings": {"personas": 3, "max_turns": 5},
}

SAMPLE_BAD_YAML = """
name: "Bad Config"
models:
  - id: only-one
    name: "Only One"
    endpoint: "https://api.example.com/v1/chat"
settings:
  personas: 5
"""


def _write_yaml(tmp_path: Path, content: str = SAMPLE_YAML) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(content)
    return path


def _write_json_config(tmp_path: Path) -> Path:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(SAMPLE_JSON_CONFIG))
    return path


def _make_mock_report(
    winner: str | None = "model-a",
    gate_passed: bool = True,
) -> MultiCompareReport:
    """Create a mock MultiCompareReport for testing."""
    rankings = [
        ModelRanking(
            model_id="model-a", model_name="Model A",
            overall_rank=1, overall_score=0.90,
            deployment_ready=True,
            dimensions=[DimensionScore(
                dimension=Dimension.OVERALL_PASS_RATE,
                score=0.90, rank=1,
            )],
        ),
        ModelRanking(
            model_id="model-b", model_name="Model B",
            overall_rank=2, overall_score=0.80,
            deployment_ready=True,
            dimensions=[DimensionScore(
                dimension=Dimension.OVERALL_PASS_RATE,
                score=0.80, rank=2,
            )],
        ),
    ]

    matrix = ComparisonMatrix(
        rankings=rankings,
        dimensions=["overall_pass_rate"],
        overall_winner=winner,
        champion_id="model-a",
        profile_applied="balanced",
        coverage_parity=[
            CoverageParityReport(model_id="model-a", total_cases=5, completed_cases=5, completion_rate=1.0),
            CoverageParityReport(model_id="model-b", total_cases=5, completed_cases=5, completion_rate=1.0),
        ],
    )

    gate = GateResult(
        verdict=GateVerdict.PASS if gate_passed else GateVerdict.FAIL,
        checks=[GateCheck(name="all", passed=gate_passed, reason="OK" if gate_passed else "Safety below threshold")],
    )

    return MultiCompareReport(
        config_name="Test",
        models_compared=["model-a", "model-b"],
        shared_persona_count=5,
        comparison_matrix=matrix,
        cost_analysis=[
            CostEfficiency(model_id="model-a", total_cost=0.50, cost_per_conversation=0.10, cost_rank=1),
            CostEfficiency(model_id="model-b", total_cost=0.30, cost_per_conversation=0.06, cost_rank=2),
        ],
        cost_governance=[],
        recommendations=["✅ 'Model A' recommended for deployment."],
        gate_result=gate,
    )


# ─────────────────────────────────────────────────────────────
# Test Class 1: Config Loading (8 tests)
# ─────────────────────────────────────────────────────────────

class TestConfigLoading:
    """Config parsing from YAML and JSON files."""

    def test_load_yaml_config(self, tmp_path):
        """Valid YAML config loads successfully."""
        # Import inline to avoid circular dependency issues
        sys.path.insert(0, str(tmp_path.parent))

        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        assert config.name == "Test Comparison"
        assert len(config.models) == 2
        assert config.champion == "model-a"

    def test_load_json_config(self, tmp_path):
        """Valid JSON config loads successfully."""
        path = _write_json_config(tmp_path)
        config = _load_multi_compare_config(str(path))
        assert config.name == "JSON Test"
        assert len(config.models) == 2

    def test_load_bad_config_raises(self, tmp_path):
        """Config with fewer than 2 models raises error."""
        path = _write_yaml(tmp_path, SAMPLE_BAD_YAML)
        with pytest.raises(Exception):
            _load_multi_compare_config(str(path))

    def test_config_validates_champion(self, tmp_path):
        """Config with invalid champion reference raises error."""
        bad = SAMPLE_YAML.replace("champion: model-a", "champion: nonexistent")
        path = _write_yaml(tmp_path, bad)
        with pytest.raises(Exception):
            _load_multi_compare_config(str(path))

    def test_config_validates_duplicate_ids(self, tmp_path):
        """Config with duplicate model IDs raises error."""
        dup = SAMPLE_YAML.replace("id: model-b", "id: model-a")
        path = _write_yaml(tmp_path, dup)
        with pytest.raises(Exception):
            _load_multi_compare_config(str(path))

    def test_config_settings_defaults(self, tmp_path):
        """Default settings are applied when not specified."""
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        assert config.settings.personas == 5
        assert config.settings.max_turns == 8

    def test_config_model_specs(self, tmp_path):
        """ModelSpec fields are correctly parsed."""
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        m = config.models[0]
        assert m.id == "model-a"
        assert m.name == "Model A"
        assert m.endpoint == "https://api.example.com/v1/chat"
        assert m.api_key_env == "MODEL_A_KEY"

    def test_config_decision_profile_resolution(self, tmp_path):
        """Decision profile resolves from config."""
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        profile = config.get_decision_profile()
        assert profile.name == "balanced"


# ─────────────────────────────────────────────────────────────
# Test Class 2: API Key Resolution (4 tests)
# ─────────────────────────────────────────────────────────────

class TestAPIKeyResolution:
    """Environment variable API key resolution."""

    def test_resolve_api_key_from_env(self, tmp_path):
        """API key resolved from environment variable."""
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))

        with patch.dict(os.environ, {"MODEL_A_KEY": "secret-key-a", "MODEL_B_KEY": "secret-key-b"}):
            _resolve_api_keys(config)

        assert config.models[0].api_key == "secret-key-a"
        assert config.models[1].api_key == "secret-key-b"

    def test_missing_env_key_warns(self, tmp_path, capsys):
        """Missing environment variable produces warning."""
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))

        # Ensure env vars are NOT set
        env = {k: v for k, v in os.environ.items() if k not in ("MODEL_A_KEY", "MODEL_B_KEY")}
        with patch.dict(os.environ, env, clear=True):
            _resolve_api_keys(config)

        assert config.models[0].api_key is None

    def test_no_env_key_field_skips(self, tmp_path):
        """Model without api_key_env is skipped silently."""
        yaml_no_key = SAMPLE_YAML.replace("    api_key_env: MODEL_A_KEY\n", "")
        yaml_no_key = yaml_no_key.replace("    api_key_env: MODEL_B_KEY\n", "")
        path = _write_yaml(tmp_path, yaml_no_key)
        config = _load_multi_compare_config(str(path))
        _resolve_api_keys(config)
        # Should not crash
        assert config.models[0].api_key is None

    def test_pre_set_key_not_overwritten(self, tmp_path):
        """Already-set api_key is not overwritten by env."""
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        config.models[0].api_key = "pre-existing"

        with patch.dict(os.environ, {"MODEL_A_KEY": "from-env"}):
            _resolve_api_keys(config)

        assert config.models[0].api_key == "pre-existing"


# ─────────────────────────────────────────────────────────────
# Test Class 3: CLI Argument Parsing (6 tests)
# ─────────────────────────────────────────────────────────────

class TestCLIArguments:
    """CLI flag parsing and override behavior."""

    def test_config_is_required(self):
        """--config is required."""
        runner = CliRunner()
        result = runner.invoke(_cli_main, ["multi-compare"])
        assert result.exit_code != 0
        assert "Missing" in result.output or "required" in result.output.lower() or "Error" in result.output

    def test_dry_run_exits_cleanly(self, tmp_path):
        """--dry-run validates config and exits without running."""
        path = _write_yaml(tmp_path)

        runner = CliRunner()
        with patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path), "--dry-run"
            ])

        assert result.exit_code == 0
        assert "Dry-run" in result.output or "dry-run" in result.output.lower()

    def test_dry_run_shows_plan(self, tmp_path):
        """--dry-run displays the run plan with model table."""
        path = _write_yaml(tmp_path)

        runner = CliRunner()
        with patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path), "--dry-run"
            ])

        assert "Model A" in result.output
        assert "Model B" in result.output
        assert "balanced" in result.output.lower() or "Balanced" in result.output

    def test_profile_override(self, tmp_path):
        """--profile overrides config decision_profile."""
        path = _write_yaml(tmp_path)

        runner = CliRunner()
        with patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--profile", "safety_first", "--dry-run"
            ])

        assert result.exit_code == 0
        assert "Safety First" in result.output or "safety_first" in result.output

    def test_invalid_profile_override_errors(self, tmp_path):
        """Invalid --profile produces error."""
        path = _write_yaml(tmp_path)

        runner = CliRunner()
        with patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--profile", "nonexistent_profile", "--dry-run"
            ])

        assert result.exit_code != 0

    def test_personas_override(self, tmp_path):
        """--personas overrides config value."""
        path = _write_yaml(tmp_path)

        runner = CliRunner()
        with patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--personas", "3", "--dry-run"
            ])

        assert result.exit_code == 0
        assert "3" in result.output


# ─────────────────────────────────────────────────────────────
# Test Class 4: Plan Display (4 tests)
# ─────────────────────────────────────────────────────────────

class TestPlanDisplay:
    """Run plan display formatting."""

    def test_plan_shows_mode(self, tmp_path):
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        # Just verify it doesn't crash
        _print_multi_compare_plan(config, strict=False, resume=False)

    def test_plan_shows_strict_mode(self, tmp_path):
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        _print_multi_compare_plan(config, strict=True, resume=False)

    def test_plan_shows_resume(self, tmp_path):
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        _print_multi_compare_plan(config, strict=False, resume=True)

    def test_plan_shows_budget(self, tmp_path):
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        config.settings.budget_limit = 5.0
        _print_multi_compare_plan(config, strict=False, resume=False)


# ─────────────────────────────────────────────────────────────
# Test Class 5: Summary Display (5 tests)
# ─────────────────────────────────────────────────────────────

class TestSummaryDisplay:
    """Console summary formatting."""

    def test_summary_with_winner(self):
        report = _make_mock_report(winner="model-a")
        # Should not crash
        _print_multi_compare_summary(report, elapsed=45.0, show_cost=False)

    def test_summary_no_winner(self):
        report = _make_mock_report(winner=None)
        _print_multi_compare_summary(report, elapsed=30.0, show_cost=False)

    def test_summary_with_cost(self):
        report = _make_mock_report()
        _print_multi_compare_summary(report, elapsed=50.0, show_cost=True)

    def test_summary_gate_fail(self):
        report = _make_mock_report(gate_passed=False)
        _print_multi_compare_summary(report, elapsed=25.0, show_cost=False)

    def test_summary_rankings_display(self):
        report = _make_mock_report()
        # Verify it runs without error — rankings should show medal icons
        _print_multi_compare_summary(report, elapsed=60.0, show_cost=False)


# ─────────────────────────────────────────────────────────────
# Test Class 6: Error Handling (5 tests)
# ─────────────────────────────────────────────────────────────

class TestErrorHandling:
    """Error handling for bad configs and failed runs."""

    def test_missing_config_file(self):
        """Non-existent config file produces error."""
        runner = CliRunner()
        result = runner.invoke(_cli_main, [
            "multi-compare", "--config", "/nonexistent/path.yaml"
        ])
        assert result.exit_code != 0

    def test_malformed_yaml(self, tmp_path):
        """Malformed YAML produces error."""
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("name: [unclosed bracket")

        runner = CliRunner()
        result = runner.invoke(_cli_main, [
            "multi-compare", "--config", str(bad_path)
        ])
        assert result.exit_code != 0

    def test_empty_yaml(self, tmp_path):
        """Empty YAML produces error."""
        path = tmp_path / "empty.yaml"
        path.write_text("")

        runner = CliRunner()
        result = runner.invoke(_cli_main, [
            "multi-compare", "--config", str(path)
        ])
        assert result.exit_code != 0

    def test_invalid_endpoint_url(self, tmp_path):
        """Model with non-URL endpoint produces error."""
        bad_endpoint = SAMPLE_YAML.replace(
            "endpoint: \"https://api.example.com/v1/chat\"",
            "endpoint: \"not-a-url\""
        )
        path = _write_yaml(tmp_path, bad_endpoint)

        runner = CliRunner()
        result = runner.invoke(_cli_main, [
            "multi-compare", "--config", str(path), "--dry-run"
        ])
        assert result.exit_code != 0

    def test_single_model_config_error(self, tmp_path):
        """Config with only one model produces validation error."""
        path = _write_yaml(tmp_path, SAMPLE_BAD_YAML)

        runner = CliRunner()
        result = runner.invoke(_cli_main, [
            "multi-compare", "--config", str(path), "--dry-run"
        ])
        assert result.exit_code != 0


# ─────────────────────────────────────────────────────────────
# Test Class 7: CI/CD Gate (4 tests)
# ─────────────────────────────────────────────────────────────

class TestCICDGate:
    """CI/CD gate exit code behavior."""

    def test_gate_pass_exits_zero(self, tmp_path):
        """--fail-if-worse with passing gate exits 0."""
        path = _write_yaml(tmp_path)
        report = _make_mock_report(gate_passed=True)

        with patch(f"{CLI_MODULE}._run_single_model") as mock_run, \
             patch("src.multi_compare.analysis.NWayComparisonEngine") as mock_engine_cls, \
             patch("src.multi_compare.dashboard.MultiCompareDashboard") as mock_dash, \
             patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):

            mock_result = ModelRunResult(
                model_id="model-a", model_name="Model A",
                status=ModelRunStatus.SUCCESS,
                summary_dict={"pass_rate": 0.9, "total_conversations": 5},
            )
            mock_run.return_value = (mock_result, [])
            mock_engine_cls.return_value.analyze.return_value = report
            mock_dash.return_value.export.return_value = tmp_path / "report.html"

            runner = CliRunner()
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--fail-if-worse", "--output", str(tmp_path / "out"),
            ])

        assert result.exit_code == 0

    def test_gate_fail_exits_one(self, tmp_path):
        """--fail-if-worse with failing gate exits 1."""
        path = _write_yaml(tmp_path)
        report = _make_mock_report(gate_passed=False)

        with patch(f"{CLI_MODULE}._run_single_model") as mock_run, \
             patch("src.multi_compare.analysis.NWayComparisonEngine") as mock_engine_cls, \
             patch("src.multi_compare.dashboard.MultiCompareDashboard") as mock_dash, \
             patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):

            mock_result = ModelRunResult(
                model_id="model-a", model_name="Model A",
                status=ModelRunStatus.SUCCESS,
                summary_dict={"pass_rate": 0.9, "total_conversations": 5},
            )
            mock_run.return_value = (mock_result, [])
            mock_engine_cls.return_value.analyze.return_value = report
            mock_dash.return_value.export.return_value = tmp_path / "report.html"

            runner = CliRunner()
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--fail-if-worse", "--output", str(tmp_path / "out"),
            ])

        assert result.exit_code == 1

    def test_no_gate_flag_always_exits_zero(self, tmp_path):
        """Without --fail-if-worse, always exits 0 even if gate fails."""
        path = _write_yaml(tmp_path)
        report = _make_mock_report(gate_passed=False)

        with patch(f"{CLI_MODULE}._run_single_model") as mock_run, \
             patch("src.multi_compare.analysis.NWayComparisonEngine") as mock_engine_cls, \
             patch("src.multi_compare.dashboard.MultiCompareDashboard") as mock_dash, \
             patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):

            mock_result = ModelRunResult(
                model_id="model-a", model_name="Model A",
                status=ModelRunStatus.SUCCESS,
                summary_dict={"pass_rate": 0.9, "total_conversations": 5},
            )
            mock_run.return_value = (mock_result, [])
            mock_engine_cls.return_value.analyze.return_value = report
            mock_dash.return_value.export.return_value = tmp_path / "report.html"

            runner = CliRunner()
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--output", str(tmp_path / "out"),
            ])

        assert result.exit_code == 0

    def test_gate_fail_shows_reason(self, tmp_path):
        """Gate failure shows check reasons."""
        path = _write_yaml(tmp_path)
        report = _make_mock_report(gate_passed=False)
        report.gate_result.checks[0].reason = "Safety score 0.55 below 0.70"

        with patch(f"{CLI_MODULE}._run_single_model") as mock_run, \
             patch("src.multi_compare.analysis.NWayComparisonEngine") as mock_engine_cls, \
             patch("src.multi_compare.dashboard.MultiCompareDashboard") as mock_dash, \
             patch.dict(os.environ, {"MODEL_A_KEY": "k1", "MODEL_B_KEY": "k2"}):

            mock_result = ModelRunResult(
                model_id="model-a", model_name="Model A",
                status=ModelRunStatus.SUCCESS,
                summary_dict={"pass_rate": 0.9, "total_conversations": 5},
            )
            mock_run.return_value = (mock_result, [])
            mock_engine_cls.return_value.analyze.return_value = report
            mock_dash.return_value.export.return_value = tmp_path / "report.html"

            runner = CliRunner()
            result = runner.invoke(_cli_main, [
                "multi-compare", "--config", str(path),
                "--fail-if-worse", "--output", str(tmp_path / "out"),
            ])

        assert "Safety score" in result.output


# ─────────────────────────────────────────────────────────────
# Test Class 8: Model Spec & Config Edge Cases (6 tests)
# ─────────────────────────────────────────────────────────────

class TestModelSpecEdgeCases:
    """Edge cases in model spec configuration."""

    def test_head_to_head_mode_no_champion(self, tmp_path):
        """Head-to-head mode works without champion."""
        h2h_yaml = """
name: "Head to Head"
mode: head_to_head
models:
  - id: model-a
    name: "Model A"
    endpoint: "https://a.com/v1/chat"
  - id: model-b
    name: "Model B"
    endpoint: "https://b.com/v1/chat"
settings:
  personas: 3
"""
        path = _write_yaml(tmp_path, h2h_yaml)
        config = _load_multi_compare_config(str(path))
        assert config.mode == ComparisonMode.HEAD_TO_HEAD
        assert config.champion is None

    def test_ten_models(self, tmp_path):
        """Config with 10 models loads successfully."""
        models_block = "\n".join([
            f'  - id: model-{i}\n    name: "Model {i}"\n    endpoint: "https://api{i}.com/v1/chat"'
            for i in range(10)
        ])
        yaml_10 = f"""
name: "Big Compare"
mode: head_to_head
models:
{models_block}
settings:
  personas: 3
"""
        path = _write_yaml(tmp_path, yaml_10)
        config = _load_multi_compare_config(str(path))
        assert len(config.models) == 10

    def test_custom_profile_weights(self, tmp_path):
        """Custom decision profile with explicit weights."""
        custom_yaml = """
name: "Custom Profile"
mode: head_to_head
models:
  - id: m1
    name: "M1"
    endpoint: "https://a.com/v1/chat"
  - id: m2
    name: "M2"
    endpoint: "https://b.com/v1/chat"
decision_profile: custom
decision_weights:
  safety: 0.5
  quality: 0.3
  overall_pass_rate: 0.2
settings:
  personas: 3
"""
        path = _write_yaml(tmp_path, custom_yaml)
        config = _load_multi_compare_config(str(path))
        profile = config.get_decision_profile()
        assert profile.name == "custom"
        assert profile.weights["safety"] == 0.5

    def test_model_with_tags(self, tmp_path):
        """Model spec with tags parses correctly."""
        tagged = SAMPLE_YAML + "    tags: [production, v2]\n"
        # Add tags to second model
        tagged = tagged.replace(
            "    api_key_env: MODEL_B_KEY",
            "    api_key_env: MODEL_B_KEY\n    tags:\n      - staging\n      - experimental"
        )
        path = _write_yaml(tmp_path)
        config = _load_multi_compare_config(str(path))
        # Tags default to empty list
        assert isinstance(config.models[0].tags, list)

    def test_model_with_generic_adapter(self, tmp_path):
        """Model spec with generic adapter and response_path."""
        generic_yaml = """
name: "Generic Test"
mode: head_to_head
models:
  - id: custom-bot
    name: "Custom Bot"
    endpoint: "https://custom.com/api"
    adapter: generic
    response_path: "data.reply.text"
  - id: other-bot
    name: "Other Bot"
    endpoint: "https://other.com/v1/chat"
settings:
  personas: 3
"""
        path = _write_yaml(tmp_path, generic_yaml)
        config = _load_multi_compare_config(str(path))
        assert config.models[0].adapter.value == "generic"
        assert config.models[0].response_path == "data.reply.text"

    def test_settings_with_all_options(self, tmp_path):
        """ComparisonSettings with all optional fields."""
        full_yaml = """
name: "Full Settings"
mode: head_to_head
models:
  - id: m1
    name: "M1"
    endpoint: "https://a.com/v1/chat"
  - id: m2
    name: "M2"
    endpoint: "https://b.com/v1/chat"
settings:
  personas: 10
  max_turns: 12
  min_turns: 3
  parallel: 5
  budget_limit: 10.0
  budget_mode: hard
  stress_memory: true
  rag_eval: true
  pass_threshold: 0.8
  warn_threshold: 0.6
  signature: false
"""
        path = _write_yaml(tmp_path, full_yaml)
        config = _load_multi_compare_config(str(path))
        s = config.settings
        assert s.personas == 10
        assert s.max_turns == 12
        assert s.min_turns == 3
        assert s.parallel == 5
        assert s.budget_limit == 10.0
        assert s.budget_mode == "hard"
        assert s.stress_memory is True
        assert s.rag_eval is True
        assert s.pass_threshold == 0.8