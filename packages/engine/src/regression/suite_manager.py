"""
Regression Suite Manager - Save failing conversations as frozen test suites,
replay them against the bot to track if issues got fixed or regressed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.logging import get_logger
from src.models import (
    BotConfig,
    JudgmentLabel,
    SimulationReport,
)

logger = get_logger(__name__)


# ============================================================
# Regression Suite Models
# ============================================================

class RegressionTestCase(BaseModel):
    """A single frozen test case: user messages that triggered a failure."""
    id: str
    source_conversation_id: str
    source_persona_name: str
    source_persona_type: str
    user_messages: list[str]          # The exact user messages to replay
    original_bot_responses: list[str]  # What the bot said originally (for comparison)
    original_score: float
    original_label: str               # PASS/FAIL/WARNING
    failure_modes: list[str]
    tags: list[str] = Field(default_factory=list)  # e.g. ["grounding", "safety"]


class RegressionSuite(BaseModel):
    """A saved regression test suite."""
    id: str
    name: str
    description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_simulation_id: str = ""
    source_simulation_name: str = ""
    test_cases: list[RegressionTestCase] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @property
    def total_cases(self) -> int:
        return len(self.test_cases)


class ReplayResult(BaseModel):
    """Result of replaying a single test case."""
    test_case_id: str
    persona_name: str
    original_label: str
    new_label: str
    original_score: float
    new_score: float
    status: str  # "fixed", "regressed", "unchanged", "new_warning"
    original_failures: list[str]
    new_failures: list[str]
    new_bot_responses: list[str]


class ReplaySummary(BaseModel):
    """Summary of a regression suite replay."""
    suite_name: str
    total_cases: int
    fixed: int = 0          # Was FAIL, now PASS
    regressed: int = 0      # Was PASS, now FAIL
    still_failing: int = 0  # Was FAIL, still FAIL
    still_passing: int = 0  # Was PASS, still PASS
    improved: int = 0       # Score went up
    degraded: int = 0       # Score went down
    avg_score_change: float = 0.0
    results: list[ReplayResult] = Field(default_factory=list)
    replayed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ============================================================
# Regression Suite Manager
# ============================================================

class RegressionSuiteManager:
    """
    Create, save, load, and replay regression test suites.
    """

    def create_from_report(
        self,
        report: SimulationReport,
        name: str = "Auto-generated Regression Suite",
        include_passing: bool = False,
        include_warnings: bool = True,
        max_cases: int = 100,
    ) -> RegressionSuite:
        """
        Extract test cases from a simulation report.

        By default, saves FAIL + WARNING cases. Set include_passing=True
        to also save passing edge cases (important to not regress on).
        """
        suite = RegressionSuite(
            id=f"suite_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            name=name,
            source_simulation_id=report.summary.simulation_id,
            source_simulation_name=report.summary.simulation_name,
            description=(
                f"Auto-extracted from simulation '{report.summary.simulation_name}'. "
                f"Pass rate: {report.summary.pass_rate:.0%}, "
                f"Avg score: {report.summary.average_score:.2f}"
            ),
            metadata={
                "source_pass_rate": report.summary.pass_rate,
                "source_avg_score": report.summary.average_score,
                "extraction_criteria": {
                    "include_passing": include_passing,
                    "include_warnings": include_warnings,
                },
            },
        )

        for jc in report.judged_conversations:
            # Determine if we should include this conversation
            has_failures = any(
                jt.overall_label == JudgmentLabel.FAIL for jt in jc.judged_turns
            )
            has_warnings = any(
                jt.overall_label == JudgmentLabel.WARNING for jt in jc.judged_turns
            )
            is_edge_case = jc.persona.persona_type.value in ("edge_case", "adversarial")

            should_include = has_failures
            if include_warnings and has_warnings:
                should_include = True
            if include_passing and is_edge_case:
                should_include = True

            if not should_include:
                continue

            # Extract user messages and bot responses
            user_msgs = [t.message for t in jc.conversation.turns if t.speaker == "user"]
            bot_msgs = [t.message for t in jc.conversation.turns if t.speaker == "bot"]

            # Determine tags from judge failures
            tags = set()
            for jt in jc.judged_turns:
                for j in jt.judgments:
                    if not j.passed:
                        tags.add(j.judge_name)

            # Determine worst label for this conversation
            labels = [jt.overall_label for jt in jc.judged_turns]
            if JudgmentLabel.FAIL in labels:
                worst_label = "FAIL"
            elif JudgmentLabel.WARNING in labels:
                worst_label = "WARNING"
            else:
                worst_label = "PASS"

            test_case = RegressionTestCase(
                id=f"tc_{jc.conversation.id}",
                source_conversation_id=jc.conversation.id,
                source_persona_name=jc.persona.name,
                source_persona_type=jc.persona.persona_type.value,
                user_messages=user_msgs,
                original_bot_responses=bot_msgs,
                original_score=jc.overall_score,
                original_label=worst_label,
                failure_modes=jc.failure_modes[:5],
                tags=sorted(tags),
            )
            suite.test_cases.append(test_case)

            if len(suite.test_cases) >= max_cases:
                break

        logger.info(
            "regression_suite_created",
            name=name,
            total_cases=suite.total_cases,
            from_conversations=len(report.judged_conversations),
        )
        return suite

    def save_suite(self, suite: RegressionSuite, output_path: str | Path) -> Path:
        """Save a regression suite to a JSON file."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(suite.model_dump(), f, indent=2, default=str)
        logger.info("suite_saved", path=str(path), cases=suite.total_cases)
        return path

    def load_suite(self, input_path: str | Path) -> RegressionSuite:
        """Load a regression suite from a JSON file."""
        path = Path(input_path)
        with open(path) as f:
            data = json.load(f)
        suite = RegressionSuite(**data)
        logger.info("suite_loaded", path=str(path), cases=suite.total_cases)
        return suite

    async def replay_suite(
        self,
        suite: RegressionSuite,
        bot_config: BotConfig,
        documentation: str = "",
        max_turns: int = 15,
    ) -> ReplaySummary:
        """
        Replay all test cases in the suite against the bot.
        Sends the same user messages and judges the new responses.
        """
        from src.judges import JudgeEngine
        from src.judges.grounding_judge import GroundingJudge
        from src.judges.quality_judge import QualityJudge, RelevanceJudge
        from src.judges.safety_judge import SafetyJudge
        from src.models import (
            Conversation,
            Persona,
            PersonaType,
            Turn,
        )
        from src.simulators.conversation_simulator import TargetBotClient

        # Setup bot client and judges
        bot_client = TargetBotClient(bot_config)
        judge_engine = JudgeEngine()
        judge_engine.add_judge(GroundingJudge())
        judge_engine.add_judge(SafetyJudge())
        judge_engine.add_judge(QualityJudge())
        judge_engine.add_judge(RelevanceJudge())
        await judge_engine.initialize_all()

        results: list[ReplayResult] = []
        total_score_change = 0.0

        for tc in suite.test_cases:
            try:
                # Replay the conversation: send the same user messages
                turns: list[Turn] = []
                new_bot_responses: list[str] = []
                conversation_id = f"replay_{tc.id}"

                for user_msg in tc.user_messages[:max_turns]:
                    # Add user turn
                    turns.append(Turn(speaker="user", message=user_msg))

                    # Send to bot
                    try:
                        bot_response = await bot_client.send_message(
                            message=user_msg,
                            conversation_history=turns,
                            conversation_id=conversation_id,
                        )
                        turns.append(Turn(speaker="bot", message=bot_response))
                        new_bot_responses.append(bot_response)
                    except Exception as e:
                        turns.append(Turn(speaker="bot", message=f"[ERROR: {e}]"))
                        new_bot_responses.append(f"[ERROR: {e}]")

                # Judge the conversation
                conversation = Conversation(
                    id=conversation_id,
                    persona_id=tc.id,
                    turns=turns,
                )
                persona = Persona(
                    name=tc.source_persona_name,
                    role="regression_test",
                    goals=["regression test"],
                    persona_type=PersonaType(tc.source_persona_type),
                )

                judged = await judge_engine.judge_conversation(
                    conversation=conversation,
                    persona=persona,
                    documentation=documentation,
                )

                # Determine new label
                new_labels = [jt.overall_label for jt in judged.judged_turns]
                if JudgmentLabel.FAIL in new_labels:
                    new_label = "FAIL"
                elif JudgmentLabel.WARNING in new_labels:
                    new_label = "WARNING"
                else:
                    new_label = "PASS"

                new_score = judged.overall_score
                new_failures = judged.failure_modes

                # Determine status
                if tc.original_label == "FAIL" and new_label == "PASS":
                    status = "fixed"
                elif tc.original_label == "PASS" and new_label == "FAIL":
                    status = "regressed"
                elif tc.original_label == "FAIL" and new_label == "FAIL":
                    status = "still_failing"
                elif tc.original_label == "FAIL" and new_label == "WARNING":
                    status = "improved"
                elif tc.original_label == "WARNING" and new_label == "FAIL":
                    status = "regressed"
                elif tc.original_label == "WARNING" and new_label == "PASS":
                    status = "fixed"
                else:
                    status = "still_passing"

                score_change = new_score - tc.original_score
                total_score_change += score_change

                results.append(ReplayResult(
                    test_case_id=tc.id,
                    persona_name=tc.source_persona_name,
                    original_label=tc.original_label,
                    new_label=new_label,
                    original_score=tc.original_score,
                    new_score=new_score,
                    status=status,
                    original_failures=tc.failure_modes,
                    new_failures=new_failures,
                    new_bot_responses=new_bot_responses,
                ))

            except Exception as e:
                logger.error("replay_error", test_case=tc.id, error=str(e))
                results.append(ReplayResult(
                    test_case_id=tc.id,
                    persona_name=tc.source_persona_name,
                    original_label=tc.original_label,
                    new_label="ERROR",
                    original_score=tc.original_score,
                    new_score=0.0,
                    status="error",
                    original_failures=tc.failure_modes,
                    new_failures=[str(e)],
                    new_bot_responses=[],
                ))

        # Build summary
        summary = ReplaySummary(
            suite_name=suite.name,
            total_cases=len(results),
            fixed=sum(1 for r in results if r.status == "fixed"),
            regressed=sum(1 for r in results if r.status == "regressed"),
            still_failing=sum(1 for r in results if r.status == "still_failing"),
            still_passing=sum(1 for r in results if r.status == "still_passing"),
            improved=sum(1 for r in results if r.status == "improved"),
            degraded=sum(1 for r in results if r.status in ("regressed",)),
            avg_score_change=total_score_change / len(results) if results else 0.0,
            results=results,
        )

        logger.info(
            "replay_complete",
            total=summary.total_cases,
            fixed=summary.fixed,
            regressed=summary.regressed,
            still_failing=summary.still_failing,
        )

        return summary

    def save_replay_summary(
        self, summary: ReplaySummary, output_path: str | Path
    ) -> Path:
        """Save a replay summary to JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(summary.model_dump(), f, indent=2, default=str)
        logger.info("replay_summary_saved", path=str(path))
        return path
