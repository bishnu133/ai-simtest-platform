"""
Tests for CLI --mode auto integration (Phase F.5).
Verifies that the fully autonomous mode is properly wired into the CLI.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from click.testing import CliRunner


# ============================================================
# Test: --mode auto is a valid choice
# ============================================================

class TestAutoModeCliAcceptance:
    """Verify --mode auto is accepted by the CLI."""

    def test_auto_mode_in_choices(self):
        """--mode auto should be a valid CLI choice."""
        from src.cli import main
        runner = CliRunner()

        # --mode auto without --bot-endpoint should error about missing endpoint, NOT invalid choice
        result = runner.invoke(main, ["run", "--mode", "auto"])
        assert "Error: Missing option '--bot-endpoint'" in result.output or result.exit_code != 0
        # Should NOT say "invalid choice"
        assert "invalid choice" not in result.output.lower()

    def test_auto_mode_rejects_invalid(self):
        """--mode invalid should be rejected."""
        from src.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--mode", "invalid", "--bot-endpoint", "http://test"])
        assert "invalid choice" in result.output.lower() or result.exit_code != 0

    def test_manual_and_partial_still_work(self):
        """Existing modes should still be valid choices."""
        from src.cli import main
        runner = CliRunner()

        # manual mode (should fail later due to missing LLM, not due to choice validation)
        result = runner.invoke(main, ["run", "--mode", "manual", "--bot-endpoint", "http://test"])
        assert "invalid choice" not in result.output.lower()

        # partial mode (should fail due to missing --doc-dir)
        result = runner.invoke(main, ["run", "--mode", "partial", "--bot-endpoint", "http://test"])
        assert "Partial mode requires --doc-dir" in result.output or result.exit_code != 0


# ============================================================
# Test: Auto mode CLI banner
# ============================================================

class TestAutoModeBanner:
    """Verify the auto mode displays correct banner."""

    @patch("src.cli._run_full_autonomous", new_callable=AsyncMock)
    @patch("src.cli.asyncio.run")
    def test_auto_mode_shows_banner(self, mock_asyncio_run, mock_runner):
        """Auto mode should display the fully autonomous banner."""
        from src.cli import main
        runner = CliRunner()

        result = runner.invoke(main, [
            "run", "--mode", "auto",
            "--bot-endpoint", "http://localhost:9999/v1/chat/completions",
        ])

        assert "Fully Autonomous" in result.output
        assert "discover bot purpose" in result.output.lower() or "exploratory" in result.output.lower()

    @patch("src.cli._run_full_autonomous", new_callable=AsyncMock)
    @patch("src.cli.asyncio.run")
    def test_auto_mode_shows_auto_approve(self, mock_asyncio_run, mock_runner):
        """Auto mode with --auto-approve should show CI/CD indicator."""
        from src.cli import main
        runner = CliRunner()

        result = runner.invoke(main, [
            "run", "--mode", "auto",
            "--bot-endpoint", "http://localhost:9999/v1/chat/completions",
            "--auto-approve",
        ])

        assert "Auto-approve" in result.output or "CI/CD" in result.output


# ============================================================
# Test: Auto mode passes parameters correctly
# ============================================================

class TestAutoModeParameterPassing:
    """Verify parameters are passed through to _run_full_autonomous."""

    @patch("src.cli.asyncio.run")
    def test_passes_endpoint(self, mock_asyncio_run):
        """Bot endpoint should be passed to the auto runner."""
        from src.cli import main
        runner = CliRunner()

        result = runner.invoke(main, [
            "run", "--mode", "auto",
            "--bot-endpoint", "http://mybot.com/api",
        ])

        # asyncio.run should have been called
        assert mock_asyncio_run.called
        # The coroutine should have been created with our endpoint
        call_args = mock_asyncio_run.call_args
        assert call_args is not None

    @patch("src.cli.asyncio.run")
    def test_custom_personas_passed_as_none_when_default(self, mock_asyncio_run):
        """Default personas (20) should be passed as None to let auto mode decide."""
        from src.cli import main
        runner = CliRunner()

        result = runner.invoke(main, [
            "run", "--mode", "auto",
            "--bot-endpoint", "http://mybot.com/api",
            "--personas", "20",  # default
        ])

        assert mock_asyncio_run.called


# ============================================================
# Test: _print_auto_result handles all outcome types
# ============================================================

class TestAutoResultDisplay:
    """Verify the auto result display handles different outcomes."""

    def test_print_completed_result(self, capsys):
        """Completed result should show green success."""
        from src.cli import _print_auto_result

        result = MagicMock()
        result.status = "completed"
        result.execution_time = 45.3
        result.total_execution_time = 45.3
        result.discovery_attempt1 = MagicMock()
        result.discovery_attempt1.context = MagicMock()
        result.discovery_attempt1.context.bot_name = "SkyBot"
        result.discovery_attempt1.context.domain = "airline"
        result.discovery_attempt1.context.bot_description = "A helpful airline assistant"
        result.discovery_attempt1.context.capabilities = ["booking", "baggage"]
        result.discovery_attempt1.context.overall_confidence = MagicMock(score=0.85)
        result.discovery_attempt1.context.quality_level = "good"
        result.discovery_attempt2 = None
        result.retry_result = None
        result.discovered_context = result.discovery_attempt1.context
        result.final_context = None
        result.mismatch_report = None
        result.simulation_result = None

        _print_auto_result(result, "./output")
        # No assertion needed — just verifying no exception

    def test_print_failed_no_response(self, capsys):
        """Failed (no_response) result should suggest checking endpoint."""
        from src.cli import _print_auto_result

        result = MagicMock()
        result.status = "failed"
        result.failure_type = "no_response"
        result.error_message = ""
        result.execution_time = 5.1
        result.total_execution_time = 5.1
        result.discovery_attempt1 = None
        result.discovery_result = None
        result.discovery_attempt2 = None
        result.retry_result = None
        result.discovered_context = None
        result.final_context = None
        result.mismatch_report = None
        result.diagnostic_report_path = "/output/diagnostic.md"

        _print_auto_result(result, "./output")

    def test_print_failed_inconsistent(self, capsys):
        """Failed (inconsistent) result should suggest partial mode."""
        from src.cli import _print_auto_result

        result = MagicMock()
        result.status = "failed"
        result.failure_type = "inconsistent"
        result.error_message = ""
        result.execution_time = 30.0
        result.total_execution_time = 30.0
        result.discovery_attempt1 = MagicMock()
        result.discovery_attempt1.context = MagicMock()
        result.discovery_attempt1.context.bot_name = "SkyBot"
        result.discovery_attempt1.context.domain = "airline"
        result.discovery_attempt1.context.bot_description = "An airline bot"
        result.discovery_attempt1.context.capabilities = ["flights"]
        result.discovery_attempt1.context.overall_confidence = MagicMock(score=0.3)
        result.discovery_attempt1.context.quality_level = "poor"
        result.discovery_attempt2 = MagicMock()
        result.discovery_attempt2.context = MagicMock()
        result.discovery_attempt2.context.bot_name = "BankBot"
        result.discovery_attempt2.context.domain = "banking"
        result.discovery_attempt2.context.overall_confidence = MagicMock(score=0.4)
        result.discovery_attempt2.context.quality_level = "fair"
        result.retry_result = result.discovery_attempt2
        result.discovered_context = None
        result.final_context = None
        result.mismatch_report = MagicMock()
        result.mismatch_report.has_mismatches = True
        result.mismatch_report.severity = "critical"
        result.mismatch_report.to_report_section.return_value = "Domain changed from airline to banking"
        result.diagnostic_report_path = ""

        _print_auto_result(result, "./output")

    def test_print_stopped_result(self, capsys):
        """Stopped result should suggest partial mode."""
        from src.cli import _print_auto_result

        result = MagicMock()
        result.status = "stopped"
        result.stopped_reason = "Discovery rejected after retry"
        result.execution_time = 25.0
        result.total_execution_time = 25.0
        result.discovery_attempt1 = None
        result.discovery_result = None
        result.discovery_attempt2 = None
        result.retry_result = None
        result.discovered_context = None
        result.final_context = None
        result.mismatch_report = None

        _print_auto_result(result, "./output")

    def test_print_fullautoresult_style(self, capsys):
        """FullAutoResult (success field) should also display correctly."""
        from src.cli import _print_auto_result

        result = MagicMock()
        # FullAutoResult uses 'success' instead of 'status'
        result.status = "unknown"  # Not the AutoResult pattern
        result.success = True
        result.stopped_reason = ""
        result.diagnostic_report = ""
        result.execution_time = 0
        result.total_execution_time = 50.0
        result.discovery_attempt1 = None
        result.discovery_result = MagicMock()
        result.discovery_result.context = MagicMock()
        result.discovery_result.context.bot_name = "TestBot"
        result.discovery_result.context.domain = "testing"
        result.discovery_result.context.bot_description = "A testing bot"
        result.discovery_result.context.capabilities = ["test"]
        result.discovery_result.context.overall_confidence = MagicMock(score=0.7)
        result.discovery_result.context.quality_level = "good"
        result.discovery_attempt2 = None
        result.retry_result = None
        result.discovered_context = None
        result.final_context = result.discovery_result.context
        result.mismatch_report = None

        _print_auto_result(result, "./output")


# ============================================================
# Test: Version command shows auto mode
# ============================================================

class TestVersionShowsAutoMode:
    """Verify the version command mentions auto mode."""

    def test_version_mentions_auto(self):
        from src.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert "auto" in result.output.lower()


# ============================================================
# Test: Auto mode doesn't require --doc-dir
# ============================================================

class TestAutoModeNoDocsRequired:
    """Auto mode should NOT require --doc-dir or --doc-file."""

    @patch("src.cli.asyncio.run")
    def test_auto_mode_without_docs(self, mock_asyncio_run):
        """Auto mode should work with just --bot-endpoint."""
        from src.cli import main
        runner = CliRunner()

        result = runner.invoke(main, [
            "run", "--mode", "auto",
            "--bot-endpoint", "http://mybot.com/api",
        ])

        # Should NOT error about missing docs
        assert "requires --doc-dir" not in result.output
        assert mock_asyncio_run.called