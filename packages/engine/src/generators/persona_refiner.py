"""
Iterative Persona Refinement - Snowglobe's killer feature.
After a simulation run, analyzes which persona types triggered the most failures,
then generates MORE personas with similar profiles to drill deeper into weak spots.
"""

from __future__ import annotations

import json
from collections import Counter

from src.core.logging import get_logger
from src.models import (
    JudgedConversation,
    JudgmentLabel,
    Persona,
    PersonaType,
    SimulationReport,
)

logger = get_logger(__name__)


class PersonaRefiner:
    """
    Analyzes simulation results to identify high-risk persona profiles,
    then generates refined personas that drill deeper into failure areas.
    """

    def analyze_failures(
        self, report: SimulationReport
    ) -> dict:
        """
        Analyze a simulation report to identify failure patterns by persona attributes.

        Returns a dict with:
          - high_risk_personas: personas with highest failure rates
          - failure_by_type: failure rates by persona type
          - failure_by_tone: failure rates by persona tone
          - failure_by_topic: failure rates by topic
          - refinement_strategy: recommended focus areas
        """
        if not report.judged_conversations:
            return {"high_risk_personas": [], "refinement_strategy": []}

        # Track per-persona performance
        persona_stats: list[dict] = []
        type_scores: dict[str, list[float]] = {}
        tone_scores: dict[str, list[float]] = {}
        topic_failures: Counter = Counter()

        for jc in report.judged_conversations:
            p = jc.persona
            fail_rate = 1.0 - jc.pass_rate

            persona_stats.append({
                "persona": p,
                "score": jc.overall_score,
                "pass_rate": jc.pass_rate,
                "fail_rate": fail_rate,
                "failure_modes": jc.failure_modes,
                "num_failures": len([
                    jt for jt in jc.judged_turns
                    if jt.overall_label == JudgmentLabel.FAIL
                ]),
            })

            # Group by type
            type_key = p.persona_type.value
            type_scores.setdefault(type_key, []).append(jc.overall_score)

            # Group by tone
            tone_scores.setdefault(p.tone, []).append(jc.overall_score)

            # Track which topics trigger failures
            if fail_rate > 0.3:
                for topic in p.topics:
                    topic_failures[topic] += 1

        # Sort by failure rate (worst first)
        persona_stats.sort(key=lambda x: x["fail_rate"], reverse=True)

        # High-risk personas (fail rate > 50%)
        high_risk = [ps for ps in persona_stats if ps["fail_rate"] > 0.5]

        # Failure rates by type
        failure_by_type = {
            t: 1.0 - (sum(scores) / len(scores))
            for t, scores in type_scores.items()
            if scores
        }

        # Failure rates by tone
        failure_by_tone = {
            t: 1.0 - (sum(scores) / len(scores))
            for t, scores in tone_scores.items()
            if scores
        }

        # Build refinement strategy
        strategy = self._build_strategy(
            high_risk, failure_by_type, failure_by_tone, topic_failures
        )

        return {
            "high_risk_personas": high_risk[:10],  # Top 10 worst
            "failure_by_type": failure_by_type,
            "failure_by_tone": failure_by_tone,
            "failure_by_topic": dict(topic_failures.most_common(10)),
            "refinement_strategy": strategy,
        }

    def build_refinement_prompt(
        self,
        analysis: dict,
        num_personas: int = 10,
        original_bot_description: str = "",
        original_documentation: str = "",
    ) -> str:
        """
        Build an LLM prompt to generate refined personas that focus on weak spots.
        """
        # Extract key failure patterns
        high_risk = analysis.get("high_risk_personas", [])
        strategy = analysis.get("refinement_strategy", [])
        failure_by_type = analysis.get("failure_by_type", {})
        failure_by_tone = analysis.get("failure_by_tone", {})
        failure_by_topic = analysis.get("failure_by_topic", {})

        # Build examples of high-risk personas
        risk_examples = ""
        for ps in high_risk[:5]:
            p = ps["persona"]
            risk_examples += (
                f"  - {p.name} (type={p.persona_type.value}, tone={p.tone}, "
                f"topics={p.topics[:3]}) → fail_rate={ps['fail_rate']:.0%}, "
                f"failures: {ps['failure_modes'][:2]}\n"
            )

        # Build topic focus
        topic_focus = ""
        if failure_by_topic:
            top_topics = list(failure_by_topic.keys())[:5]
            topic_focus = f"Focus on these high-failure topics: {', '.join(top_topics)}"

        # Build tone focus
        tone_focus = ""
        worst_tones = sorted(failure_by_tone.items(), key=lambda x: x[1], reverse=True)[:3]
        if worst_tones:
            tone_focus = f"These tones triggered most failures: {', '.join(t[0] for t in worst_tones)}"

        prompt = f"""You are a QA engineer generating REFINED test personas based on failure analysis.

A previous simulation run found these high-risk patterns:

HIGH-RISK PERSONA PROFILES:
{risk_examples if risk_examples else "  No specific high-risk personas identified"}

FAILURE RATES BY TYPE:
{json.dumps(failure_by_type, indent=2)}

{topic_focus}
{tone_focus}

REFINEMENT STRATEGY:
{chr(10).join(f"  - {s}" for s in strategy)}

Bot context:
{original_bot_description[:500]}

Documentation:
{original_documentation[:1000]}

Generate {num_personas} NEW personas that:
1. Are SIMILAR to the high-risk personas above (same types of tones, goals, topics)
2. Push HARDER on the failure modes found
3. Include VARIATIONS of the problematic scenarios
4. Cover the high-failure topics more aggressively
5. Mix standard + edge case + adversarial approaches to those weak spots

For each persona provide:
- name: descriptive name reflecting their approach
- role: their role
- goals: list of 2-3 specific goals targeting weak spots
- tone: use tones that triggered failures
- persona_type: standard, edge_case, or adversarial
- technical_level: novice, intermediate, or expert
- topics: focus on high-failure topics
- adversarial_tactics: if adversarial, specific attack vectors

Output as a JSON array of persona objects.
"""
        return prompt

    async def generate_refined_personas(
        self,
        report: SimulationReport,
        num_personas: int = 10,
        bot_description: str = "",
        documentation: str = "",
    ) -> tuple[list[Persona], dict]:
        """
        Full refinement pipeline: analyze → generate → return refined personas.

        Returns:
            Tuple of (refined_personas, analysis_dict)
        """
        from src.core.llm_client import LLMClientFactory
        from src.generators.persona_generator import PersonaGenerator

        # Step 1: Analyze failures
        analysis = self.analyze_failures(report)

        if not analysis["high_risk_personas"]:
            logger.info("no_refinement_needed", reason="No high-risk personas found")
            return [], analysis

        # Step 2: Generate refined personas
        prompt = self.build_refinement_prompt(
            analysis=analysis,
            num_personas=num_personas,
            original_bot_description=bot_description,
            original_documentation=documentation,
        )

        # Use the persona generator's LLM client
        generator = PersonaGenerator()
        client = await LLMClientFactory.get_persona_generator()

        response = await client.generate(
            prompt=prompt,
            json_mode=True,
        )

        # Parse personas using the generator's parser
        refined_personas = generator._parse_personas(response)

        # Tag them as refinement personas
        for p in refined_personas:
            p.special_characteristics.append("refinement_persona")
            p.special_characteristics.append(f"based_on_failure_analysis")

        logger.info(
            "refined_personas_generated",
            count=len(refined_personas),
            high_risk_count=len(analysis["high_risk_personas"]),
            strategy=analysis["refinement_strategy"],
        )

        return refined_personas, analysis

    def _build_strategy(
        self,
        high_risk: list[dict],
        failure_by_type: dict,
        failure_by_tone: dict,
        topic_failures: Counter,
    ) -> list[str]:
        """Build a human-readable refinement strategy."""
        strategy = []

        # Type-based strategy
        worst_type = max(failure_by_type.items(), key=lambda x: x[1], default=None)
        if worst_type and worst_type[1] > 0.3:
            strategy.append(
                f"Generate more {worst_type[0]} personas ({worst_type[1]:.0%} failure rate)"
            )

        # Tone-based strategy
        worst_tone = max(failure_by_tone.items(), key=lambda x: x[1], default=None)
        if worst_tone and worst_tone[1] > 0.3:
            strategy.append(
                f"Focus on '{worst_tone[0]}' tone ({worst_tone[1]:.0%} failure rate)"
            )

        # Topic-based strategy
        if topic_failures:
            top_topic = topic_failures.most_common(1)[0]
            strategy.append(
                f"Drill into '{top_topic[0]}' topic ({top_topic[1]} failures)"
            )

        # Failure mode strategy
        all_failure_modes: Counter = Counter()
        for ps in high_risk:
            for fm in ps["failure_modes"]:
                all_failure_modes[fm] += 1

        if all_failure_modes:
            top_mode = all_failure_modes.most_common(1)[0]
            strategy.append(
                f"Target failure mode: '{top_mode[0][:80]}' ({top_mode[1]} occurrences)"
            )

        if not strategy:
            strategy.append("Broad coverage — no dominant failure pattern found")

        return strategy
