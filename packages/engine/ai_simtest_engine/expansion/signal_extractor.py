"""
Signal Extractor — Scans judged conversations and extracts structured failure signals.

Phase 1 of the Adaptive Expansion pipeline:
  JudgedConversation[] → FailureSignal[] (ranked by severity)
"""

from __future__ import annotations

import uuid
from typing import Any

from .models import AdaptiveExpansionConfig, FailureSignal, SignalSeverity


class SignalExtractor:
    """
    Extracts failure signals from judged conversations.

    Scans every judged turn for failures, creates structured FailureSignal
    objects, deduplicates similar failures, and ranks by severity.
    """

    # Map judge severity strings to our SignalSeverity
    _SEVERITY_MAP = {
        "critical": SignalSeverity.CRITICAL,
        "high": SignalSeverity.HIGH,
        "medium": SignalSeverity.MEDIUM,
        "low": SignalSeverity.LOW,
        "info": SignalSeverity.LOW,
    }

    def __init__(self, config: AdaptiveExpansionConfig | None = None):
        self.config = config or AdaptiveExpansionConfig()

    def extract(
        self,
        judged_conversations: list,
        workflow_results: list[dict[str, Any]] | None = None,
    ) -> list[FailureSignal]:
        """
        Extract failure signals from judged conversations.

        Args:
            judged_conversations: List of JudgedConversation objects from the simulation.
            workflow_results: Optional workflow judge results (list of dicts with
                'conversation_id', 'violations', 'score', etc.).

        Returns:
            List of FailureSignal objects, ranked by severity then score (worst first).
        """
        signals: list[FailureSignal] = []

        # Extract from generic judge failures
        for jc in judged_conversations:
            conv = jc.conversation if hasattr(jc, "conversation") else jc
            persona = jc.persona if hasattr(jc, "persona") else None
            judged_turns = jc.judged_turns if hasattr(jc, "judged_turns") else []

            conv_id = conv.id if hasattr(conv, "id") else str(uuid.uuid4())[:8]
            persona_name = persona.name if persona and hasattr(persona, "name") else "Unknown"
            persona_type = (
                persona.persona_type.value
                if persona and hasattr(persona, "persona_type") and hasattr(persona.persona_type, "value")
                else "standard"
            )

            # Build turn list for context
            all_turns = conv.turns if hasattr(conv, "turns") else []

            for turn_idx, jt in enumerate(judged_turns):
                turn = jt.turn if hasattr(jt, "turn") else jt
                judgments = jt.judgments if hasattr(jt, "judgments") else []
                overall_label = jt.overall_label if hasattr(jt, "overall_label") else None

                # Check individual judge failures — even in PASS turns,
                # individual judges may have failed (e.g., relevance failed but
                # overall score was above threshold due to other judges passing).
                # We extract signals from ANY turn that has at least one failing judge.

                # Extract the user message that preceded this bot turn
                user_message = self._find_preceding_user_message(all_turns, turn)

                for judgment in judgments:
                    if judgment is None:
                        continue
                    if judgment.passed:
                        continue

                    severity = self._map_severity(judgment.severity)
                    if not self._meets_min_severity(severity):
                        continue

                    # Build conversation context (preceding turns)
                    context = self._build_context(all_turns, turn, max_context=6)

                    signal = FailureSignal(
                        signal_id=f"sig_{uuid.uuid4().hex[:8]}",
                        judge_name=judgment.judge_name,
                        severity=severity,
                        score=judgment.score,
                        message=judgment.message,
                        conversation_id=conv_id,
                        persona_name=persona_name,
                        persona_type=persona_type,
                        turn_index=turn_idx,
                        user_message=user_message,
                        bot_response=turn.message if hasattr(turn, "message") else str(turn),
                        conversation_context=context,
                        evidence=judgment.evidence if hasattr(judgment, "evidence") and judgment.evidence else {},
                    )
                    signals.append(signal)

        # Extract from workflow judge results
        if workflow_results and self.config.include_workflow_signals:
            for wr in workflow_results:
                wf_signals = self._extract_workflow_signals(wr)
                signals.extend(wf_signals)

        # Deduplicate similar signals
        signals = self._deduplicate(signals)

        # Sort: CRITICAL first, then HIGH, etc. Within same severity, lowest score first.
        severity_order = {
            SignalSeverity.CRITICAL: 0,
            SignalSeverity.HIGH: 1,
            SignalSeverity.MEDIUM: 2,
            SignalSeverity.LOW: 3,
        }
        signals.sort(key=lambda s: (severity_order.get(s.severity, 9), s.score))

        # Limit to max_signals
        return signals[: self.config.max_signals]

    def _find_preceding_user_message(self, all_turns: list, bot_turn) -> str:
        """Find the user message that came right before this bot turn."""
        bot_msg = bot_turn.message if hasattr(bot_turn, "message") else str(bot_turn)
        found_bot = False
        for turn in reversed(all_turns):
            msg = turn.message if hasattr(turn, "message") else str(turn)
            speaker = turn.speaker if hasattr(turn, "speaker") else ""
            if msg == bot_msg and speaker == "bot":
                found_bot = True
                continue
            if found_bot and speaker == "user":
                return msg
        return ""

    def _build_context(self, all_turns: list, bot_turn, max_context: int = 6) -> list[dict]:
        """Build conversation context preceding the failing turn."""
        context = []
        bot_msg = bot_turn.message if hasattr(bot_turn, "message") else str(bot_turn)

        for turn in all_turns:
            msg = turn.message if hasattr(turn, "message") else str(turn)
            speaker = turn.speaker if hasattr(turn, "speaker") else ""
            if msg == bot_msg and speaker == "bot":
                break
            context.append({"speaker": speaker, "message": msg})

        return context[-max_context:]

    def _extract_workflow_signals(self, wr: dict) -> list[FailureSignal]:
        """Extract signals from workflow judge results."""
        signals = []
        violations = wr.get("violations", [])
        conv_id = wr.get("conversation_id", "unknown")
        persona_name = wr.get("persona_name", "Unknown")

        for violation in violations:
            severity_str = violation.get("severity", "medium")
            severity = self._SEVERITY_MAP.get(severity_str, SignalSeverity.MEDIUM)

            if not self._meets_min_severity(severity):
                continue

            signals.append(FailureSignal(
                signal_id=f"sig_wf_{uuid.uuid4().hex[:8]}",
                judge_name="workflow",
                severity=severity,
                score=violation.get("score", 0.0),
                message=violation.get("message", "Workflow violation"),
                conversation_id=conv_id,
                persona_name=persona_name,
                persona_type="standard",
                turn_index=violation.get("turn_index", 0),
                user_message=violation.get("user_message", ""),
                bot_response=violation.get("bot_response", ""),
                conversation_context=violation.get("context", []),
                evidence=violation.get("evidence", {}),
            ))

        return signals

    def _map_severity(self, severity) -> SignalSeverity:
        """Map judge severity to our SignalSeverity enum."""
        if severity is None:
            return SignalSeverity.MEDIUM
        val = severity.value if hasattr(severity, "value") else str(severity).lower()
        return self._SEVERITY_MAP.get(val, SignalSeverity.MEDIUM)

    def _meets_min_severity(self, severity: SignalSeverity) -> bool:
        """Check if a severity meets the minimum threshold."""
        order = {
            SignalSeverity.CRITICAL: 0,
            SignalSeverity.HIGH: 1,
            SignalSeverity.MEDIUM: 2,
            SignalSeverity.LOW: 3,
        }
        return order.get(severity, 9) <= order.get(self.config.min_severity, 2)

    def _deduplicate(self, signals: list[FailureSignal]) -> list[FailureSignal]:
        """
        Deduplicate signals with the same judge + similar message.

        When multiple turns in the same conversation fail with the same judge
        and similar message, keep only the most severe one.
        Uses word-set overlap (Jaccard > 0.7) for fuzzy matching.
        """
        severity_order = {
            SignalSeverity.CRITICAL: 0,
            SignalSeverity.HIGH: 1,
            SignalSeverity.MEDIUM: 2,
            SignalSeverity.LOW: 3,
        }

        deduped: list[FailureSignal] = []

        for signal in signals:
            is_duplicate = False
            sig_words = set(signal.message.lower().split())

            for i, existing in enumerate(deduped):
                # Must be same judge + same conversation
                if existing.judge_name != signal.judge_name:
                    continue
                if existing.conversation_id != signal.conversation_id:
                    continue

                # Jaccard similarity on message words
                ex_words = set(existing.message.lower().split())
                if not sig_words or not ex_words:
                    continue
                intersection = sig_words & ex_words
                union = sig_words | ex_words
                jaccard = len(intersection) / len(union) if union else 0

                if jaccard >= 0.6:
                    # Keep the more severe one
                    if severity_order.get(signal.severity, 9) < severity_order.get(existing.severity, 9):
                        deduped[i] = signal
                    is_duplicate = True
                    break

            if not is_duplicate:
                deduped.append(signal)

        return deduped