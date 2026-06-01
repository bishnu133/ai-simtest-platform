"""
Coverage constants — scenario metadata lookup and grade enum.

Scenario metadata mirrors src/scenarios/built_in.py categories/difficulties
without importing them, keeping coverage module dependency-free.
"""

from __future__ import annotations

import enum


class CoverageGrade(str, enum.Enum):
    """Letter grade for test coverage completeness."""

    A = "A"  # >= 90%
    B = "B"  # >= 75%
    C = "C"  # >= 60%
    D = "D"  # >= 40%
    F = "F"  # <  40%

    @classmethod
    def from_score(cls, score: float) -> CoverageGrade:
        if score >= 0.90:
            return cls.A
        elif score >= 0.75:
            return cls.B
        elif score >= 0.60:
            return cls.C
        elif score >= 0.40:
            return cls.D
        else:
            return cls.F


# Scenario metadata lookup (mirrors built_in.py without importing it)
SCENARIO_METADATA: dict[str, dict[str, str]] = {
    "clarification_required": {"category": "quality", "difficulty": "easy"},
    "goal_shift": {"category": "robustness", "difficulty": "medium"},
    "emotional_escalation": {"category": "empathy", "difficulty": "medium"},
    "prompt_injection": {"category": "safety", "difficulty": "hard"},
    "out_of_scope": {"category": "robustness", "difficulty": "easy"},
    "multi_intent": {"category": "quality", "difficulty": "medium"},
    "correction_loop": {"category": "quality", "difficulty": "medium"},
    "context_retention": {"category": "memory", "difficulty": "hard"},
}

# All scenario categories (from ScenarioCategory enum)
ALL_SCENARIO_CATEGORIES = ["robustness", "safety", "quality", "memory", "empathy"]

# All difficulty levels (from ScenarioDifficulty enum)
ALL_DIFFICULTY_LEVELS = ["easy", "medium", "hard"]
