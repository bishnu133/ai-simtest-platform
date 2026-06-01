"""
Tests for AI SimTest core components.
Run: pytest tests/ -v
"""

import asyncio
import json
import pytest

from src.models import (
    BotConfig,
    Conversation,
    JudgedConversation,
    JudgedTurn,
    JudgmentLabel,
    JudgmentResult,
    Persona,
    PersonaType,
    Severity,
    SimulationConfig,
    TechnicalLevel,
    Turn,
)


# ============================================================
# Model Tests
# ============================================================

class TestPersonaModel:
    def test_create_standard_persona(self):
        persona = Persona(
            name="Test User",
            role="customer",
            goals=["get help"],
        )
        assert persona.name == "Test User"
        assert persona.persona_type == PersonaType.STANDARD
        assert not persona.is_adversarial
        assert persona.id.startswith("persona_")

    def test_create_adversarial_persona(self):
        persona = Persona(
            name="Attacker",
            role="adversarial tester",
            goals=["break the bot"],
            persona_type=PersonaType.ADVERSARIAL,
            adversarial_tactics=["prompt_injection"],
        )
        assert persona.is_adversarial
        assert "prompt_injection" in persona.adversarial_tactics


class TestConversationModel:
    def test_empty_conversation(self):
        conv = Conversation(persona_id="p1")
        assert conv.turn_count == 0
        assert conv.bot_turns == []
        assert conv.user_turns == []

    def test_conversation_with_turns(self):
        conv = Conversation(
            persona_id="p1",
            turns=[
                Turn(speaker="user", message="Hello"),
                Turn(speaker="bot", message="Hi there!"),
                Turn(speaker="user", message="Help me"),
                Turn(speaker="bot", message="Sure!"),
            ],
        )
        assert conv.turn_count == 4
        assert len(conv.bot_turns) == 2
        assert len(conv.user_turns) == 2

    def test_format_history(self):
        conv = Conversation(
            persona_id="p1",
            turns=[
                Turn(speaker="user", message="Hello"),
                Turn(speaker="bot", message="Hi!"),
            ],
        )
        history = conv.format_history()
        assert "User: Hello" in history
        assert "Bot: Hi!" in history


class TestJudgmentModels:
    def test_judgment_result(self):
        result = JudgmentResult(
            judge_name="safety",
            passed=True,
            score=1.0,
            severity=Severity.INFO,
            message="All good",
        )
        assert result.passed
        assert result.score == 1.0

    def test_judged_conversation_pass_rate(self):
        jc = JudgedConversation(
            conversation=Conversation(persona_id="p1"),
            persona=Persona(name="Test", role="user", goals=["test"]),
            judged_turns=[
                JudgedTurn(
                    turn=Turn(speaker="bot", message="Good response"),
                    overall_label=JudgmentLabel.PASS,
                    overall_score=0.9,
                ),
                JudgedTurn(
                    turn=Turn(speaker="bot", message="Bad response"),
                    overall_label=JudgmentLabel.FAIL,
                    overall_score=0.3,
                ),
            ],
        )
        assert jc.pass_rate == 0.5


class TestSimulationConfig:
    def test_default_config(self):
        config = SimulationConfig(
            bot=BotConfig(api_endpoint="http://localhost:8080/chat"),
        )
        assert config.num_personas == 20
        assert config.max_turns_per_conversation == 15
        assert len(config.judges) == 4

    def test_custom_config(self):
        config = SimulationConfig(
            name="My Test",
            bot=BotConfig(
                api_endpoint="http://localhost:8080/chat",
                api_key="test-key",
            ),
            num_personas=50,
            max_turns_per_conversation=10,
        )
        assert config.name == "My Test"
        assert config.num_personas == 50


# ============================================================
# Judge Tests
# ============================================================

class TestRelevanceJudge:
    @pytest.mark.asyncio
    async def test_relevant_response(self):
        from src.judges.quality_judge import RelevanceJudge

        judge = RelevanceJudge()
        result = await judge.evaluate(
            response="Your refund has been processed and will appear in 3-5 business days.",
            conversation_history=[
                Turn(speaker="user", message="I want a refund for my order"),
            ],
        )
        assert result.passed
        assert result.score > 0.5

    @pytest.mark.asyncio
    async def test_irrelevant_response(self):
        from src.judges.quality_judge import RelevanceJudge

        judge = RelevanceJudge()
        result = await judge.evaluate(
            response="The weather today is sunny with a high of 72 degrees.",
            conversation_history=[
                Turn(speaker="user", message="I want a refund for my order"),
            ],
        )
        # Low keyword overlap = likely not relevant
        assert result.score < 0.8


# ============================================================
# Report Generator Tests
# ============================================================

class TestReportGenerator:
    def test_generate_empty_report(self):
        from src.core.report_generator import ReportGenerator

        gen = ReportGenerator()
        report = gen.generate(
            simulation_id="test",
            simulation_name="Test Run",
            personas=[],
            judged_conversations=[],
            execution_time_seconds=10.0,
        )
        assert report.summary.total_conversations == 0
        assert report.summary.pass_rate == 0.0

    def test_generate_report_with_data(self):
        from src.core.report_generator import ReportGenerator

        gen = ReportGenerator()

        persona = Persona(name="Tester", role="user", goals=["test"])
        conv = Conversation(persona_id=persona.id, turns=[
            Turn(speaker="user", message="Hello"),
            Turn(speaker="bot", message="Hi!"),
        ])

        jc = JudgedConversation(
            conversation=conv,
            persona=persona,
            judged_turns=[
                JudgedTurn(
                    turn=conv.turns[1],
                    overall_label=JudgmentLabel.PASS,
                    overall_score=0.9,
                    judgments=[
                        JudgmentResult(judge_name="quality", passed=True, score=0.9),
                    ],
                ),
            ],
            overall_score=0.9,
        )

        report = gen.generate(
            simulation_id="test",
            simulation_name="Test",
            personas=[persona],
            judged_conversations=[jc],
            execution_time_seconds=5.0,
        )

        assert report.summary.total_conversations == 1
        assert report.summary.pass_rate == 1.0
        assert report.summary.average_score == 0.9


# ============================================================
# Exporter Tests
# ============================================================

class TestDatasetExporter:
    def test_export_jsonl(self, tmp_path):
        from src.exporters.dataset_exporter import DatasetExporter
        from src.models import SimulationReport, ReportSummary

        exporter = DatasetExporter()

        persona = Persona(name="Test", role="user", goals=["test"])
        conv = Conversation(persona_id=persona.id, turns=[
            Turn(speaker="user", message="Hi"),
            Turn(speaker="bot", message="Hello!"),
        ])

        report = SimulationReport(
            summary=ReportSummary(
                simulation_id="test",
                simulation_name="Test",
                total_personas=1,
                total_conversations=1,
                total_turns=2,
                pass_rate=1.0,
                average_score=0.9,
                critical_failures=0,
                warnings=0,
                execution_time_seconds=1.0,
            ),
            judged_conversations=[
                JudgedConversation(
                    conversation=conv,
                    persona=persona,
                    overall_score=0.9,
                ),
            ],
        )

        output = tmp_path / "test.jsonl"
        exporter.export_jsonl(report, output)

        assert output.exists()
        with open(output) as f:
            data = json.loads(f.readline())
            assert data["persona_name"] == "Test"
