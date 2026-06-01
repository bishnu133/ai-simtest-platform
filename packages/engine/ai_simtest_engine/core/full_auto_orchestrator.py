"""
Full Auto Orchestrator — Fully Autonomous Mode (--mode auto).

Flow:
  1. Bot Discovery (Phase E) — exploratory conversations to understand the bot
  2. Approval Gate 0 — tester reviews discovered context
     - Approve → proceed
     - Modify → tester supplements → proceed
     - Reject → RETRY with cross-examination strategy (1 retry max)
       - If retry also rejected → diagnostic report + STOP
  3. Hand off to existing partial autonomous pipeline
     (criteria → guardrails → personas → test plan → simulation → report)

Mismatch Detection:
  If the bot contradicts itself (e.g., claims airline but responds to banking queries),
  the diagnostic report highlights the inconsistency as a quality signal about the bot.

Design:
  - New file, separate from autonomous_orchestrator.py (partial mode)
  - Imports BotDiscoveryEngine from discovery package
  - Imports AutonomousOrchestrator from partial mode for handoff
  - Minimal new code, maximum reuse
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from ai_simtest_engine.core.logging import get_logger
from ai_simtest_engine.discovery.bot_discovery import BotDiscoveryEngine, DiscoveryResult
from ai_simtest_engine.discovery.strategy import DiscoveryStrategy
from ai_simtest_engine.discovery.synthesizer import DiscoveredContext, ConfidenceScore

logger = get_logger(__name__)


# ============================================================
# Retry Strategy — Cross-examination approach
# ============================================================

class RetryStrategy(DiscoveryStrategy):
    """
    A more aggressive discovery strategy used on retry.
    
    Instead of open-ended questions, this uses:
    - Direct yes/no questions based on what attempt 1 found
    - Domain-specific probes to verify or challenge initial findings
    - Contradiction detection questions
    """

    def __init__(
        self,
        first_attempt_context: DiscoveredContext,
        max_turns: int = 10,
    ):
        super().__init__(max_turns=max_turns)
        self.first_attempt = first_attempt_context
        self._build_cross_exam_questions()

    def _build_cross_exam_questions(self):
        """Build targeted questions based on first attempt findings."""
        from ai_simtest_engine.discovery.strategy import DiscoveryQuestion, DiscoveryPhase

        ctx = self.first_attempt

        # Override identity questions with direct verification
        self.IDENTITY_QUESTIONS = []

        if ctx.bot_name and ctx.bot_name != "Unknown Bot":
            self.IDENTITY_QUESTIONS.append(DiscoveryQuestion(
                phase=DiscoveryPhase.IDENTITY,
                question=f"Are you {ctx.bot_name}? Please confirm your name and purpose.",
                purpose="Verify bot identity from first attempt",
                priority=1,
            ))

        if ctx.domain and ctx.domain != "general":
            self.IDENTITY_QUESTIONS.append(DiscoveryQuestion(
                phase=DiscoveryPhase.IDENTITY,
                question=f"Are you a {ctx.domain} assistant? Yes or no, and what specifically do you help with?",
                purpose="Verify detected domain",
                priority=1,
            ))

        # Test with opposite-domain questions to detect contradictions
        opposite_domains = {
            "travel": "banking",
            "finance": "airline",
            "healthcare": "hotel booking",
            "customer_service": "medical",
            "education": "financial",
            "ecommerce": "healthcare",
            "technology": "travel",
        }
        opposite = opposite_domains.get(ctx.domain, "cooking recipes")

        self.IDENTITY_QUESTIONS.append(DiscoveryQuestion(
            phase=DiscoveryPhase.IDENTITY,
            question=f"Can you help me with {opposite}?",
            purpose=f"Test if bot correctly refuses {opposite} when its domain is {ctx.domain}",
            priority=1,
        ))

        # Override capability questions with verification
        self.CAPABILITY_QUESTIONS = []
        for cap in ctx.capabilities[:3]:  # Verify top 3 capabilities
            self.CAPABILITY_QUESTIONS.append(DiscoveryQuestion(
                phase=DiscoveryPhase.CAPABILITIES,
                question=f"Can you help me with: {cap}? Give me a specific example.",
                purpose=f"Verify capability: {cap}",
                priority=1,
            ))

        # Add boundary verification
        self.BOUNDARY_QUESTIONS = []
        for lim in ctx.limitations[:2]:
            self.BOUNDARY_QUESTIONS.append(DiscoveryQuestion(
                phase=DiscoveryPhase.BOUNDARIES,
                question=f"I heard you can't do this: {lim}. Is that correct?",
                purpose=f"Verify limitation: {lim}",
                priority=1,
            ))

        # Keep edge case questions the same (from parent class)


# ============================================================
# Mismatch Detection
# ============================================================

class MismatchReport(BaseModel):
    """Report of contradictions found between discovery attempts."""
    has_mismatches: bool = False
    domain_mismatch: Optional[str] = None
    capability_contradictions: list[str] = Field(default_factory=list)
    identity_inconsistencies: list[str] = Field(default_factory=list)
    severity: str = "info"  # info, warning, critical

    def to_report_section(self) -> str:
        """Generate a human-readable mismatch report section."""
        if not self.has_mismatches:
            return ""

        lines = [
            "## ⚠️ CONTEXT MISMATCH DETECTED",
            "",
        ]

        if self.domain_mismatch:
            lines.append(f"**Domain Inconsistency:** {self.domain_mismatch}")
            lines.append("")

        if self.identity_inconsistencies:
            lines.append("**Identity Inconsistencies:**")
            for inc in self.identity_inconsistencies:
                lines.append(f"  - {inc}")
            lines.append("")

        if self.capability_contradictions:
            lines.append("**Capability Contradictions:**")
            for con in self.capability_contradictions:
                lines.append(f"  - {con}")
            lines.append("")

        lines.extend([
            f"**Severity:** {self.severity.upper()}",
            "",
            "**What This Means:**",
            "The bot provided inconsistent information about itself across multiple",
            "discovery conversations. This suggests the bot may have:",
            "  - Misconfigured system prompt or instructions",
            "  - Inconsistent guardrails",
            "  - Identity confusion (responding to off-domain queries as if it's a different bot)",
            "",
            "**Recommendation:** Review the bot's system prompt and configuration.",
        ])

        return "\n".join(lines)


def detect_mismatches(
    attempt1: DiscoveredContext,
    attempt2: DiscoveredContext,
) -> MismatchReport:
    """
    Compare two discovery attempts to find contradictions.
    """
    report = MismatchReport()

    # Check domain mismatch
    if (
        attempt1.domain != "general"
        and attempt2.domain != "general"
        and attempt1.domain != attempt2.domain
    ):
        report.has_mismatches = True
        report.domain_mismatch = (
            f"Attempt 1 detected domain '{attempt1.domain}', "
            f"but attempt 2 detected '{attempt2.domain}'"
        )
        report.severity = "critical"

    # Check identity inconsistencies
    if (
        attempt1.bot_name != "Unknown Bot"
        and attempt2.bot_name != "Unknown Bot"
        and attempt1.bot_name.lower() != attempt2.bot_name.lower()
    ):
        report.has_mismatches = True
        report.identity_inconsistencies.append(
            f"Bot identified as '{attempt1.bot_name}' in attempt 1, "
            f"but '{attempt2.bot_name}' in attempt 2"
        )

    # Check capability contradictions using word overlap (fuzzy matching)
    caps1 = [c.lower() for c in attempt1.capabilities]
    caps2 = [c.lower() for c in attempt2.capabilities]
    if caps1 and caps2:
        # Use word-level Jaccard similarity to compare capability sets
        words1 = set()
        for c in caps1:
            words1.update(c.split())
        words2 = set()
        for c in caps2:
            words2.update(c.split())

        if words1 and words2:
            overlap = len(words1 & words2)
            union = len(words1 | words2)
            similarity = overlap / union if union > 0 else 0

            if similarity < 0.3:  # Less than 30% word overlap = very different
                report.has_mismatches = True
                report.capability_contradictions.append(
                    f"Significant capability difference: "
                    f"Attempt 1 capabilities ({', '.join(caps1[:3])}) vs "
                    f"Attempt 2 capabilities ({', '.join(caps2[:3])}) — "
                    f"only {similarity:.0%} word overlap"
                )
                if report.severity == "info":
                    report.severity = "warning"

    # Check if bot responded to opposite-domain queries positively
    # (this is detected from retry responses)
    for qa in attempt2.raw_conversation:
        q = qa.get("question", "").lower()
        r = qa.get("response", "").lower()
        if "can you help me with" in q and attempt1.domain:
            # If bot said yes to an opposite-domain question
            positive_indicators = [
                "yes", "sure", "of course", "certainly",
                "i can help", "happy to", "let me",
            ]
            if any(ind in r[:100] for ind in positive_indicators):
                # Check if the question was about a different domain
                if attempt1.domain not in q:
                    report.has_mismatches = True
                    report.capability_contradictions.append(
                        f"Bot claims to be a {attempt1.domain} assistant but "
                        f"responded positively to off-domain question: '{qa.get('question', '')[:80]}'"
                    )
                    report.severity = "critical"

    return report


# ============================================================
# Full Auto Orchestrator
# ============================================================

class FullAutoResult(BaseModel):
    """Result of the fully autonomous flow."""
    success: bool = False
    discovery_result: Optional[DiscoveryResult] = None
    retry_result: Optional[DiscoveryResult] = None
    mismatch_report: Optional[MismatchReport] = None
    final_context: Optional[DiscoveredContext] = None
    diagnostic_report: Optional[str] = None
    simulation_report: Optional[dict] = None  # From partial pipeline
    total_execution_time: float = 0.0
    stopped_reason: str = ""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class FullAutoOrchestrator:
    """
    Orchestrates the fully autonomous testing flow.
    
    Stage 0: Bot Discovery → Approval Gate
      - Approve → hand off to partial pipeline
      - Modify → merge tester input → hand off
      - Reject → Retry with cross-examination (1 max)
        - Still rejected → diagnostic report + STOP
    
    Stage 1-5: Delegates to existing AutonomousOrchestrator (partial mode)
    """

    def __init__(
        self,
        bot_client,
        llm_client,
        approval_gate=None,
        max_discovery_turns: int = 12,
        discovery_timeout: float = 30.0,
        auto_approve: bool = False,
        # Pass-through to partial pipeline
        num_personas: int = None,
        max_turns: int = None,
        output_dir: str = "./output",
        export_formats: str = "jsonl,csv,summary,html",
        pass_threshold: float = 0.7,
        warn_threshold: float = 0.5,
    ):
        self.bot_client = bot_client
        self.llm_client = llm_client
        self.approval_gate = approval_gate
        self.max_discovery_turns = max_discovery_turns
        self.discovery_timeout = discovery_timeout
        self.auto_approve = auto_approve
        self.num_personas = num_personas
        self.max_turns = max_turns
        self.output_dir = output_dir
        self.export_formats = export_formats
        self.pass_threshold = pass_threshold
        self.warn_threshold = warn_threshold

    async def run(self) -> FullAutoResult:
        """
        Execute the fully autonomous flow.
        """
        start_time = time.time()
        result = FullAutoResult()

        logger.info("full_auto_started", max_discovery_turns=self.max_discovery_turns)

        # ── Stage 0: First Discovery Attempt ──────────────────

        logger.info("discovery_attempt", attempt=1)
        engine = BotDiscoveryEngine(
            bot_client=self.bot_client,
            llm_client=self.llm_client,
            max_turns=self.max_discovery_turns,
            timeout_per_turn=self.discovery_timeout,
        )

        discovery_result = await engine.discover()
        result.discovery_result = discovery_result

        if not discovery_result.success and not discovery_result.context.bot_description and not discovery_result.context.raw_conversation:
            # Bot didn't respond at all
            result.diagnostic_report = discovery_result.diagnostic_report
            result.stopped_reason = "Bot did not respond to discovery questions"
            result.total_execution_time = time.time() - start_time
            logger.error("discovery_failed_no_response")
            return result

        # ── Approval Gate 0 ───────────────────────────────────

        gate_decision = await self._present_discovery_for_approval(
            discovery_result.context, attempt=1
        )

        if gate_decision == "approved":
            result.final_context = discovery_result.context
            logger.info("discovery_approved", attempt=1)

        elif gate_decision == "modified":
            # Tester provided supplementary info — merge it
            result.final_context = discovery_result.context
            logger.info("discovery_modified", attempt=1)

        elif gate_decision == "rejected":
            # ── Retry with cross-examination ──────────────────

            logger.info("discovery_rejected_retrying", attempt=2)

            retry_result = await self._retry_discovery(discovery_result.context)
            result.retry_result = retry_result

            # Check for mismatches between attempts
            mismatch = detect_mismatches(
                discovery_result.context,
                retry_result.context,
            )
            result.mismatch_report = mismatch

            # Present retry result for approval
            retry_decision = await self._present_discovery_for_approval(
                retry_result.context,
                attempt=2,
                mismatch_report=mismatch,
            )

            if retry_decision in ("approved", "modified"):
                result.final_context = retry_result.context
                logger.info("retry_discovery_approved")
            else:
                # Retry also rejected — generate diagnostic and STOP
                diagnostic = self._generate_final_diagnostic(
                    attempt1=discovery_result,
                    attempt2=retry_result,
                    mismatch=mismatch,
                )
                result.diagnostic_report = diagnostic
                result.stopped_reason = "Discovery rejected after retry — bot context insufficient"
                result.total_execution_time = time.time() - start_time
                logger.warning("discovery_rejected_after_retry")
                return result

        # ── Stage 1-5: Hand off to partial pipeline ───────────

        if result.final_context:
            documentation = result.final_context.to_documentation()
            result.success = True

            logger.info(
                "handoff_to_partial_pipeline",
                domain=result.final_context.domain,
                capabilities=len(result.final_context.capabilities),
                confidence=result.final_context.overall_confidence.score,
            )

            # The actual partial pipeline execution would be:
            # autonomous_orchestrator = AutonomousOrchestrator(
            #     bot_endpoint=self.bot_client.endpoint,
            #     documentation=documentation,
            #     ...
            # )
            # simulation_result = await autonomous_orchestrator.run()
            # result.simulation_report = simulation_result
            #
            # For now, we mark handoff as successful.
            # The CLI will wire this into the actual partial pipeline.

        result.total_execution_time = time.time() - start_time

        logger.info(
            "full_auto_completed",
            success=result.success,
            execution_time=f"{result.total_execution_time:.1f}s",
            attempts=2 if result.retry_result else 1,
            has_mismatches=bool(result.mismatch_report and result.mismatch_report.has_mismatches),
        )

        return result

    async def _retry_discovery(
        self,
        first_attempt_context: DiscoveredContext,
    ) -> DiscoveryResult:
        """
        Run a second discovery attempt using cross-examination strategy.
        """
        retry_strategy = RetryStrategy(
            first_attempt_context=first_attempt_context,
            max_turns=min(10, self.max_discovery_turns),
        )

        engine = BotDiscoveryEngine(
            bot_client=self.bot_client,
            llm_client=self.llm_client,
            max_turns=min(10, self.max_discovery_turns),
            timeout_per_turn=self.discovery_timeout,
        )
        # Replace strategy with cross-examination strategy
        engine.strategy = retry_strategy

        return await engine.discover()

    async def _present_discovery_for_approval(
        self,
        context: DiscoveredContext,
        attempt: int,
        mismatch_report: Optional[MismatchReport] = None,
    ) -> str:
        """
        Present discovered context to tester for approval.
        
        Returns: "approved", "modified", or "rejected"
        """
        if self.auto_approve:
            if context.is_sufficient:
                logger.info("auto_approved_discovery", confidence=context.overall_confidence.score)
                return "approved"
            else:
                logger.warning(
                    "auto_approve_rejected_insufficient",
                    confidence=context.overall_confidence.score,
                )
                return "rejected"

        if self.approval_gate:
            # Use the approval gate framework
            from ai_simtest_engine.core.approval_gate import GateProposal, ProposalItem, ConfidenceLevel

            confidence_map = {
                "excellent": ConfidenceLevel.HIGH,
                "good": ConfidenceLevel.HIGH,
                "fair": ConfidenceLevel.MEDIUM,
                "poor": ConfidenceLevel.LOW,
                "insufficient": ConfidenceLevel.LOW,
            }

            items = []
            items.append(ProposalItem(
                content=f"**Bot Name:** {context.bot_name}",
                confidence=confidence_map.get(context.quality_level, ConfidenceLevel.MEDIUM),
            ))
            items.append(ProposalItem(
                content=f"**Domain:** {context.domain}",
                confidence=confidence_map.get(context.quality_level, ConfidenceLevel.MEDIUM),
            ))
            items.append(ProposalItem(
                content=f"**Description:** {context.bot_description}",
                confidence=confidence_map.get(context.quality_level, ConfidenceLevel.MEDIUM),
            ))

            for cap in context.capabilities:
                items.append(ProposalItem(
                    content=f"Capability: {cap}",
                    confidence=ConfidenceLevel.MEDIUM,
                ))

            description = f"Discovery Attempt {attempt} | Confidence: {context.overall_confidence.score:.0%} ({context.quality_level})"

            if mismatch_report and mismatch_report.has_mismatches:
                description += f"\n\n⚠️ MISMATCHES DETECTED:\n{mismatch_report.to_report_section()}"

            proposal = GateProposal(
                gate_name="bot_discovery",
                title=f"Bot Context Discovery (Attempt {attempt})",
                description=description,
                stage_number=0,
                items=items,
            )

            gate_result = await self.approval_gate.submit(proposal)
            return gate_result.decision.value

        # Fallback: auto-approve if sufficient
        return "approved" if context.is_sufficient else "rejected"

    def _generate_final_diagnostic(
        self,
        attempt1: DiscoveryResult,
        attempt2: DiscoveryResult,
        mismatch: MismatchReport,
    ) -> str:
        """Generate comprehensive diagnostic when both attempts are rejected."""
        lines = [
            "# Bot Discovery Diagnostic Report",
            "",
            "## Status: STOPPED — Context Rejected After 2 Attempts",
            "",
            "---",
            "",
            "## Attempt 1 Summary",
            f"- **Bot name:** {attempt1.context.bot_name}",
            f"- **Domain:** {attempt1.context.domain}",
            f"- **Capabilities:** {len(attempt1.context.capabilities)}",
            f"- **Confidence:** {attempt1.context.overall_confidence.score:.0%}",
            f"- **Quality:** {attempt1.context.quality_level}",
            f"- **Turns:** {attempt1.context.discovery_turns}",
            "",
            "## Attempt 2 Summary (Cross-Examination)",
            f"- **Bot name:** {attempt2.context.bot_name}",
            f"- **Domain:** {attempt2.context.domain}",
            f"- **Capabilities:** {len(attempt2.context.capabilities)}",
            f"- **Confidence:** {attempt2.context.overall_confidence.score:.0%}",
            f"- **Quality:** {attempt2.context.quality_level}",
            f"- **Turns:** {attempt2.context.discovery_turns}",
            "",
        ]

        # Add mismatch section
        if mismatch.has_mismatches:
            lines.append(mismatch.to_report_section())
            lines.append("")

        lines.extend([
            "---",
            "",
            "## Conversation Logs",
            "",
            "### Attempt 1",
        ])

        for i, qa in enumerate(attempt1.context.raw_conversation[:5], 1):
            lines.append(f"**Turn {i}:**")
            lines.append(f"  Q: {qa.get('question', '')[:100]}")
            lines.append(f"  A: {qa.get('response', '')[:150]}")
            lines.append("")

        lines.append("### Attempt 2 (Cross-Examination)")
        for i, qa in enumerate(attempt2.context.raw_conversation[:5], 1):
            lines.append(f"**Turn {i}:**")
            lines.append(f"  Q: {qa.get('question', '')[:100]}")
            lines.append(f"  A: {qa.get('response', '')[:150]}")
            lines.append("")

        lines.extend([
            "---",
            "",
            "## Recommended Actions",
            "",
            "1. **Review the conversation logs above** — they show exactly what the bot said",
            "2. **Try Partial Autonomous mode** — provide documentation instead:",
            "   ```",
            "   simtest run --mode partial --doc-dir ./docs/ --bot-endpoint <url>",
            "   ```",
            "3. **Try Manual mode** — provide everything explicitly:",
            "   ```",
            '   simtest run --documentation "Your bot description" --bot-endpoint <url>',
            "   ```",
            "4. **Check the bot's configuration** — the discovery results suggest the bot may have issues with self-description or identity consistency",
        ])

        return "\n".join(lines)