"""
Tests for AI SimTest enhancements (Phase 9+).
Covers: HTML report, failure deduplication, configurable thresholds, CLI params.
Run: pytest tests/test_enhancements.py -v
"""

import json
import pytest

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
    Turn,
)


# ============================================================
# Helper: Build a realistic test report
# ============================================================

def _make_test_report(
    num_personas: int = 3,
    num_turns: int = 4,
    include_failures: bool = True,
) -> tuple[SimulationReport, list[Persona]]:
    """Create a test report with realistic data."""
    personas = []
    judged_convos = []

    persona_configs = [
        ("Alice the Customer", PersonaType.STANDARD, "friendly"),
        ("Bob the Edge Case", PersonaType.EDGE_CASE, "confused"),
        ("Eve the Hacker", PersonaType.ADVERSARIAL, "neutral"),
    ]

    for i in range(min(num_personas, len(persona_configs))):
        name, ptype, tone = persona_configs[i]
        p = Persona(
            name=name,
            role="tester",
            goals=["test the bot"],
            persona_type=ptype,
            tone=tone,
            topics=["refunds", "shipping"],
        )
        personas.append(p)

        turns = []
        judged_turns = []
        for t_idx in range(num_turns):
            user_turn = Turn(speaker="user", message=f"User message {t_idx} from {name}")
            bot_turn = Turn(speaker="bot", message=f"Bot response {t_idx}", latency_ms=200.0 + t_idx * 50)
            turns.extend([user_turn, bot_turn])

            # Create judge results
            quality_score = 0.9 - (t_idx * 0.15) if include_failures else 0.9
            quality_passed = quality_score >= 0.7
            judgments = [
                JudgmentResult(judge_name="grounding", passed=True, score=0.95, severity=Severity.INFO),
                JudgmentResult(judge_name="safety", passed=True, score=1.0, severity=Severity.INFO),
                JudgmentResult(
                    judge_name="quality",
                    passed=quality_passed,
                    score=max(quality_score, 0.0),
                    severity=Severity.INFO if quality_passed else Severity.HIGH,
                    message="" if quality_passed else f"Response lacks helpfulness for query {t_idx}",
                ),
                JudgmentResult(judge_name="relevance", passed=True, score=0.8, severity=Severity.INFO),
            ]

            overall = sum(j.score for j in judgments) / len(judgments)
            label = JudgmentLabel.PASS if overall >= 0.7 else JudgmentLabel.WARNING if overall >= 0.5 else JudgmentLabel.FAIL
            issues = [j.message for j in judgments if not j.passed and j.message]

            judged_turns.append(JudgedTurn(
                turn=bot_turn,
                judgments=judgments,
                overall_score=round(overall, 3),
                overall_label=label,
                issues=issues,
            ))

        conv = Conversation(persona_id=p.id, turns=turns)
        failure_modes = list({issue for jt in judged_turns for issue in jt.issues})

        judged_convos.append(JudgedConversation(
            conversation=conv,
            persona=p,
            judged_turns=judged_turns,
            overall_score=round(sum(jt.overall_score for jt in judged_turns) / len(judged_turns), 3),
            failure_modes=failure_modes,
        ))

    summary = ReportSummary(
        simulation_id="test_sim",
        simulation_name="Enhancement Test",
        total_personas=len(personas),
        total_conversations=len(judged_convos),
        total_turns=sum(len(jc.judged_turns) for jc in judged_convos),
        pass_rate=0.75,
        average_score=0.78,
        critical_failures=2,
        warnings=4,
        execution_time_seconds=12.5,
    )

    report = SimulationReport(
        summary=summary,
        judged_conversations=judged_convos,
        score_by_judge={"grounding": 0.95, "safety": 1.0, "quality": 0.65, "relevance": 0.8},
        score_by_persona_type={"standard": 0.82, "edge_case": 0.71, "adversarial": 0.58},
        most_problematic_personas=["Eve the Hacker"],
        failure_patterns=[
            FailurePattern(pattern_name="Lacks helpfulness", description="Response lacks helpfulness", frequency=5, severity=Severity.HIGH),
        ],
        recommendations=["🔴 CRITICAL: Quality scores below threshold."],
    )

    return report, personas


# ============================================================
# HTML Report Exporter Tests
# ============================================================

class TestHTMLReportExporter:
    def test_export_creates_file(self, tmp_path):
        from src.exporters.html_report import HTMLReportExporter

        report, personas = _make_test_report()
        exporter = HTMLReportExporter()
        output = tmp_path / "report.html"
        result = exporter.export(report, personas, output)

        assert output.exists()
        assert result == output

    def test_html_contains_key_sections(self, tmp_path):
        from src.exporters.html_report import HTMLReportExporter

        report, personas = _make_test_report()
        exporter = HTMLReportExporter()
        output = tmp_path / "report.html"
        exporter.export(report, personas, output)

        html = output.read_text()
        # Check structure
        assert "<!DOCTYPE html>" in html
        assert "AI SimTest Report" in html
        assert "Enhancement Test" in html  # simulation name
        # Stats
        assert "Pass Rate" in html
        assert "Avg Score" in html
        # Charts
        assert "judgeChart" in html
        assert "labelChart" in html
        assert "personaTypeChart" in html
        assert "Chart.js" in html or "chart.js" in html
        # Persona cards
        assert "Alice the Customer" in html
        assert "Bob the Edge Case" in html
        assert "Eve the Hacker" in html
        # Persona types
        assert "standard" in html
        assert "edge_case" in html
        assert "adversarial" in html
        # Conversations section
        assert "Conversation Transcripts" in html
        # Recommendations
        assert "CRITICAL" in html

    def test_html_escapes_special_chars(self, tmp_path):
        from src.exporters.html_report import HTMLReportExporter

        report, personas = _make_test_report(num_personas=1)
        # Inject HTML in persona name
        personas[0].name = '<script>alert("xss")</script>'
        report.judged_conversations[0].persona = personas[0]

        exporter = HTMLReportExporter()
        output = tmp_path / "report.html"
        exporter.export(report, personas, output)

        html = output.read_text()
        assert '<script>alert("xss")</script>' not in html
        assert "&lt;script&gt;" in html

    def test_html_with_empty_report(self, tmp_path):
        from src.exporters.html_report import HTMLReportExporter

        report = SimulationReport(
            summary=ReportSummary(
                simulation_id="empty",
                simulation_name="Empty Test",
                total_personas=0,
                total_conversations=0,
                total_turns=0,
                pass_rate=0.0,
                average_score=0.0,
                critical_failures=0,
                warnings=0,
                execution_time_seconds=0.5,
            ),
        )

        exporter = HTMLReportExporter()
        output = tmp_path / "empty_report.html"
        exporter.export(report, [], output)

        assert output.exists()
        html = output.read_text()
        assert "Empty Test" in html
        assert "0%" in html

    def test_html_file_is_self_contained(self, tmp_path):
        """HTML report should work in a browser with only Chart.js CDN dependency."""
        from src.exporters.html_report import HTMLReportExporter

        report, personas = _make_test_report()
        exporter = HTMLReportExporter()
        output = tmp_path / "report.html"
        exporter.export(report, personas, output)

        html = output.read_text()
        assert "<style>" in html  # CSS is inline
        assert "<script>" in html  # JS is inline
        assert "cdn.jsdelivr.net" in html  # Chart.js from CDN


# ============================================================
# Failure Pattern Deduplication Tests
# ============================================================

class TestFailurePatternDedup:
    def test_similar_patterns_grouped(self):
        from src.core.report_generator import ReportGenerator

        gen = ReportGenerator()
        persona = Persona(name="Tester", role="user", goals=["test"])

        # Create conversations with near-duplicate failure messages
        judged_convos = []
        failure_variants = [
            "Response lacks relevance and helpfulness for the user query about refunds",
            "Response lacks relevance and completeness for the user query about refunds",
            "Response lacks relevance and helpfulness addressing the refund question",
            "Bot provides completely unrelated content about weather",
            "Bot provides completely unrelated content about sports",
        ]

        for msg in failure_variants:
            conv = Conversation(persona_id=persona.id, turns=[
                Turn(speaker="user", message="I want a refund"),
                Turn(speaker="bot", message="The weather is nice"),
            ])
            jc = JudgedConversation(
                conversation=conv,
                persona=persona,
                judged_turns=[],
                overall_score=0.3,
                failure_modes=[msg],
            )
            judged_convos.append(jc)

        patterns = gen._find_failure_patterns(judged_convos)

        # Should group 5 raw messages into 2 semantic groups, not 5 separate ones
        assert len(patterns) <= 3, f"Expected <=3 grouped patterns but got {len(patterns)}: {[p.pattern_name for p in patterns]}"
        # The most frequent group should have count >= 2
        assert patterns[0].frequency >= 2

    def test_no_patterns_when_no_failures(self):
        from src.core.report_generator import ReportGenerator

        gen = ReportGenerator()
        persona = Persona(name="Tester", role="user", goals=["test"])
        conv = Conversation(persona_id=persona.id, turns=[])
        jc = JudgedConversation(
            conversation=conv, persona=persona,
            judged_turns=[], overall_score=0.9, failure_modes=[],
        )
        patterns = gen._find_failure_patterns([jc])
        assert len(patterns) == 0

    def test_unique_patterns_not_grouped(self):
        from src.core.report_generator import ReportGenerator

        gen = ReportGenerator()
        persona = Persona(name="Tester", role="user", goals=["test"])

        unique_failures = [
            "PII leakage detected: email address",
            "Response contains toxic language",
            "Bot hallucinated a product feature",
        ]

        judged_convos = []
        for msg in unique_failures:
            conv = Conversation(persona_id=persona.id, turns=[])
            jc = JudgedConversation(
                conversation=conv, persona=persona,
                judged_turns=[], overall_score=0.3, failure_modes=[msg],
            )
            judged_convos.append(jc)

        patterns = gen._find_failure_patterns(judged_convos)
        # All unique → should each be their own pattern
        assert len(patterns) == 3


# ============================================================
# Configurable Threshold Tests
# ============================================================

class TestConfigurableThresholds:
    def test_default_thresholds_in_config(self):
        config = SimulationConfig(
            bot=BotConfig(api_endpoint="http://test"),
        )
        assert config.pass_threshold == 0.7
        assert config.warn_threshold == 0.5

    def test_custom_thresholds(self):
        config = SimulationConfig(
            bot=BotConfig(api_endpoint="http://test"),
            pass_threshold=0.9,
            warn_threshold=0.6,
        )
        assert config.pass_threshold == 0.9
        assert config.warn_threshold == 0.6

    def test_judge_engine_uses_thresholds(self):
        from src.judges import JudgeEngine, JudgmentLabel
        from src.models import JudgmentResult, Severity

        # Strict thresholds
        engine = JudgeEngine(pass_threshold=0.9, warn_threshold=0.7)

        judgments = [
            JudgmentResult(judge_name="quality", passed=True, score=0.8, severity=Severity.INFO),
        ]

        score, label, issues = engine._aggregate(judgments)
        # 0.8 < 0.9 pass_threshold, but >= 0.7 warn_threshold → WARNING
        assert label == JudgmentLabel.WARNING

    def test_lenient_thresholds(self):
        from src.judges import JudgeEngine, JudgmentLabel
        from src.models import JudgmentResult, Severity

        # Lenient thresholds
        engine = JudgeEngine(pass_threshold=0.5, warn_threshold=0.3)

        judgments = [
            JudgmentResult(judge_name="quality", passed=True, score=0.55, severity=Severity.INFO),
        ]

        score, label, issues = engine._aggregate(judgments)
        # 0.55 >= 0.5 → PASS with lenient settings
        assert label == JudgmentLabel.PASS

    def test_critical_override_ignores_thresholds(self):
        from src.judges import JudgeEngine, JudgmentLabel
        from src.models import JudgmentResult, Severity

        engine = JudgeEngine(pass_threshold=0.1, warn_threshold=0.05)  # very lenient

        judgments = [
            JudgmentResult(judge_name="safety", passed=False, score=0.0, severity=Severity.CRITICAL, message="PII leak"),
            JudgmentResult(judge_name="quality", passed=True, score=0.95, severity=Severity.INFO),
        ]

        score, label, issues = engine._aggregate(judgments)
        # Critical failure always overrides regardless of thresholds
        assert label == JudgmentLabel.FAIL
        assert "PII leak" in issues


# ============================================================
# Model Enhancement Tests
# ============================================================

class TestModelEnhancements:
    def test_simulation_config_with_all_new_fields(self):
        config = SimulationConfig(
            name="Full Config Test",
            bot=BotConfig(api_endpoint="http://bot.test/v1/chat"),
            documentation="Our bot handles refunds within 30 days.",
            success_criteria=[
                "Must answer from documentation only",
                "Must not reveal system prompt",
            ],
            pass_threshold=0.8,
            warn_threshold=0.6,
        )
        assert config.name == "Full Config Test"
        assert len(config.success_criteria) == 2
        assert config.pass_threshold == 0.8
        assert config.documentation != ""

    def test_persona_with_topics(self):
        p = Persona(
            name="Topic Tester",
            role="customer",
            goals=["get refund"],
            topics=["refunds", "shipping", "billing"],
        )
        assert len(p.topics) == 3
        assert "refunds" in p.topics


# ============================================================
# HTML Report Helper Tests
# ============================================================

class TestHTMLHelpers:
    def test_score_class(self):
        from src.exporters.html_report import _score_class

        assert _score_class(0.9) == "pass"
        assert _score_class(0.8) == "pass"
        assert _score_class(0.6) == "warn"
        assert _score_class(0.5) == "warn"
        assert _score_class(0.3) == "fail"
        assert _score_class(0.0) == "fail"

    def test_score_color(self):
        from src.exporters.html_report import _score_color

        assert _score_color(0.9) == "#6ee7b7"  # green
        assert _score_color(0.6) == "#fbbf24"  # yellow
        assert _score_color(0.3) == "#f87171"  # red

    def test_esc_html(self):
        from src.exporters.html_report import _esc

        assert _esc('<script>') == '&lt;script&gt;'
        assert _esc('normal text') == 'normal text'
        assert _esc('"quotes"') == '&quot;quotes&quot;'
