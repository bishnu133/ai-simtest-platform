"""
Replay Evaluator — runs the full judge pipeline on imported conversations
and produces a SimulationReport compatible with all existing exports.

Supports three modes:
  - EVALUATE: Judge existing bot responses as-is
  - RETEST: Send user messages to a bot, get NEW responses, judge those
            NOTE: This is sequential re-sending, not full context replay (#4)
  - HYBRID: Evaluate existing + generate variations via LLM + test variations

Review fixes applied:
  #1 — Source-to-conversation mapping uses conv_id dict, not list index
  #4 — RETEST mode clearly documented as sequential re-sending
  #5 — Hardened JSON parsing with retry + schema validation
  #9 — Configurable judge selection via config.judges list
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from src.core.logging import get_logger
from src.models import (
    BotConfig,
    Conversation,
    FailurePattern,
    JudgedConversation,
    JudgmentLabel,
    Persona,
    PersonaType,
    ReportSummary,
    Severity,
    SimulationReport,
    Turn,
)
from src.replay.models import (
    ConversationEvalResult,
    ConversationSource,
    LoadSummary,
    PIIReport,
    ReplayConfig,
    ReplayMode,
    ReplayResult,
)

logger = get_logger(__name__)


class ReplayEvaluator:
    """
    Evaluates imported conversations using the AI SimTest judge pipeline.

    Produces a SimulationReport that plugs into all existing exports
    (HTML report, JSONL, CSV, comparison engine, policy, workflow).
    """

    async def evaluate(
        self,
        conversations: list[Conversation],
        personas: dict[str, Persona],
        sources: list[ConversationSource],
        config: ReplayConfig,
        load_summary: LoadSummary | None = None,
        pii_report: PIIReport | None = None,
    ) -> tuple[SimulationReport, ReplayResult]:
        """
        Run the full evaluation pipeline on imported conversations.

        Returns:
            - SimulationReport (compatible with all existing exports)
            - ReplayResult (replay-specific metadata and gate results)
        """
        start_time = time.time()

        # Build source lookup by conversation ID (#1)
        source_map: dict[str, ConversationSource] = {}
        for conv, src in zip(conversations, sources):
            source_map[conv.id] = src

        # Setup judges (configurable #9)
        judge_engine = await self._setup_judges(config)

        # Decide evaluation mode
        if config.mode == ReplayMode.RETEST:
            judged = await self._retest_conversations(
                conversations, personas, config, judge_engine
            )
        elif config.mode == ReplayMode.HYBRID:
            judged = await self._hybrid_evaluation(
                conversations, personas, config, judge_engine, source_map
            )
        else:
            judged = await self._evaluate_conversations(
                conversations, personas, config, judge_engine
            )

        # Build SimulationReport
        report = self._build_report(judged, config, start_time)

        # Build ReplayResult with gate checks (#1 — uses source_map)
        replay_result = self._build_replay_result(
            report, judged, source_map, config, start_time,
            load_summary=load_summary,
            pii_report=pii_report,
        )

        return report, replay_result

    async def _setup_judges(self, config: ReplayConfig):
        """Initialize the judge engine. Respects config.judges for selection (#9)."""
        from src.judges import JudgeEngine
        from src.judges.grounding_judge import GroundingJudge
        from src.judges.quality_judge import QualityJudge, RelevanceJudge
        from src.judges.safety_judge import SafetyJudge

        engine = JudgeEngine(
            pass_threshold=config.pass_threshold,
            warn_threshold=config.warn_threshold,
        )

        # All available judges
        available = {
            "grounding": lambda: GroundingJudge(),
            "safety": lambda: SafetyJudge(),
            "quality": lambda: QualityJudge(),
            "relevance": lambda: RelevanceJudge(),
        }

        # Filter by config (#9)
        selected = config.judges if config.judges else list(available.keys())

        for name in selected:
            factory = available.get(name.strip().lower())
            if factory:
                engine.add_judge(factory())
            else:
                logger.warning("unknown_judge", name=name, available=list(available.keys()))

        await engine.initialize_all()
        return engine

    async def _evaluate_conversations(
        self,
        conversations: list[Conversation],
        personas: dict[str, Persona],
        config: ReplayConfig,
        judge_engine,
    ) -> list[JudgedConversation]:
        """Mode: EVALUATE — judge existing bot responses as-is."""
        return await judge_engine.judge_all_conversations(
            conversations=conversations,
            personas=personas,
            documentation=config.documentation,
            max_parallel=5,
        )

    async def _retest_conversations(
        self,
        conversations: list[Conversation],
        personas: dict[str, Persona],
        config: ReplayConfig,
        judge_engine,
    ) -> list[JudgedConversation]:
        """Mode: RETEST — send user messages to bot, get new responses, judge.

        IMPORTANT (#4): This mode extracts user messages and sends them
        sequentially to the bot. Each user message includes the conversation
        history built up so far (user+bot turns), so the bot has context
        from earlier in the replayed conversation. However, it does NOT
        recreate the original bot responses as context — the bot sees its
        OWN new responses as the conversation progresses.

        This means RETEST answers: "How would the bot respond TODAY to
        the same user inputs?" — not "What would happen if we perfectly
        reconstructed the original dialogue?"
        """
        if not config.bot_endpoint:
            raise ValueError("RETEST mode requires --bot-endpoint")

        from src.simulators.conversation_simulator import TargetBotClient

        bot_config = BotConfig(
            api_endpoint=config.bot_endpoint,
            api_key=config.bot_api_key,
            request_format=config.bot_format,
        )
        bot_client = TargetBotClient(bot_config)

        retested_conversations = []
        for conv in conversations:
            new_conv = await self._retest_single(conv, bot_client)
            retested_conversations.append(new_conv)

        return await judge_engine.judge_all_conversations(
            conversations=retested_conversations,
            personas=personas,
            documentation=config.documentation,
            max_parallel=5,
        )

    async def _retest_single(
        self,
        original: Conversation,
        bot_client,
    ) -> Conversation:
        """Send user messages from original conversation to bot.

        Builds conversation_history incrementally so the bot sees
        the full context of user+bot turns so far (#4).
        """
        new_turns: list[Turn] = []
        user_messages = [t for t in original.turns if t.speaker == "user"]

        for user_turn in user_messages:
            new_turns.append(Turn(speaker="user", message=user_turn.message))
            try:
                response = await bot_client.send_message(
                    message=user_turn.message,
                    conversation_history=new_turns,  # full history for context
                    conversation_id=f"retest_{original.id}",
                )
                if isinstance(response, tuple):
                    response = response[0]
                new_turns.append(Turn(speaker="bot", message=str(response)))
            except Exception as e:
                new_turns.append(Turn(speaker="bot", message=f"[ERROR: {e}]"))

        return Conversation(
            id=f"retest_{original.id}",
            persona_id=original.persona_id,
            turns=new_turns,
            metadata={
                **original.metadata,
                "retest_mode": True,
                "original_conversation_id": original.id,
            },
        )

    async def _hybrid_evaluation(
        self,
        conversations: list[Conversation],
        personas: dict[str, Persona],
        config: ReplayConfig,
        judge_engine,
        source_map: dict[str, ConversationSource],
    ) -> list[JudgedConversation]:
        """Mode: HYBRID — evaluate existing + generate variations + test."""
        original_judged = await self._evaluate_conversations(
            conversations, personas, config, judge_engine
        )

        if not config.bot_endpoint:
            logger.warning("hybrid_no_bot", hint="HYBRID mode without bot endpoint only evaluates originals")
            return original_judged

        from src.core.llm_client import LLMClientFactory
        try:
            llm_client = LLMClientFactory.persona_generator()
        except Exception:
            logger.warning("hybrid_no_llm", hint="Could not create LLM client for variation generation")
            return original_judged

        from src.simulators.conversation_simulator import TargetBotClient
        bot_config = BotConfig(
            api_endpoint=config.bot_endpoint,
            api_key=config.bot_api_key,
            request_format=config.bot_format,
        )
        bot_client = TargetBotClient(bot_config)

        variation_judged = []
        for conv in conversations[:10]:
            variations = await self._generate_variations(conv, llm_client, config.num_variations)
            for var_conv in variations:
                retested = await self._retest_single(var_conv, bot_client)
                persona = personas.get(conv.persona_id, Persona(
                    name="Variation User", role="real_user", goals=["variation_test"],
                ))
                # Register variation source in source_map (#1)
                if conv.id in source_map:
                    source_map[retested.id] = ConversationSource(
                        file_path=source_map[conv.id].file_path,
                        original_id=f"variation_of_{conv.id}",
                        source_metadata={"variation_of": conv.id},
                    )
                judged_var = await judge_engine.judge_conversation(
                    retested, persona, config.documentation,
                )
                variation_judged.append(judged_var)

        return original_judged + variation_judged

    async def _generate_variations(
        self,
        conversation: Conversation,
        llm_client,
        num_variations: int,
    ) -> list[Conversation]:
        """Generate N variation conversations from a real conversation.

        Fix #5: Hardened JSON parsing with retry and schema validation.
        """
        user_messages = [t.message for t in conversation.turns if t.speaker == "user"]
        if not user_messages:
            return []

        prompt = (
            f"Given these real user messages from a chat conversation:\n"
            f"{chr(10).join(f'  - {m}' for m in user_messages)}\n\n"
            f"Generate {num_variations} variations of this conversation. "
            f"Each variation should convey the same intent but with different "
            f"wording, style, or common user mistakes (typos, incomplete sentences, "
            f"slang).\n\n"
            f"RESPOND WITH ONLY a JSON array of arrays. No markdown, no commentary.\n"
            f"Example: [[\"msg1\", \"msg2\"], [\"msg1\", \"msg2\"]]"
        )

        # Retry up to 2 times (#5)
        for attempt in range(2):
            try:
                response = await llm_client.generate(prompt=prompt)
                variations_data = self._extract_json_array(response)

                if not isinstance(variations_data, list):
                    logger.warning("variation_not_array", attempt=attempt)
                    continue

                results = []
                for idx, var_messages in enumerate(variations_data[:num_variations]):
                    # Schema validation (#5): must be list of strings
                    if not isinstance(var_messages, list):
                        continue
                    if not all(isinstance(m, str) for m in var_messages):
                        continue
                    if not var_messages:
                        continue

                    turns = [Turn(speaker="user", message=msg) for msg in var_messages]
                    results.append(Conversation(
                        id=f"var_{conversation.id}_{idx}",
                        persona_id=conversation.persona_id,
                        turns=turns,
                        metadata={
                            "variation_of": conversation.id,
                            "variation_index": idx,
                        },
                    ))

                if results:
                    return results

            except Exception as e:
                logger.warning("variation_generation_failed", attempt=attempt, error=str(e))

        return []

    def _extract_json_array(self, raw: str) -> Any:
        """Extract a JSON array from an LLM response, handling common issues (#5)."""
        cleaned = raw.strip()

        # Strip markdown code fences
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        # Try direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try to find array boundaries
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract JSON array from response ({len(raw)} chars)")

    def _build_report(
        self,
        judged: list[JudgedConversation],
        config: ReplayConfig,
        start_time: float,
    ) -> SimulationReport:
        """Build a SimulationReport from judged conversations."""
        total_turns = sum(len(jc.judged_turns) for jc in judged)
        pass_count = sum(
            1 for jc in judged for jt in jc.judged_turns
            if jt.overall_label == JudgmentLabel.PASS
        )
        total_judged = sum(len(jc.judged_turns) for jc in judged)
        pass_rate = pass_count / total_judged if total_judged else 0.0
        avg_score = sum(jc.overall_score for jc in judged) / len(judged) if judged else 0.0

        critical_failures = sum(
            1 for jc in judged for jt in jc.judged_turns
            if jt.overall_label == JudgmentLabel.FAIL
            and any(j.severity == Severity.CRITICAL for j in jt.judgments if not j.passed)
        )
        warnings = sum(
            1 for jc in judged for jt in jc.judged_turns
            if jt.overall_label == JudgmentLabel.WARNING
        )

        # Score by judge
        score_by_judge: dict[str, list[float]] = {}
        for jc in judged:
            for jt in jc.judged_turns:
                for j in jt.judgments:
                    score_by_judge.setdefault(j.judge_name, []).append(j.score)
        avg_by_judge = {
            name: sum(scores) / len(scores)
            for name, scores in score_by_judge.items()
            if scores
        }

        # Failure patterns
        failure_msgs: list[str] = []
        for jc in judged:
            failure_msgs.extend(jc.failure_modes)

        pattern_counts = Counter(failure_msgs)
        failure_patterns = [
            FailurePattern(
                pattern_name=msg[:60],
                description=msg,
                frequency=count,
                severity=Severity.HIGH if count > 2 else Severity.MEDIUM,
            )
            for msg, count in pattern_counts.most_common(10)
            if count >= 1
        ]

        execution_time = time.time() - start_time

        summary = ReportSummary(
            simulation_id=f"replay_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            simulation_name=f"Conversation Replay ({config.mode.value})",
            total_personas=len(set(jc.persona.id for jc in judged)),
            total_conversations=len(judged),
            total_turns=total_turns,
            pass_rate=pass_rate,
            average_score=avg_score,
            critical_failures=critical_failures,
            warnings=warnings,
            execution_time_seconds=round(execution_time, 1),
        )

        recommendations = []
        if avg_by_judge.get("safety", 1.0) < 0.9:
            recommendations.append("Safety scores below 90% — review bot safety guardrails")
        if avg_by_judge.get("grounding", 1.0) < 0.5:
            recommendations.append("Low grounding — bot may be hallucinating")
        if avg_by_judge.get("quality", 1.0) < 0.6:
            recommendations.append("Quality below 60% — review helpfulness and clarity")
        if avg_by_judge.get("relevance", 1.0) < 0.5:
            recommendations.append("Low relevance — bot giving off-topic responses")

        return SimulationReport(
            summary=summary,
            judged_conversations=judged,
            failure_patterns=failure_patterns,
            score_by_judge=avg_by_judge,
            recommendations=recommendations,
        )

    def _build_replay_result(
        self,
        report: SimulationReport,
        judged: list[JudgedConversation],
        source_map: dict[str, ConversationSource],
        config: ReplayConfig,
        start_time: float,
        load_summary: LoadSummary | None = None,
        pii_report: PIIReport | None = None,
    ) -> ReplayResult:
        """Build replay-specific result with CI/CD gate checks.

        Fix #1: Uses source_map (conversation_id → source) instead of
        list index, which breaks in HYBRID mode with generated variations.
        """
        conv_results = []
        for jc in judged:
            # Resolve source by conversation ID (#1)
            source = source_map.get(
                jc.conversation.id,
                ConversationSource(
                    file_path="generated",
                    original_id=jc.conversation.id,
                    source_metadata={"generated": True},
                ),
            )

            judge_scores: dict[str, list[float]] = {}
            for jt in jc.judged_turns:
                for j in jt.judgments:
                    judge_scores.setdefault(j.judge_name, []).append(j.score)

            conv_results.append(ConversationEvalResult(
                source=source,
                conversation_id=jc.conversation.id,
                overall_score=jc.overall_score,
                pass_rate=jc.pass_rate,
                total_turns=len(jc.judged_turns),
                issues=jc.failure_modes,
                judge_scores={
                    name: sum(scores) / len(scores)
                    for name, scores in judge_scores.items()
                    if scores
                },
                pii_masked=len(source.pii_detections) > 0,
                pii_findings=[d.get("entity_type", "unknown") for d in source.pii_detections],
                quality=source.source_metadata.get("quality", "complete"),
                quality_warnings=source.source_metadata.get("quality_warnings", []),
            ))

        # PII summary (from sources)
        pii_summary: dict[str, int] = {}
        for src in source_map.values():
            for d in src.pii_detections:
                entity = d.get("entity_type", "unknown")
                pii_summary[entity] = pii_summary.get(entity, 0) + 1

        # CI/CD gate checks (#R2: respect minimum sample size)
        gate_results: dict[str, bool] = {}
        gate_passed = True
        total_convs = len(judged)
        total_turns = sum(len(jc.judged_turns) for jc in judged)

        for judge_name, threshold in config.fail_thresholds.items():
            actual = report.score_by_judge.get(judge_name, 0.0)

            # Minimum sample protection (#R2)
            if total_convs < config.min_conversations_for_gate:
                gate_results[f"{judge_name}>={threshold} (skipped: {total_convs}<{config.min_conversations_for_gate} convos)"] = True
                logger.warning(
                    "gate_skipped_insufficient_sample",
                    judge=judge_name, threshold=threshold,
                    conversations=total_convs, min_required=config.min_conversations_for_gate,
                )
                continue

            if total_turns < config.min_turns_for_gate:
                gate_results[f"{judge_name}>={threshold} (skipped: {total_turns}<{config.min_turns_for_gate} turns)"] = True
                logger.warning(
                    "gate_skipped_insufficient_turns",
                    judge=judge_name, threshold=threshold,
                    turns=total_turns, min_required=config.min_turns_for_gate,
                )
                continue

            passed = actual >= threshold
            gate_results[f"{judge_name}>={threshold}"] = passed
            if not passed:
                gate_passed = False

        execution_time = time.time() - start_time

        return ReplayResult(
            config=config,
            total_conversations=len(judged),
            total_turns_evaluated=sum(len(jc.judged_turns) for jc in judged),
            overall_pass_rate=report.summary.pass_rate,
            overall_avg_score=report.summary.average_score,
            score_by_judge=report.score_by_judge,
            conversation_results=conv_results,
            pii_summary=pii_summary,
            pii_report=pii_report or PIIReport(),
            load_summary=load_summary or LoadSummary(),
            gate_results=gate_results,
            gate_passed=gate_passed,
            completed_at=datetime.now(timezone.utc).isoformat(),
            execution_time_seconds=round(execution_time, 1),
        )