"""
Report Generator - Creates comprehensive simulation test reports.
"""

from __future__ import annotations

from datetime import datetime
from ai_simtest_engine.core.semantic_clustering import SemanticFailureClusterer, FailureMessage
from ai_simtest_engine.core.logging import get_logger
from ai_simtest_engine.models import (
    FailurePattern,
    JudgedConversation,
    JudgmentLabel,
    Persona,
    ReportSummary,
    Severity,
    SimulationReport,
)

logger = get_logger(__name__)


class ReportGenerator:
    """
    Generates comprehensive test reports from judged conversations.
    """

    def generate(
        self,
        simulation_id: str,
        simulation_name: str,
        personas: list[Persona],
        judged_conversations: list[JudgedConversation],
        execution_time_seconds: float,
    ) -> SimulationReport:
        """Generate a complete simulation report."""

        summary = self._build_summary(
            simulation_id, simulation_name, personas,
            judged_conversations, execution_time_seconds,
        )

        failure_patterns = self._find_failure_patterns(judged_conversations)
        score_by_judge = self._score_by_judge(judged_conversations)
        score_by_persona = self._score_by_persona_type(judged_conversations, personas)
        problematic = self._most_problematic_personas(judged_conversations, personas)
        recommendations = self._generate_recommendations(
            summary, failure_patterns, score_by_judge
        )

        report = SimulationReport(
            summary=summary,
            failure_patterns=failure_patterns,
            score_by_judge=score_by_judge,
            score_by_persona_type=score_by_persona,
            most_problematic_personas=problematic,
            recommendations=recommendations,
            judged_conversations=judged_conversations,
        )

        logger.info(
            "report_generated",
            pass_rate=summary.pass_rate,
            critical_failures=summary.critical_failures,
            recommendations=len(recommendations),
        )

        return report

    def _build_summary(
        self,
        sim_id: str,
        sim_name: str,
        personas: list[Persona],
        judged: list[JudgedConversation],
        exec_time: float,
    ) -> ReportSummary:
        total_turns = sum(len(jc.judged_turns) for jc in judged)
        all_labels = [jt.overall_label for jc in judged for jt in jc.judged_turns]

        passed = sum(1 for l in all_labels if l == JudgmentLabel.PASS)
        failed = sum(1 for l in all_labels if l == JudgmentLabel.FAIL)
        warnings = sum(1 for l in all_labels if l == JudgmentLabel.WARNING)

        pass_rate = passed / len(all_labels) if all_labels else 0.0
        avg_score = (
            sum(jc.overall_score for jc in judged) / len(judged) if judged else 0.0
        )

        return ReportSummary(
            simulation_id=sim_id,
            simulation_name=sim_name,
            total_personas=len(personas),
            total_conversations=len(judged),
            total_turns=total_turns,
            pass_rate=round(pass_rate, 4),
            average_score=round(avg_score, 4),
            critical_failures=failed,
            warnings=warnings,
            execution_time_seconds=round(exec_time, 2),
        )

    def _find_failure_patterns(
        self, judged: list[JudgedConversation]
    ) -> list[FailurePattern]:
        """Identify recurring failure patterns using Sentence-BERT semantic clustering.

        Groups similar failure messages by meaning (not just shared words).
        Falls back to Jaccard word-overlap if Sentence-BERT is unavailable.

        Example improvement over Jaccard:
          "Bot hallucinated a return policy" + "Made up fake refund rules"
          → Jaccard: SEPARATE clusters (different words)
          → Semantic: SAME cluster (same meaning) ✓
        """
        # Collect all failure messages with metadata
        failure_messages: list[FailureMessage] = []
        for jc in judged:
            conv_id = jc.conversation.id
            for fm in jc.failure_modes:
                failure_messages.append(FailureMessage(
                    message=fm,
                    conversation_id=conv_id,
                ))

        if not failure_messages:
            return []

        # Cluster using semantic similarity (auto-falls back to Jaccard)
        clusterer = SemanticFailureClusterer(similarity_threshold=0.65)
        clusters = clusterer.cluster_failures(failure_messages)

        # Convert clusters to FailurePattern models (top 10)
        patterns = []
        for cluster in clusters[:10]:
            freq = cluster.count
            patterns.append(FailurePattern(
                pattern_name=cluster.representative[:80],
                description=cluster.representative,
                frequency=freq,
                severity=Severity.HIGH if freq > len(judged) * 0.2 else Severity.MEDIUM,
                example_conversation_ids=cluster.conversation_ids[:3],
            ))

        return patterns

    def _score_by_judge(self, judged: list[JudgedConversation]) -> dict[str, float]:
        """Average score per judge across all conversations."""
        judge_scores: dict[str, list[float]] = {}

        for jc in judged:
            for jt in jc.judged_turns:
                for j in jt.judgments:
                    judge_scores.setdefault(j.judge_name, []).append(j.score)

        return {
            name: round(sum(scores) / len(scores), 4)
            for name, scores in judge_scores.items()
            if scores
        }

    def _score_by_persona_type(
        self, judged: list[JudgedConversation], personas: list[Persona]
    ) -> dict[str, float]:
        """Average score per persona type."""
        persona_map = {p.id: p for p in personas}
        type_scores: dict[str, list[float]] = {}

        for jc in judged:
            persona = persona_map.get(jc.conversation.persona_id)
            ptype = persona.persona_type.value if persona else "unknown"
            type_scores.setdefault(ptype, []).append(jc.overall_score)

        return {
            ptype: round(sum(scores) / len(scores), 4)
            for ptype, scores in type_scores.items()
            if scores
        }

    def _most_problematic_personas(
        self, judged: list[JudgedConversation], personas: list[Persona]
    ) -> list[str]:
        """Find personas that triggered the most failures."""
        persona_map = {p.id: p for p in personas}
        persona_scores: dict[str, float] = {}

        for jc in judged:
            pid = jc.conversation.persona_id
            persona = persona_map.get(pid)
            name = persona.name if persona else pid
            persona_scores[name] = jc.overall_score

        # Sort by score (worst first)
        sorted_personas = sorted(persona_scores.items(), key=lambda x: x[1])
        return [name for name, _ in sorted_personas[:5]]

    def _generate_recommendations(
        self,
        summary: ReportSummary,
        patterns: list[FailurePattern],
        score_by_judge: dict[str, float],
    ) -> list[str]:
        """Generate actionable recommendations based on results."""
        recs = []

        if summary.pass_rate < 0.5:
            recs.append("🔴 CRITICAL: Overall pass rate is below 50%. Major improvements needed before production use.")
        elif summary.pass_rate < 0.8:
            recs.append("🟡 HIGH: Pass rate is below 80%. Address top failure patterns before scaling.")

        # Judge-specific recommendations
        for judge, score in score_by_judge.items():
            if score < 0.6:
                recs.append(f"🔴 CRITICAL: '{judge}' judge average score is {score:.0%}. This needs immediate attention.")
            elif score < 0.8:
                recs.append(f"🟡 MEDIUM: '{judge}' judge average score is {score:.0%}. Room for improvement.")

        # Pattern-based recommendations
        for pattern in patterns[:3]:
            recs.append(
                f"🟡 Address recurring issue: '{pattern.pattern_name}' "
                f"(found in {pattern.frequency} responses)"
            )

        if not recs:
            recs.append("🟢 All metrics look good! Consider increasing test coverage with more personas.")

        return recs