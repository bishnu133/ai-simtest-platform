"""
Rule Engine v2 — Deterministic hard checks with match_type support and per-turn evidence.

V2 improvements:
- match_type: phrase (substring), whole_word (word boundary), regex
- Per-turn violation tracking (which turn caused it)
- Severity enum awareness
"""
from __future__ import annotations
import re
import logging
from typing import List
from .models import HardRule, HardRuleResult, HardRuleType, MatchType, TurnViolation

logger = logging.getLogger(__name__)


class RuleEngine:
    def __init__(self, rules: List[HardRule]):
        self.rules = rules

    def evaluate(self, bot_messages: List[str], all_messages: List[str] | None = None, turn_count: int = 0) -> List[HardRuleResult]:
        all_messages = all_messages or bot_messages
        return [self._evaluate_rule(rule, bot_messages, all_messages, turn_count) for rule in self.rules]

    def _evaluate_rule(self, rule, bot_messages, all_messages, turn_count) -> HardRuleResult:
        dispatch = {
            HardRuleType.FORBIDDEN_PHRASE: self._check_forbidden_phrase,
            HardRuleType.REQUIRED_PHRASE: self._check_required_phrase,
            HardRuleType.FORBIDDEN_TOPIC: self._check_forbidden_topic,
            HardRuleType.REQUIRED_TOPIC: self._check_required_topic,
            HardRuleType.MAX_TURNS_TO_RESOLVE: lambda r, b, a, t: self._check_max_turns(r, t),
            HardRuleType.MUST_ESCALATE: lambda r, b, a, t: self._check_must_escalate(r, b),
            HardRuleType.MUST_NOT_ESCALATE: lambda r, b, a, t: self._check_must_not_escalate(r, b),
        }
        fn = dispatch.get(rule.rule_type)
        if fn:
            return fn(rule, bot_messages, all_messages, turn_count)
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=True, severity=rule.severity, evidence="Unknown rule type", rule_type=str(rule.rule_type.value))

    def _get_phrases(self, rule: HardRule) -> List[str]:
        phrases = list(rule.values) if rule.values else []
        if rule.value and rule.value not in phrases:
            phrases.append(rule.value)
        return phrases

    def _match_text(self, text: str, phrase: str, rule: HardRule) -> bool:
        """Match using the rule's match_type."""
        if not rule.case_sensitive:
            text, phrase = text.lower(), phrase.lower()
        mt = rule.match_type_enum
        if mt == MatchType.WHOLE_WORD:
            pattern = r'\b' + re.escape(phrase) + r'\b'
            return bool(re.search(pattern, text))
        elif mt == MatchType.REGEX:
            try:
                flags = 0 if rule.case_sensitive else re.IGNORECASE
                return bool(re.search(phrase, text, flags))
            except re.error:
                return phrase in text
        else:  # PHRASE (substring)
            return phrase in text

    def _check_forbidden_phrase(self, rule, bot_messages, all_messages, turn_count) -> HardRuleResult:
        phrases = self._get_phrases(rule)
        violations = []
        turn_violations = []
        for i, msg in enumerate(bot_messages):
            for phrase in phrases:
                if self._match_text(msg, phrase, rule):
                    if phrase not in violations:
                        violations.append(phrase)
                    turn_violations.append(TurnViolation(turn_index=i, turn_text=msg[:200], matched_phrase=phrase))
        passed = len(violations) == 0
        evidence = f"Found forbidden: {violations}" if violations else "No forbidden phrases found"
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=passed, severity=rule.severity,
                              evidence=evidence, rule_type=HardRuleType.FORBIDDEN_PHRASE.value, turn_violations=turn_violations)

    def _check_required_phrase(self, rule, bot_messages, all_messages, turn_count) -> HardRuleResult:
        phrases = self._get_phrases(rule)
        combined = " ".join(bot_messages)
        missing = [p for p in phrases if not self._match_text(combined, p, rule)]
        passed = len(missing) == 0
        evidence = f"Missing required: {missing}" if missing else "All required phrases found"
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=passed, severity=rule.severity,
                              evidence=evidence, rule_type=HardRuleType.REQUIRED_PHRASE.value)

    def _check_forbidden_topic(self, rule, bot_messages, all_messages, turn_count) -> HardRuleResult:
        phrases = self._get_phrases(rule)
        combined = " ".join(bot_messages)
        found = [p for p in phrases if self._match_text(combined, p, rule)]
        passed = len(found) == 0
        evidence = f"Forbidden topic indicators: {found}" if found else "No forbidden topics detected"
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=passed, severity=rule.severity,
                              evidence=evidence, rule_type=HardRuleType.FORBIDDEN_TOPIC.value)

    def _check_required_topic(self, rule, bot_messages, all_messages, turn_count) -> HardRuleResult:
        phrases = self._get_phrases(rule)
        combined = " ".join(bot_messages)
        found = [p for p in phrases if self._match_text(combined, p, rule)]
        passed = len(found) > 0
        evidence = f"Topic indicators found: {found}" if found else f"Required topic not found (looking for: {phrases})"
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=passed, severity=rule.severity,
                              evidence=evidence, rule_type=HardRuleType.REQUIRED_TOPIC.value)

    def _check_max_turns(self, rule, turn_count) -> HardRuleResult:
        try: max_turns = int(rule.value) if rule.value else 20
        except ValueError: max_turns = 20
        passed = turn_count <= max_turns
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=passed, severity=rule.severity,
                              evidence=f"Conversation took {turn_count} turns (max: {max_turns})", rule_type=HardRuleType.MAX_TURNS_TO_RESOLVE.value)

    def _check_must_escalate(self, rule, bot_messages) -> HardRuleResult:
        indicators = ["human agent", "live agent", "customer support", "speak to a representative",
                      "transfer you", "connect you", "real person", "human representative",
                      "escalate", "support team", "customer service representative"]
        custom = self._get_phrases(rule)
        all_ind = indicators + [i.lower() for i in custom]
        combined = " ".join(bot_messages).lower()
        found = [i for i in all_ind if i in combined]
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=len(found) > 0, severity=rule.severity,
                              evidence=f"Escalation offered: {found[:3]}" if found else "No escalation offered",
                              rule_type=HardRuleType.MUST_ESCALATE.value)

    def _check_must_not_escalate(self, rule, bot_messages) -> HardRuleResult:
        indicators = ["human agent", "live agent", "transfer you", "connect you", "speak to a representative", "real person"]
        custom = self._get_phrases(rule)
        all_ind = indicators + [i.lower() for i in custom]
        combined = " ".join(bot_messages).lower()
        found = [i for i in all_ind if i in combined]
        return HardRuleResult(rule_id=rule.id, rule_name=rule.name, passed=len(found) == 0, severity=rule.severity,
                              evidence=f"Unnecessary escalation: {found}" if found else "Self-serve maintained",
                              rule_type=HardRuleType.MUST_NOT_ESCALATE.value)

    def calculate_score(self, results: List[HardRuleResult]) -> float:
        if not results: return 1.0
        return sum(1 for r in results if r.passed) / len(results)
