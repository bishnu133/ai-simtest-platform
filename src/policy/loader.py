"""
Policy Loader — Load PolicySet from YAML files, directories, or dictionaries.

Supports:
- Single YAML file: load_from_file("policy.yaml")
- Directory of YAML files: load_from_directory("policies/")
- Python dict: load_from_dict({...})
- Built-in templates: load_built_in("healthcare")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from .models import PolicyCondition, PolicyRule, PolicySet, PolicySeverity


class PolicyLoadError(Exception):
    """Raised when a policy file cannot be loaded or parsed."""
    pass


class PolicyLoader:
    """
    Loads and validates policy sets from various sources.
    """

    @staticmethod
    def load_from_file(path: Union[str, Path]) -> PolicySet:
        """
        Load a PolicySet from a YAML file.

        Args:
            path: Path to the YAML policy file.

        Returns:
            Validated PolicySet.

        Raises:
            PolicyLoadError: If file cannot be read or parsed.
        """
        path = Path(path)

        if not path.exists():
            raise PolicyLoadError(f"Policy file not found: {path}")

        if not path.suffix.lower() in (".yaml", ".yml"):
            raise PolicyLoadError(f"Policy file must be .yaml or .yml: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PolicyLoadError(f"Invalid YAML in {path}: {e}")
        except OSError as e:
            raise PolicyLoadError(f"Cannot read {path}: {e}")

        if not isinstance(data, dict):
            raise PolicyLoadError(f"Policy file must contain a YAML mapping, got {type(data).__name__}")

        return PolicyLoader.load_from_dict(data)

    @staticmethod
    def load_from_directory(directory: Union[str, Path]) -> List[PolicySet]:
        """
        Load all PolicySets from YAML files in a directory.

        Args:
            directory: Path to directory containing YAML policy files.

        Returns:
            List of validated PolicySets.
        """
        directory = Path(directory)

        if not directory.exists():
            raise PolicyLoadError(f"Policy directory not found: {directory}")

        if not directory.is_dir():
            raise PolicyLoadError(f"Not a directory: {directory}")

        policy_sets = []
        for file_path in sorted(directory.glob("*.y*ml")):
            if file_path.suffix.lower() in (".yaml", ".yml"):
                try:
                    ps = PolicyLoader.load_from_file(file_path)
                    policy_sets.append(ps)
                except PolicyLoadError:
                    # Skip invalid files but continue loading others
                    continue

        return policy_sets

    @staticmethod
    def load_from_dict(data: Dict[str, Any]) -> PolicySet:
        """
        Load a PolicySet from a Python dictionary.

        Args:
            data: Dictionary with policy set structure.

        Returns:
            Validated PolicySet.

        Raises:
            PolicyLoadError: If data is invalid.
        """
        if not isinstance(data, dict):
            raise PolicyLoadError(f"Expected dict, got {type(data).__name__}")

        if "name" not in data:
            raise PolicyLoadError("Policy set must have a 'name' field")

        if "rules" not in data or not isinstance(data.get("rules"), list):
            raise PolicyLoadError("Policy set must have a 'rules' list")

        try:
            rules = []
            for i, rule_data in enumerate(data["rules"]):
                if not isinstance(rule_data, dict):
                    raise PolicyLoadError(f"Rule {i} must be a mapping, got {type(rule_data).__name__}")

                # Validate and normalize condition
                condition_str = rule_data.get("condition", "")
                try:
                    condition = PolicyCondition(condition_str)
                except ValueError:
                    valid = [c.value for c in PolicyCondition]
                    raise PolicyLoadError(
                        f"Rule '{rule_data.get('id', i)}': invalid condition '{condition_str}'. "
                        f"Valid conditions: {valid}"
                    )

                # Validate and normalize severity
                severity_str = rule_data.get("severity", "high")
                try:
                    severity = PolicySeverity(severity_str)
                except ValueError:
                    valid = [s.value for s in PolicySeverity]
                    raise PolicyLoadError(
                        f"Rule '{rule_data.get('id', i)}': invalid severity '{severity_str}'. "
                        f"Valid severities: {valid}"
                    )

                rule = PolicyRule(
                    id=rule_data.get("id", f"rule_{i}"),
                    name=rule_data.get("name", f"Rule {i}"),
                    description=rule_data.get("description", ""),
                    judge=rule_data.get("judge", "overall"),
                    condition=condition,
                    threshold=float(rule_data.get("threshold", 0.0)),
                    severity=severity,
                    tags=rule_data.get("tags", []),
                    metadata=rule_data.get("metadata", {}),
                )
                rules.append(rule)

            policy_set = PolicySet(
                name=data["name"],
                version=str(data.get("version", "1.0")),
                description=data.get("description", ""),
                industry=data.get("industry", "general"),
                rules=rules,
                metadata=data.get("metadata", {}),
            )

            return policy_set

        except PolicyLoadError:
            raise
        except Exception as e:
            raise PolicyLoadError(f"Failed to parse policy set: {e}")

    @staticmethod
    def load_built_in(template_name: str) -> Optional[PolicySet]:
        """
        Load a built-in policy template by name.

        Args:
            template_name: Template name (e.g., "general", "healthcare", "finance", "airline").

        Returns:
            PolicySet if template exists, None otherwise.
        """
        from .built_in import BUILT_IN_POLICIES

        template_name = template_name.lower().strip()

        if template_name in BUILT_IN_POLICIES:
            return PolicyLoader.load_from_dict(BUILT_IN_POLICIES[template_name])

        return None

    @staticmethod
    def list_built_in() -> List[str]:
        """List available built-in policy template names."""
        from .built_in import BUILT_IN_POLICIES
        return list(BUILT_IN_POLICIES.keys())

    @staticmethod
    def validate_policy_set(policy_set: PolicySet) -> List[str]:
        """
        Validate a PolicySet for common issues.

        Returns:
            List of warning messages (empty = no issues).
        """
        warnings = []

        if not policy_set.rules:
            warnings.append("Policy set has no rules defined")

        # Check for duplicate rule IDs
        rule_ids = [r.id for r in policy_set.rules]
        duplicates = [rid for rid in rule_ids if rule_ids.count(rid) > 1]
        if duplicates:
            warnings.append(f"Duplicate rule IDs found: {set(duplicates)}")

        # Check for valid judge names
        valid_judges = {"grounding", "safety", "quality", "relevance", "overall"}
        for rule in policy_set.rules:
            if rule.judge not in valid_judges:
                warnings.append(
                    f"Rule '{rule.id}': unknown judge '{rule.judge}'. "
                    f"Valid judges: {valid_judges}"
                )

        # Check threshold ranges
        for rule in policy_set.rules:
            if rule.condition in (PolicyCondition.MIN_SCORE, PolicyCondition.MIN_PASS_RATE):
                if not (0.0 <= rule.threshold <= 1.0):
                    warnings.append(
                        f"Rule '{rule.id}': threshold {rule.threshold} should be 0.0-1.0 "
                        f"for condition '{rule.condition.value}'"
                    )
            if rule.condition == PolicyCondition.MAX_FAILURE_RATE:
                if not (0.0 <= rule.threshold <= 1.0):
                    warnings.append(
                        f"Rule '{rule.id}': threshold {rule.threshold} should be 0.0-1.0 "
                        f"for condition '{rule.condition.value}'"
                    )

        return warnings
