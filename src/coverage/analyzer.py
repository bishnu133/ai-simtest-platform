"""
Coverage Analyzer — computes test coverage across 4 dimensions.

Non-breaking integration: takes existing simulation data (personas,
judged conversations) and computes coverage metrics from it.
Zero changes to orchestrator, simulator, or judges required.
"""

from __future__ import annotations

import re
from collections import Counter

from src.coverage.constants import (
    SCENARIO_METADATA,
    ALL_SCENARIO_CATEGORIES,
)
from src.coverage.models import (
    CoverageConfig,
    CoverageGrade,
    CoverageReport,
    PersonaTypeCoverage,
    TopicCoverage,
    ScenarioCoverage,
    JudgeCoverage,
)


class CoverageAnalyzer:
    """
    Analyzes test coverage across 4 dimensions:
      1. Persona-type distribution
      2. Topic presence in conversations
      3. Scenario category/difficulty breadth
      4. Judge evaluation completeness
    """

    def analyze(
        self,
        personas: list,
        judged_conversations: list,
        config: CoverageConfig | None = None,
    ) -> CoverageReport:
        """
        Compute full coverage analysis.

        Args:
            personas: List of Persona objects from the simulation.
            judged_conversations: List of JudgedConversation objects.
            config: What SHOULD have been tested (topics, scenarios, etc.).

        Returns:
            CoverageReport with per-dimension scores and overall grade.
        """
        if config is None:
            config = CoverageConfig()

        report = CoverageReport()
        report.total_personas = len(personas)
        report.total_conversations = len(judged_conversations)

        report.persona_type = self._analyze_persona_types(
            personas, judged_conversations, config
        )
        report.topic = self._analyze_topics(judged_conversations, config)
        report.scenario = self._analyze_scenarios(config)
        report.judge = self._analyze_judges(judged_conversations, config)

        self._compute_overall(report, config)
        return report

    # ── Dimension 1: Persona-Type Coverage ──────────────────────────

    def _analyze_persona_types(
        self,
        personas: list,
        judged_conversations: list,
        config: CoverageConfig,
    ) -> PersonaTypeCoverage:
        """Analyze persona-type distribution vs. expected 70/20/10."""
        result = PersonaTypeCoverage()
        result.expected_percentages = dict(config.expected_persona_distribution)

        type_counts: Counter = Counter()
        for p in personas:
            type_counts[_get_persona_type(p)] += 1

        result.type_counts = dict(type_counts)
        result.total = len(personas)

        if result.total == 0:
            result.score = 0.0
            result.missing_types = list(config.expected_persona_distribution.keys())
            return result

        result.type_percentages = {
            ptype: count / result.total for ptype, count in type_counts.items()
        }

        for expected_type in config.expected_persona_distribution:
            if type_counts.get(expected_type, 0) < config.min_conversations_per_type:
                result.missing_types.append(expected_type)

        for ptype, expected_pct in config.expected_persona_distribution.items():
            actual_pct = result.type_percentages.get(ptype, 0.0)
            result.deviation[ptype] = actual_pct - expected_pct

        # Score: presence (60%) + distribution accuracy (40%)
        if result.missing_types:
            presence_score = 1.0 - (
                len(result.missing_types) / len(config.expected_persona_distribution)
            )
        else:
            presence_score = 1.0

        if config.expected_persona_distribution:
            total_dev = sum(
                abs(result.deviation.get(pt, 0.0))
                for pt in config.expected_persona_distribution
            )
            avg_dev = total_dev / len(config.expected_persona_distribution)
            deviation_score = max(
                0.0, 1.0 - (avg_dev / (2 * config.distribution_tolerance))
            )
        else:
            deviation_score = 1.0

        result.score = round(presence_score * 0.6 + deviation_score * 0.4, 4)
        return result

    # ── Dimension 2: Topic Coverage ─────────────────────────────────

    def _analyze_topics(
        self,
        judged_conversations: list,
        config: CoverageConfig,
    ) -> TopicCoverage:
        """Analyze what % of defined --topics were discussed."""
        result = TopicCoverage()
        result.defined_topics = list(config.defined_topics)

        if not config.defined_topics:
            result.score = 1.0
            return result

        # Collect all message text
        all_messages = []
        for jc in judged_conversations:
            conv = _get_conversation(jc)
            if conv:
                for turn in _get_turns(conv):
                    msg = _get_message(turn)
                    if msg:
                        all_messages.append(msg.lower())

        combined_text = " ".join(all_messages)

        for topic in config.defined_topics:
            pattern = re.compile(re.escape(topic.lower().strip()), re.IGNORECASE)
            matches = pattern.findall(combined_text)
            count = len(matches)
            result.topic_mention_counts[topic] = count
            if count > 0:
                result.covered_topics.append(topic)
            else:
                result.uncovered_topics.append(topic)

        result.score = round(
            len(result.covered_topics) / len(config.defined_topics), 4
        ) if config.defined_topics else 1.0

        return result

    # ── Dimension 3: Scenario Coverage ──────────────────────────────

    def _analyze_scenarios(self, config: CoverageConfig) -> ScenarioCoverage:
        """Analyze scenario category and difficulty breadth."""
        result = ScenarioCoverage()
        result.requested_scenarios = list(config.scenario_ids)
        result.stress_included = config.stress_enabled

        if not config.scenario_ids and not config.stress_enabled:
            result.score = 0.0
            result.missing_categories = list(result.all_categories)
            return result

        categories_seen = set()
        difficulties_seen = set()

        for sid in config.scenario_ids:
            meta = SCENARIO_METADATA.get(sid, {})
            if meta.get("category"):
                categories_seen.add(meta["category"])
            if meta.get("difficulty"):
                difficulties_seen.add(meta["difficulty"])

        if config.stress_enabled:
            categories_seen.add("memory")

        result.categories_tested = sorted(categories_seen)
        result.difficulties_tested = sorted(difficulties_seen)
        result.missing_categories = sorted(
            set(result.all_categories) - categories_seen
        )

        # 60% category breadth + 25% difficulty spread + 15% stress bonus
        cat_score = (
            len(categories_seen) / len(result.all_categories)
            if result.all_categories
            else 0
        )
        diff_score = len(difficulties_seen) / 3.0
        stress_bonus = 1.0 if config.stress_enabled else 0.0

        result.score = round(
            cat_score * 0.60 + diff_score * 0.25 + stress_bonus * 0.15, 4
        )
        return result

    # ── Dimension 4: Judge Coverage ─────────────────────────────────

    def _analyze_judges(
        self,
        judged_conversations: list,
        config: CoverageConfig,
    ) -> JudgeCoverage:
        """Analyze judge evaluation completeness."""
        result = JudgeCoverage()
        result.expected_judges = list(config.expected_judges)

        judge_scores: dict[str, list[float]] = {}
        for jc in judged_conversations:
            for jt in _get_judged_turns(jc):
                for j in _get_judgments(jt):
                    name = _get_judge_name(j)
                    score = _get_judge_score(j)
                    if name:
                        judge_scores.setdefault(name, []).append(score)

        result.active_judges = sorted(judge_scores.keys())
        result.verdict_counts = {
            name: len(scores) for name, scores in judge_scores.items()
        }

        result.missing_judges = sorted(
            set(config.expected_judges) - set(result.active_judges)
        )

        for name, scores in judge_scores.items():
            if len(scores) >= 3:
                if all(s >= 0.99 for s in scores) or all(s <= 0.01 for s in scores):
                    result.suspicious_judges.append(name)

        if config.expected_judges:
            active_expected = len(
                set(config.expected_judges) & set(result.active_judges)
            )
            base_score = active_expected / len(config.expected_judges)
        else:
            base_score = 1.0 if result.active_judges else 0.0

        suspicion_penalty = len(result.suspicious_judges) * 0.05
        result.score = round(max(0.0, base_score - suspicion_penalty), 4)
        return result

    # ── Composite Score ─────────────────────────────────────────────

    def _compute_overall(self, report: CoverageReport, config: CoverageConfig):
        """Compute weighted overall, grade, gaps, and recommendations."""

        # Dynamic weights — dimensions not configured get reduced weight
        weights = {"persona_type": 0.25, "judge": 0.25}
        weights["topic"] = 0.25 if config.defined_topics else 0.0
        weights["scenario"] = (
            0.25
            if (config.scenario_ids or config.stress_enabled)
            else 0.15  # Absence is still a gap, but smaller penalty
        )

        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        report.dimension_weights = weights
        report.dimension_scores = {
            "persona_type": report.persona_type.score,
            "topic": report.topic.score,
            "scenario": report.scenario.score,
            "judge": report.judge.score,
        }

        overall = sum(
            report.dimension_scores[dim] * weights[dim] for dim in weights
        )
        report.overall_coverage = round(overall, 4)
        report.grade = CoverageGrade.from_score(report.overall_coverage)

        report.gaps = _generate_gaps(report, config)
        report.recommendations = _generate_recommendations(report, config)


# ━━━ Gap & Recommendation Generators ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _generate_gaps(report: CoverageReport, config: CoverageConfig) -> list[str]:
    """Identify concrete coverage gaps."""
    gaps = []

    for missing in report.persona_type.missing_types:
        gaps.append(f"No {missing} personas tested")

    for ptype, dev in report.persona_type.deviation.items():
        if abs(dev) > config.distribution_tolerance:
            direction = "over" if dev > 0 else "under"
            gaps.append(f"{ptype} personas {direction}-represented by {abs(dev)*100:.0f}%")

    for topic in report.topic.uncovered_topics:
        gaps.append(f"Topic '{topic}' not covered in any conversation")

    for cat in report.scenario.missing_categories:
        gaps.append(f"Scenario category '{cat}' not tested")

    if not config.scenario_ids and not config.stress_enabled:
        gaps.append("No structured scenarios or stress tests were used")

    for judge in report.judge.missing_judges:
        gaps.append(f"Judge '{judge}' produced no verdicts")

    for judge in report.judge.suspicious_judges:
        gaps.append(f"Judge '{judge}' produced uniform scores (possible misconfiguration)")

    if report.total_conversations < 5:
        gaps.append(
            f"Only {report.total_conversations} conversations — "
            f"consider at least 10 for meaningful coverage"
        )

    return gaps


def _generate_recommendations(
    report: CoverageReport, config: CoverageConfig
) -> list[str]:
    """Generate actionable recommendations to improve coverage."""
    recs = []

    if report.grade in (CoverageGrade.D, CoverageGrade.F):
        recs.append(
            f"Coverage grade is {report.grade.value}. "
            f"Increase test breadth before relying on results."
        )

    if report.persona_type.missing_types:
        missing = ", ".join(report.persona_type.missing_types)
        recs.append(f"Add {missing} personas — use --personas N with a larger count.")

    if report.topic.uncovered_topics:
        uncovered = ", ".join(report.topic.uncovered_topics[:3])
        recs.append(
            f"Topics not exercised: {uncovered}. "
            f"Add documentation or success criteria for these topics."
        )

    if not config.scenario_ids and not config.stress_enabled:
        recs.append(
            "Add --scenarios all for structured test patterns, "
            "or --stress-memory for memory endurance testing."
        )
    elif report.scenario.missing_categories:
        missing = ", ".join(report.scenario.missing_categories[:3])
        recs.append(f"Untested scenario categories: {missing}. Add with --scenarios <category>.")

    if not config.stress_enabled and config.scenario_ids:
        recs.append("Consider adding --stress-memory for context endurance testing.")

    if report.judge.suspicious_judges:
        suspicious = ", ".join(report.judge.suspicious_judges)
        recs.append(f"Judge(s) {suspicious} gave uniform scores — verify configuration.")

    if report.judge.missing_judges:
        missing = ", ".join(report.judge.missing_judges)
        recs.append(f"Judge(s) {missing} inactive — check initialization.")

    if report.total_conversations < 10:
        recs.append(
            f"Only {report.total_conversations} conversations tested. "
            f"Use --personas 20+ for statistically meaningful results."
        )

    return recs


# ━━━ Accessor Helpers (handle both Pydantic models and dicts) ━━━━━

def _get_persona_type(persona) -> str:
    if hasattr(persona, "persona_type"):
        pt = persona.persona_type
        return pt.value if hasattr(pt, "value") else str(pt)
    if isinstance(persona, dict):
        return persona.get("persona_type", "unknown")
    return "unknown"


def _get_conversation(jc):
    if hasattr(jc, "conversation"):
        return jc.conversation
    if isinstance(jc, dict):
        return jc.get("conversation")
    return None


def _get_turns(conv) -> list:
    if hasattr(conv, "turns"):
        return conv.turns
    if isinstance(conv, dict):
        return conv.get("turns", [])
    return []


def _get_message(turn) -> str | None:
    if hasattr(turn, "message"):
        return turn.message
    if isinstance(turn, dict):
        return turn.get("message")
    return None


def _get_judged_turns(jc) -> list:
    if hasattr(jc, "judged_turns"):
        return jc.judged_turns
    if isinstance(jc, dict):
        return jc.get("judged_turns", [])
    return []


def _get_judgments(jt) -> list:
    if hasattr(jt, "judgments"):
        return jt.judgments
    if isinstance(jt, dict):
        return jt.get("judgments", [])
    return []


def _get_judge_name(j) -> str | None:
    if hasattr(j, "judge_name"):
        return j.judge_name
    if isinstance(j, dict):
        return j.get("judge_name")
    return None


def _get_judge_score(j) -> float:
    if hasattr(j, "score"):
        return j.score
    if isinstance(j, dict):
        return j.get("score", 0.0)
    return 0.0
