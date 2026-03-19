"""
Phase 7 Tests - REST API & CLI verification.

Tests:
  - FastAPI health endpoint
  - Create simulation endpoint
  - Status/report/personas/list endpoints
  - Export endpoint
  - CLI entry-point resolution
  - CLI run command (mocked orchestrator)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.models import (
    BotConfig,
    Conversation,
    FailurePattern,
    JudgedConversation,
    JudgedTurn,
    JudgmentLabel,
    JudgmentResult,
    Persona,
    PersonaType,
    ReportSummary,
    Severity,
    SimulationConfig,
    SimulationReport,
    SimulationRun,
    SimulationStatus,
    Turn,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture()
def api_client():
    """Create a test client that doesn't actually run background tasks."""
    return TestClient(app)


@pytest.fixture()
def _clear_simulations():
    """Clear the in-memory simulation store before each test."""
    from src.api import app as app_module
    app_module._simulations.clear()
    yield
    app_module._simulations.clear()


def _make_completed_orchestrator() -> MagicMock:
    """Create a mock orchestrator that looks like a completed simulation."""
    persona = Persona(
        name="Test User",
        role="customer",
        goals=["get help"],
        persona_type=PersonaType.STANDARD,
    )

    turn_user = Turn(speaker="user", message="Hello")
    turn_bot = Turn(speaker="bot", message="Hi! How can I help?")
    conv = Conversation(persona_id=persona.id, turns=[turn_user, turn_bot])

    judgment = JudgmentResult(
        judge_name="safety", passed=True, score=0.9, severity=Severity.INFO, message="Clean"
    )
    jt = JudgedTurn(
        turn=turn_bot,
        judgments=[judgment],
        overall_score=0.9,
        overall_label=JudgmentLabel.PASS,
    )
    jc = JudgedConversation(
        conversation=conv,
        persona=persona,
        judged_turns=[jt],
        overall_score=0.9,
    )

    summary = ReportSummary(
        simulation_id="sim_test1234",
        simulation_name="Test Sim",
        total_personas=1,
        total_conversations=1,
        total_turns=2,
        pass_rate=1.0,
        average_score=0.9,
        critical_failures=0,
        warnings=0,
        execution_time_seconds=1.5,
    )

    report = SimulationReport(
        summary=summary,
        judged_conversations=[jc],
        score_by_judge={"safety": 0.9},
        score_by_persona_type={"standard": 0.9},
        recommendations=["All good!"],
        failure_patterns=[],
    )

    config = SimulationConfig(
        id="sim_test1234",
        name="Test Sim",
        bot=BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions"),
    )

    run = SimulationRun(id="sim_test1234", config=config)
    run.status = SimulationStatus.COMPLETED
    run.personas = [persona]
    run.conversations = [conv]
    run.report = report

    orch = MagicMock()
    orch.run = run
    orch.config = config
    return orch


# ============================================================
# API Endpoint Tests
# ============================================================

class TestHealthEndpoint:
    def test_health_returns_ok(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"


@pytest.mark.usefixtures("_clear_simulations")
class TestCreateSimulation:
    def test_create_simulation_returns_id_and_pending(self, api_client):
        """POST /simulations should return a simulation ID with pending status."""
        # Patch background task so it doesn't actually run
        with patch("src.api.app.asyncio.create_task"):
            resp = api_client.post("/simulations", json={
                "bot_endpoint": "http://localhost:9999/v1/chat/completions",
                "num_personas": 3,
                "max_turns": 5,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "simulation_id" in data
        assert data["status"] == "pending"
        assert data["simulation_id"].startswith("sim_")

    def test_create_simulation_with_full_config(self, api_client):
        """POST /simulations with all optional fields."""
        with patch("src.api.app.asyncio.create_task"):
            resp = api_client.post("/simulations", json={
                "name": "Full Config Test",
                "bot_endpoint": "http://localhost:9999/v1/chat/completions",
                "bot_api_key": "test-key-123",
                "bot_request_format": "openai",
                "documentation": "This bot handles customer support.",
                "success_criteria": ["Be helpful", "Stay on topic"],
                "num_personas": 10,
                "max_turns": 8,
                "max_parallel": 5,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"

    def test_create_simulation_missing_endpoint_fails(self, api_client):
        """POST /simulations without required bot_endpoint should fail."""
        resp = api_client.post("/simulations", json={"num_personas": 3})
        assert resp.status_code == 422  # Validation error


@pytest.mark.usefixtures("_clear_simulations")
class TestSimulationStatus:
    def test_get_status_not_found(self, api_client):
        resp = api_client.get("/simulations/nonexistent/status")
        assert resp.status_code == 404

    def test_get_status_completed(self, api_client):
        """Inject a completed orchestrator and check status."""
        from src.api import app as app_module
        orch = _make_completed_orchestrator()
        app_module._simulations["sim_test1234"] = orch

        resp = api_client.get("/simulations/sim_test1234/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["personas_count"] == 1
        assert data["conversations_count"] == 1


@pytest.mark.usefixtures("_clear_simulations")
class TestSimulationReport:
    def test_get_report_not_found(self, api_client):
        resp = api_client.get("/simulations/nonexistent/report")
        assert resp.status_code == 404

    def test_get_report_completed(self, api_client):
        """Completed simulation should return full report."""
        from src.api import app as app_module
        orch = _make_completed_orchestrator()
        app_module._simulations["sim_test1234"] = orch

        resp = api_client.get("/simulations/sim_test1234/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["summary"] is not None
        assert data["summary"]["pass_rate"] == 1.0
        assert data["summary"]["total_conversations"] == 1
        assert data["score_by_judge"]["safety"] == 0.9
        assert len(data["recommendations"]) > 0

    def test_get_report_not_yet_completed(self, api_client):
        """Running simulation should return status without report."""
        from src.api import app as app_module
        config = SimulationConfig(
            id="sim_running",
            bot=BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions"),
        )
        orch = MagicMock()
        orch.run = SimulationRun(id="sim_running", config=config)
        orch.run.status = SimulationStatus.RUNNING
        app_module._simulations["sim_running"] = orch

        resp = api_client.get("/simulations/sim_running/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["summary"] is None


@pytest.mark.usefixtures("_clear_simulations")
class TestSimulationPersonas:
    def test_get_personas_not_found(self, api_client):
        resp = api_client.get("/simulations/nonexistent/personas")
        assert resp.status_code == 404

    def test_get_personas_completed(self, api_client):
        from src.api import app as app_module
        orch = _make_completed_orchestrator()
        app_module._simulations["sim_test1234"] = orch

        resp = api_client.get("/simulations/sim_test1234/personas")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["personas"][0]["name"] == "Test User"


@pytest.mark.usefixtures("_clear_simulations")
class TestListSimulations:
    def test_list_empty(self, api_client):
        resp = api_client.get("/simulations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["simulations"] == []

    def test_list_with_simulations(self, api_client):
        from src.api import app as app_module
        orch = _make_completed_orchestrator()
        app_module._simulations["sim_test1234"] = orch

        resp = api_client.get("/simulations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["simulations"]) == 1
        assert data["simulations"][0]["id"] == "sim_test1234"
        assert data["simulations"][0]["status"] == "completed"


@pytest.mark.usefixtures("_clear_simulations")
class TestExportEndpoint:
    def test_export_not_found(self, api_client):
        resp = api_client.post("/simulations/nonexistent/export")
        assert resp.status_code == 404

    def test_export_not_completed(self, api_client):
        from src.api import app as app_module
        config = SimulationConfig(
            id="sim_running",
            bot=BotConfig(api_endpoint="http://localhost:9999/v1/chat/completions"),
        )
        orch = MagicMock()
        orch.run = SimulationRun(id="sim_running", config=config)
        orch.run.status = SimulationStatus.RUNNING
        app_module._simulations["sim_running"] = orch

        resp = api_client.post("/simulations/sim_running/export")
        assert resp.status_code == 400

    def test_export_completed(self, api_client, tmp_path):
        from src.api import app as app_module
        orch = _make_completed_orchestrator()
        orch.export_results = MagicMock(return_value={
            "jsonl": str(tmp_path / "conversations.jsonl"),
            "csv": str(tmp_path / "results.csv"),
            "summary": str(tmp_path / "summary.json"),
        })
        app_module._simulations["sim_test1234"] = orch

        resp = api_client.post("/simulations/sim_test1234/export", json={"formats": ["jsonl", "csv", "summary"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "exported_files" in data


# ============================================================
# CLI Entry Point Tests
# ============================================================

class TestCLIEntryPoint:
    def test_cli_main_group_exists(self):
        """Verify the CLI main group is importable."""
        from src.cli import main
        assert main is not None
        assert hasattr(main, "commands")

    def test_cli_has_run_command(self):
        from src.cli import main
        assert "run" in main.commands

    def test_cli_has_serve_command(self):
        from src.cli import main
        assert "serve" in main.commands

    def test_cli_has_version_command(self):
        from src.cli import main
        assert "version" in main.commands

    def test_cli_version_output(self):
        """Invoke the version command and check output."""
        from click.testing import CliRunner
        from src.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "0.2.0" in result.output

    def test_cli_run_requires_bot_endpoint(self):
        """Run without --bot-endpoint should fail."""
        from click.testing import CliRunner
        from src.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["run"])
        assert result.exit_code != 0
        assert "bot-endpoint" in result.output.lower() or "missing" in result.output.lower() or result.exit_code == 2

    def test_cli_run_invokes_simulation(self):
        """Run with mock orchestrator — verify pipeline is called."""
        from click.testing import CliRunner
        from src.cli import main

        mock_report = MagicMock()
        mock_report.summary = MagicMock(
            total_personas=2,
            total_conversations=2,
            total_turns=8,
            pass_rate=0.85,
            average_score=0.78,
            critical_failures=0,
            warnings=1,
            execution_time_seconds=3.2,
        )
        mock_report.score_by_judge = {"safety": 0.9, "relevance": 0.7}
        mock_report.recommendations = ["Improve grounding"]

        with patch("src.core.orchestrator.SimulationOrchestrator") as MockOrch:
            mock_instance = AsyncMock()
            mock_instance.run_simulation = AsyncMock(return_value=mock_report)
            mock_instance.export_results = MagicMock(return_value={
                "jsonl": "/tmp/test.jsonl",
            })
            MockOrch.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(main, [
                "run",
                "--bot-endpoint", "http://localhost:9999/v1/chat/completions",
                "--personas", "2",
                "--max-turns", "3",
                "--output", "/tmp/test_reports",
            ])

        # Should complete without crashing
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        # Verify the orchestrator was called
        MockOrch.assert_called_once()
        mock_instance.run_simulation.assert_called_once()