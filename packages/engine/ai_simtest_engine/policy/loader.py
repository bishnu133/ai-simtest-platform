"""
Policy Loader v2 — Load PolicySet from YAML files, directories, or dictionaries.

v2 upgrades:
- Parses new v2 fields: mode, compliance_threshold, evaluation_error_mode, controls,
  applies_to, match, composite, remediation, control_family, evidence_config
- Flattens control-family-grouped rules into flat rules list with control_family tag
- Stricter validation: unknown judges can hard-fail, threshold range checks expanded
- Backward-compatible: v1 YAML files load without changes
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from .models import (
    ComplianceGateMode,
    ContentMatch,
    CompositeRule,
    EvaluationErrorMode,
    PolicyCondition,
    PolicyRule,
    PolicySet,
    PolicySeverity,
    RuleScope,
)


class PolicyLoadError(Exception):
    """Raised when a policy file cannot be loaded or parsed."""
    pass


class PolicyLoader:
    """
    Loads and validates policy sets from various sources.
    Backward-compatible with v1 YAML while supporting v2 features.
    """

    # Valid judge names — expanded for v2 cross-module support
    VALID_JUDGES = {"grounding", "safety", "quality", "relevance", "overall", "workflow", "rag", "tool"}

    @staticmethod
    def load_from_file(path: Union[str, Path]) -> PolicySet:
        """Load a PolicySet from a YAML file."""
        path = Path(path)

        if not path.exists():
            raise PolicyLoadError(f"Policy file not found: {path}")

        if path.suffix.lower() not in (".yaml", ".yml"):
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
        """Load all PolicySets from YAML files in a directory."""
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
                    continue

        return policy_sets

    @staticmethod
    def load_from_dict(data: Dict[str, Any]) -> PolicySet:
        """
        Load a PolicySet from a Python dictionary.
        Handles both v1 and v2 schema.
        """
        if not isinstance(data, dict):
            raise PolicyLoadError(f"Expected dict, got {type(data).__name__}")

        if "name" not in data:
            raise PolicyLoadError("Policy set must have a 'name' field")

        # v2: Allow rules to come from top-level 'rules' or nested 'controls'
        has_rules = "rules" in data and isinstance(data.get("rules"), list)
        has_controls = "controls" in data and isinstance(data.get("controls"), list)

        if not has_rules and not has_controls:
            raise PolicyLoadError("Policy set must have a 'rules' list or 'controls' list")

        try:
            all_rules: List[PolicyRule] = []

            # Parse top-level rules
            if has_rules:
                for i, rule_data in enumerate(data["rules"]):
                    rule = PolicyLoader._parse_rule(rule_data, i)
                    all_rules.append(rule)

            # Parse controls → flatten rules with control_family tag
            if has_controls:
                for control_data in data["controls"]:
                    if not isinstance(control_data, dict):
                        continue
                    control_id = control_data.get("id", "unknown")
                    control_name = control_data.get("name", control_id)
                    control_severity = control_data.get("severity", None)
                    control_rules = control_data.get("rules", [])

                    for j, rule_data in enumerate(control_rules):
                        if not isinstance(rule_data, dict):
                            continue
                        # Inject control family metadata
                        if "control_family" not in rule_data:
                            rule_data["control_family"] = control_id
                        # Inherit severity from control if not set on rule
                        if "severity" not in rule_data and control_severity:
                            rule_data["severity"] = control_severity
                        rule = PolicyLoader._parse_rule(rule_data, j, control_name=control_name)
                        all_rules.append(rule)

            # Parse gate mode (v2)
            mode_str = data.get("mode", "critical_only")
            try:
                mode = ComplianceGateMode(mode_str)
            except ValueError:
                valid = [m.value for m in ComplianceGateMode]
                raise PolicyLoadError(f"Invalid gate mode '{mode_str}'. Valid: {valid}")

            # Parse evaluation error mode (v2)
            error_mode_str = data.get("evaluation_error_mode", "fail_closed")
            try:
                error_mode = EvaluationErrorMode(error_mode_str)
            except ValueError:
                valid = [e.value for e in EvaluationErrorMode]
                raise PolicyLoadError(f"Invalid evaluation_error_mode '{error_mode_str}'. Valid: {valid}")

            policy_set = PolicySet(
                name=data["name"],
                version=str(data.get("version", "1.0")),
                description=data.get("description", ""),
                industry=data.get("industry", "general"),
                rules=all_rules,
                metadata=data.get("metadata", {}),
                mode=mode,
                compliance_threshold=float(data.get("compliance_threshold", 0.0)),
                evaluation_error_mode=error_mode,
                max_tolerated_failures=int(data.get("max_tolerated_failures", 0)),
                default_no_data_mode=data.get("default_no_data_mode", "pass"),
                max_evidence_per_rule=int(data.get("max_evidence_per_rule", 10)),
                controls=data.get("controls", []),
            )

            return policy_set

        except PolicyLoadError:
            raise
        except Exception as e:
            raise PolicyLoadError(f"Failed to parse policy set: {e}")

    @staticmethod
    def _parse_rule(rule_data: Dict[str, Any], index: int, control_name: str = "") -> PolicyRule:
        """Parse a single rule dict into a PolicyRule. Handles v1 and v2 fields."""
        if not isinstance(rule_data, dict):
            raise PolicyLoadError(f"Rule {index} must be a mapping, got {type(rule_data).__name__}")

        # Validate condition
        condition_str = rule_data.get("condition", "")
        try:
            condition = PolicyCondition(condition_str)
        except ValueError:
            valid = [c.value for c in PolicyCondition]
            raise PolicyLoadError(
                f"Rule '{rule_data.get('id', index)}': invalid condition '{condition_str}'. Valid: {valid}"
            )

        # Validate severity
        severity_str = rule_data.get("severity", "high")
        try:
            severity = PolicySeverity(severity_str)
        except ValueError:
            valid = [s.value for s in PolicySeverity]
            raise PolicyLoadError(
                f"Rule '{rule_data.get('id', index)}': invalid severity '{severity_str}'. Valid: {valid}"
            )

        # Parse v2: applies_to scope
        applies_to = None
        if "applies_to" in rule_data and isinstance(rule_data["applies_to"], dict):
            scope_data = rule_data["applies_to"]
            applies_to = RuleScope(
                persona_types=scope_data.get("persona_types", []),
                scenarios=scope_data.get("scenarios", []),
                tags=scope_data.get("tags", []),
                workflows=scope_data.get("workflows", []),
                sources=scope_data.get("sources", []),
                turn_range=scope_data.get("turn_range", None),
                conversation_filter=scope_data.get("conversation_filter", None),
            )

        # Parse v2: content match
        match = None
        if "match" in rule_data and isinstance(rule_data["match"], dict):
            match_data = rule_data["match"]
            match = ContentMatch(
                patterns=match_data.get("patterns", []),
                issue_tags=match_data.get("issue_tags", []),
                regex=match_data.get("regex", None),
                field=match_data.get("field", "bot_response"),
                case_sensitive=match_data.get("case_sensitive", False),
            )

        # Parse v2: composite
        composite = None
        if "composite" in rule_data and isinstance(rule_data["composite"], dict):
            comp_data = rule_data["composite"]
            composite = CompositeRule(
                type=comp_data.get("type", "all_of"),
                rule_ids=comp_data.get("rule_ids", []),
                when_rule=comp_data.get("when_rule", None),
                then_rules=comp_data.get("then_rules", []),
            )

        rule = PolicyRule(
            id=rule_data.get("id", f"rule_{index}"),
            name=rule_data.get("name", f"Rule {index}"),
            description=rule_data.get("description", ""),
            judge=rule_data.get("judge", "overall"),
            condition=condition,
            threshold=float(rule_data.get("threshold", 0.0)),
            severity=severity,
            tags=rule_data.get("tags", []),
            metadata=rule_data.get("metadata", {}),
            control_family=rule_data.get("control_family", None),
            applies_to=applies_to,
            match=match,
            composite=composite,
            remediation=rule_data.get("remediation", ""),
            evidence_config=rule_data.get("evidence", {}),
            percentile=rule_data.get("percentile", None),
            metric_name=rule_data.get("metric_name", None),
            workflow_name=rule_data.get("workflow_name", None),
            on_no_data=rule_data.get("on_no_data", None),
        )

        return rule

    @staticmethod
    def load_built_in(template_name: str) -> Optional[PolicySet]:
        """Load a built-in policy template by name."""
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
    def validate_policy_set(policy_set: PolicySet, strict: bool = False) -> List[str]:
        """
        Validate a PolicySet for common issues.

        Args:
            policy_set: The policy set to validate.
            strict: If True, unknown judges cause errors instead of warnings.

        Returns:
            List of warning/error messages (empty = no issues).
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
        for rule in policy_set.rules:
            if rule.judge not in PolicyLoader.VALID_JUDGES:
                msg = (
                    f"Rule '{rule.id}': unknown judge '{rule.judge}'. "
                    f"Valid judges: {PolicyLoader.VALID_JUDGES}"
                )
                if strict:
                    raise PolicyLoadError(msg)
                warnings.append(msg)

        # Check threshold ranges
        for rule in policy_set.rules:
            if rule.condition in (
                PolicyCondition.MIN_SCORE,
                PolicyCondition.MIN_PASS_RATE,
                PolicyCondition.MAX_FAILURE_RATE,
            ):
                if not (0.0 <= rule.threshold <= 1.0):
                    warnings.append(
                        f"Rule '{rule.id}': threshold {rule.threshold} should be 0.0-1.0 "
                        f"for condition '{rule.condition.value}'"
                    )

            # v2: Check percentile range
            if rule.condition == PolicyCondition.MIN_SCORE_PERCENTILE:
                if rule.percentile is not None and not (1 <= rule.percentile <= 100):
                    warnings.append(
                        f"Rule '{rule.id}': percentile {rule.percentile} should be 1-100"
                    )

        # v2: Check composite rule references AND structural validity
        all_ids = set(rule_ids)
        valid_composite_types = {"all_of", "any_of", "not", "conditional"}
        for rule in policy_set.rules:
            if rule.composite:
                # Check composite type is valid
                if rule.composite.type.lower() not in valid_composite_types:
                    msg = f"Rule '{rule.id}': unsupported composite type '{rule.composite.type}'. Valid: {valid_composite_types}"
                    if strict:
                        raise PolicyLoadError(msg)
                    warnings.append(msg)

                # Check structural validity per type
                comp_type = rule.composite.type.lower()
                if comp_type in ("all_of", "any_of") and not rule.composite.rule_ids:
                    msg = f"Rule '{rule.id}': {comp_type} composite must have at least one rule_id"
                    if strict:
                        raise PolicyLoadError(msg)
                    warnings.append(msg)

                if comp_type == "not" and len(rule.composite.rule_ids) != 1:
                    msg = f"Rule '{rule.id}': 'not' composite must have exactly one rule_id, got {len(rule.composite.rule_ids)}"
                    if strict:
                        raise PolicyLoadError(msg)
                    warnings.append(msg)

                if comp_type == "conditional":
                    if not rule.composite.when_rule:
                        msg = f"Rule '{rule.id}': conditional composite missing 'when_rule'"
                        if strict:
                            raise PolicyLoadError(msg)
                        warnings.append(msg)
                    if not rule.composite.then_rules:
                        msg = f"Rule '{rule.id}': conditional composite missing 'then_rules'"
                        if strict:
                            raise PolicyLoadError(msg)
                        warnings.append(msg)

                # Check references exist
                for ref_id in rule.composite.rule_ids:
                    if ref_id not in all_ids:
                        msg = f"Rule '{rule.id}': composite references unknown rule '{ref_id}'"
                        if strict:
                            raise PolicyLoadError(msg)
                        warnings.append(msg)
                if rule.composite.when_rule and rule.composite.when_rule not in all_ids:
                    msg = f"Rule '{rule.id}': conditional 'when_rule' references unknown '{rule.composite.when_rule}'"
                    if strict:
                        raise PolicyLoadError(msg)
                    warnings.append(msg)
                for ref_id in rule.composite.then_rules:
                    if ref_id not in all_ids:
                        msg = f"Rule '{rule.id}': conditional 'then_rules' references unknown '{ref_id}'"
                        if strict:
                            raise PolicyLoadError(msg)
                        warnings.append(msg)

        # v2: Validate gate mode + threshold consistency
        if policy_set.mode in (ComplianceGateMode.WEIGHTED, ComplianceGateMode.THRESHOLD):
            if policy_set.compliance_threshold <= 0:
                msg = (
                    f"Gate mode '{policy_set.mode.value}' requires compliance_threshold > 0 "
                    f"(current: {policy_set.compliance_threshold})"
                )
                if strict:
                    raise PolicyLoadError(msg)
                warnings.append(msg)

        # v2: Validate content match rules have match config
        content_conditions = {
            PolicyCondition.MUST_CONTAIN, PolicyCondition.MUST_NOT_CONTAIN,
            PolicyCondition.REQUIRES_DISCLAIMER, PolicyCondition.REQUIRES_CITATION,
            PolicyCondition.REQUIRES_ESCALATION, PolicyCondition.REQUIRES_REFUSAL,
        }
        for rule in policy_set.rules:
            if rule.condition in content_conditions and rule.match is None:
                msg = f"Rule '{rule.id}': condition '{rule.condition.value}' requires a 'match' configuration"
                if strict:
                    raise PolicyLoadError(msg)
                warnings.append(msg)

        # v2.1: Validate on_no_data values
        valid_no_data = {"pass", "fail", "skip"}
        for rule in policy_set.rules:
            if rule.on_no_data and rule.on_no_data.lower() not in valid_no_data:
                msg = f"Rule '{rule.id}': on_no_data '{rule.on_no_data}' invalid. Valid: {valid_no_data}"
                if strict:
                    raise PolicyLoadError(msg)
                warnings.append(msg)
        if policy_set.default_no_data_mode.lower() not in valid_no_data:
            msg = f"default_no_data_mode '{policy_set.default_no_data_mode}' invalid. Valid: {valid_no_data}"
            if strict:
                raise PolicyLoadError(msg)
            warnings.append(msg)

        return warnings
