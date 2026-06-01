"""
Golden Dataset Manager — manages golden example sets for calibration.

Supports:
  - Built-in examples (shipped with AI SimTest)
  - Custom examples (user-provided JSON files)
  - Merging built-in + custom
  - Filtering by judge, category, difficulty, tags
  - Validation of examples
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_simtest_engine.calibration.models import GoldenExample, ExampleCategory
from ai_simtest_engine.calibration.built_in_examples import get_built_in_examples


class GoldenDatasetManager:
    """Manages golden example sets for judge calibration."""

    def __init__(self):
        self._examples: list[GoldenExample] = []

    @property
    def examples(self) -> list[GoldenExample]:
        return list(self._examples)

    @property
    def count(self) -> int:
        return len(self._examples)

    def load_built_in(self) -> int:
        """Load the built-in golden examples. Returns count loaded."""
        built_in = get_built_in_examples()
        self._examples.extend(built_in)
        return len(built_in)

    def load_from_file(self, path: str | Path) -> int:
        """Load custom golden examples from a JSON file. Returns count loaded."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Golden dataset file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        examples = data if isinstance(data, list) else data.get("examples", [])
        loaded = 0
        for item in examples:
            try:
                example = GoldenExample.from_dict(item)
                self._examples.append(example)
                loaded += 1
            except (KeyError, ValueError) as e:
                # Skip invalid examples with a warning
                continue

        return loaded

    def save_to_file(self, path: str | Path) -> int:
        """Save current examples to a JSON file. Returns count saved."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {"examples": [ex.to_dict() for ex in self._examples]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return len(self._examples)

    def filter_by_judge(self, judge_name: str) -> list[GoldenExample]:
        """Get examples targeting a specific judge."""
        return [ex for ex in self._examples if ex.target_judge == judge_name]

    def filter_by_category(self, category: ExampleCategory) -> list[GoldenExample]:
        """Get examples of a specific category."""
        return [ex for ex in self._examples if ex.category == category]

    def filter_by_difficulty(self, difficulty: str) -> list[GoldenExample]:
        """Get examples of a specific difficulty."""
        return [ex for ex in self._examples if ex.difficulty == difficulty]

    def filter_by_tags(self, tags: list[str]) -> list[GoldenExample]:
        """Get examples that have ANY of the specified tags."""
        tag_set = set(tags)
        return [ex for ex in self._examples if tag_set & set(ex.tags)]

    def get_judge_names(self) -> list[str]:
        """Get unique judge names in the dataset."""
        return sorted(set(ex.target_judge for ex in self._examples))

    def get_stats(self) -> dict:
        """Get dataset statistics."""
        by_judge: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_verdict: dict[str, int] = {}

        for ex in self._examples:
            by_judge[ex.target_judge] = by_judge.get(ex.target_judge, 0) + 1
            by_category[ex.category.value] = by_category.get(ex.category.value, 0) + 1
            by_verdict[ex.expected_verdict.value] = by_verdict.get(ex.expected_verdict.value, 0) + 1

        return {
            "total": len(self._examples),
            "by_judge": by_judge,
            "by_category": by_category,
            "by_expected_verdict": by_verdict,
        }

    def validate(self) -> list[str]:
        """Validate all examples. Returns list of issues found."""
        issues = []
        seen_ids = set()

        for ex in self._examples:
            if ex.id in seen_ids:
                issues.append(f"Duplicate ID: {ex.id}")
            seen_ids.add(ex.id)

            if not ex.user_message.strip():
                issues.append(f"{ex.id}: Empty user_message")
            if not ex.bot_response.strip():
                issues.append(f"{ex.id}: Empty bot_response")
            if ex.expected_score_min > ex.expected_score_max:
                issues.append(f"{ex.id}: score_min ({ex.expected_score_min}) > score_max ({ex.expected_score_max})")
            if ex.target_judge == "grounding" and not ex.documentation:
                issues.append(f"{ex.id}: Grounding example missing documentation")

        return issues

    def add_example(self, example: GoldenExample) -> None:
        """Add a single example."""
        self._examples.append(example)

    def remove_by_id(self, example_id: str) -> bool:
        """Remove an example by ID. Returns True if found and removed."""
        before = len(self._examples)
        self._examples = [ex for ex in self._examples if ex.id != example_id]
        return len(self._examples) < before

    def clear(self) -> None:
        """Remove all examples."""
        self._examples.clear()
