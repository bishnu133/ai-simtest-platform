"""
Workflow Loader v3 — with enhanced validation, skip_if_not_applicable,
needs_review_threshold, topic_eval_mode, and strict schema checks.

V3 additions:
- Duplicate rule ID / condition ID detection
- Regex compilation validation at load time
- Order sequence gap warnings
- Empty values for phrase/topic rules
- Impossible pass thresholds
- Required workflow with zero required steps
- Overlapping activation hints across built-in templates
- Parse skip_if_not_applicable, needs_review_threshold, topic_eval_mode
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml
from .models import (HardRule, HardRuleType, MatchType, SuccessCondition,
                     TopicEvalMode, WorkflowDefinition, WorkflowStep)


class WorkflowLoadError(Exception):
    pass


class WorkflowLoader:
    @staticmethod
    def load_from_file(path: Union[str, Path]) -> WorkflowDefinition:
        path = Path(path)
        if not path.exists(): raise WorkflowLoadError(f"Workflow file not found: {path}")
        if path.suffix.lower() not in (".yaml", ".yml"): raise WorkflowLoadError(f"Must be .yaml/.yml: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f: data = yaml.safe_load(f)
        except yaml.YAMLError as e: raise WorkflowLoadError(f"Invalid YAML in {path}: {e}")
        if not isinstance(data, dict): raise WorkflowLoadError("Workflow must be a YAML mapping")
        return WorkflowLoader.load_from_dict(data)

    @staticmethod
    def load_from_directory(directory: Union[str, Path]) -> List[WorkflowDefinition]:
        directory = Path(directory)
        if not directory.exists(): raise WorkflowLoadError(f"Directory not found: {directory}")
        workflows = []
        for fp in sorted(directory.glob("*.y*ml")):
            if fp.suffix.lower() in (".yaml", ".yml"):
                try: workflows.append(WorkflowLoader.load_from_file(fp))
                except WorkflowLoadError: continue
        return workflows

    @staticmethod
    def load_from_dict(data: Dict[str, Any]) -> WorkflowDefinition:
        if not isinstance(data, dict): raise WorkflowLoadError(f"Expected dict, got {type(data).__name__}")
        if "name" not in data: raise WorkflowLoadError("Workflow must have a 'name' field")
        try:
            steps = []
            for i, s in enumerate(data.get("steps", [])):
                if not isinstance(s, dict): raise WorkflowLoadError(f"Step {i} must be a mapping")
                steps.append(WorkflowStep(id=s.get("id", f"step_{i}"), name=s.get("name", f"Step {i}"),
                    description=s.get("description", ""), required=s.get("required", True),
                    order=s.get("order"), detection_hints=s.get("detection_hints", [])))

            hard_rules = []
            for i, r in enumerate(data.get("hard_rules", [])):
                if not isinstance(r, dict): raise WorkflowLoadError(f"Hard rule {i} must be a mapping")
                rt_str = r.get("rule_type", "forbidden_phrase")
                try: rt = HardRuleType(rt_str)
                except ValueError:
                    raise WorkflowLoadError(f"Rule '{r.get('id', i)}': invalid rule_type '{rt_str}'. Valid: {[t.value for t in HardRuleType]}")
                # V2: validate match_type
                mt_str = r.get("match_type", "phrase")
                if mt_str not in [m.value for m in MatchType]:
                    raise WorkflowLoadError(f"Rule '{r.get('id', i)}': invalid match_type '{mt_str}'. Valid: {[m.value for m in MatchType]}")
                # V3: validate topic_eval_mode
                tem_str = r.get("topic_eval_mode", "keyword")
                if tem_str not in [t.value for t in TopicEvalMode]:
                    raise WorkflowLoadError(f"Rule '{r.get('id', i)}': invalid topic_eval_mode '{tem_str}'. Valid: {[t.value for t in TopicEvalMode]}")
                # V3: validate regex compilation at load time
                if mt_str == "regex":
                    for val in ([r.get("value", "")] + r.get("values", [])):
                        if val:
                            try:
                                re.compile(val)
                            except re.error as e:
                                raise WorkflowLoadError(f"Rule '{r.get('id', i)}': invalid regex pattern '{val}': {e}")
                # V3: warn on empty values for phrase/topic rules
                rule_values = r.get("values", [])
                rule_value = r.get("value", "")
                if rt_str in ("forbidden_phrase", "required_phrase", "forbidden_topic", "required_topic"):
                    if not rule_values and not rule_value:
                        raise WorkflowLoadError(f"Rule '{r.get('id', i)}': {rt_str} rule must have 'value' or 'values'")

                hard_rules.append(HardRule(id=r.get("id", f"rule_{i}"), name=r.get("name", f"Rule {i}"),
                    rule_type=rt, value=str(r.get("value", "")), values=rule_values,
                    severity=r.get("severity", "high"), case_sensitive=r.get("case_sensitive", False),
                    match_type=mt_str, description=r.get("description", ""),
                    topic_eval_mode=tem_str))

            conditions = []
            for i, c in enumerate(data.get("success_conditions", [])):
                if not isinstance(c, dict): raise WorkflowLoadError(f"Condition {i} must be a mapping")
                conditions.append(SuccessCondition(id=c.get("id", f"cond_{i}"),
                    description=c.get("description", f"Condition {i}"), required=c.get("required", True)))

            # V3: Parse new fields with defaults
            return WorkflowDefinition(
                id=data.get("id", ""), name=data["name"], domain=data.get("domain", "general"),
                description=data.get("description", ""), version=str(data.get("version", "1.0")),
                steps=steps, hard_rules=hard_rules, success_conditions=conditions,
                step_weight=float(data.get("step_weight", 0.5)), rule_weight=float(data.get("rule_weight", 0.3)),
                condition_weight=float(data.get("condition_weight", 0.2)),
                pass_threshold=float(data.get("pass_threshold", 0.7)),
                order_mode=data.get("order_mode", "none"),
                activation_hints=data.get("activation_hints", []),
                tags=data.get("tags", []), metadata=data.get("metadata", {}),
                skip_if_not_applicable=data.get("skip_if_not_applicable", True),
                needs_review_threshold=float(data.get("needs_review_threshold", 0.5)))
        except WorkflowLoadError: raise
        except Exception as e: raise WorkflowLoadError(f"Failed to parse workflow: {e}")

    @staticmethod
    def load_built_in(template_name: str) -> Optional[WorkflowDefinition]:
        from .built_in import BUILT_IN_WORKFLOWS
        template_name = template_name.lower().strip()
        if template_name in BUILT_IN_WORKFLOWS:
            return WorkflowLoader.load_from_dict(BUILT_IN_WORKFLOWS[template_name])
        return None

    @staticmethod
    def list_built_in() -> List[str]:
        from .built_in import BUILT_IN_WORKFLOWS
        return list(BUILT_IN_WORKFLOWS.keys())

    @staticmethod
    def validate(workflow: WorkflowDefinition) -> List[str]:
        """V3: Comprehensive validation returning warnings list."""
        warnings = []

        # Steps
        if not workflow.steps:
            warnings.append("Workflow has no steps defined")
        if not workflow.hard_rules and not workflow.success_conditions:
            warnings.append("Workflow has neither hard rules nor success conditions")

        # Duplicate step IDs
        step_ids = [s.id for s in workflow.steps]
        step_dupes = [sid for sid in step_ids if step_ids.count(sid) > 1]
        if step_dupes:
            warnings.append(f"Duplicate step IDs: {set(step_dupes)}")

        # V3: Duplicate rule IDs
        rule_ids = [r.id for r in workflow.hard_rules]
        rule_dupes = [rid for rid in rule_ids if rule_ids.count(rid) > 1]
        if rule_dupes:
            warnings.append(f"Duplicate hard rule IDs: {set(rule_dupes)}")

        # V3: Duplicate condition IDs
        cond_ids = [c.id for c in workflow.success_conditions]
        cond_dupes = [cid for cid in cond_ids if cond_ids.count(cid) > 1]
        if cond_dupes:
            warnings.append(f"Duplicate condition IDs: {set(cond_dupes)}")

        # Weight normalization warning
        weights = workflow.step_weight + workflow.rule_weight + workflow.condition_weight
        if abs(weights - 1.0) > 0.01:
            warnings.append(f"Weights sum to {weights:.2f}, expected ~1.0 (will be auto-normalized)")

        # V3: Impossible pass thresholds
        if workflow.pass_threshold > 1.0:
            warnings.append(f"Pass threshold {workflow.pass_threshold} > 1.0 — workflow can never pass")
        if workflow.pass_threshold < 0.0:
            warnings.append(f"Pass threshold {workflow.pass_threshold} < 0.0 — workflow always passes")

        # V3: Order sequence gaps
        ordered = [(s.order, s.id) for s in workflow.steps if s.order is not None]
        if ordered:
            orders = sorted([o for o, _ in ordered])
            for i in range(len(orders) - 1):
                if orders[i + 1] - orders[i] > 1:
                    warnings.append(f"Order sequence gap: {orders[i]} → {orders[i+1]} (missing {list(range(orders[i]+1, orders[i+1]))})")

        # V3: Required workflow with zero required steps
        if workflow.steps and not any(s.required for s in workflow.steps):
            warnings.append("Workflow has steps but none are marked as required")

        # V3: Regex compilation check (belt-and-suspenders, also checked at load)
        for rule in workflow.hard_rules:
            if rule.match_type == "regex":
                for val in ([rule.value] + list(rule.values)):
                    if val:
                        try:
                            re.compile(val)
                        except re.error as e:
                            warnings.append(f"Rule '{rule.id}': invalid regex '{val}': {e}")

        # V3: Empty values for phrase/topic rules
        for rule in workflow.hard_rules:
            if rule.rule_type.value in ("forbidden_phrase", "required_phrase", "forbidden_topic", "required_topic"):
                if not rule.values and not rule.value:
                    warnings.append(f"Rule '{rule.id}' ({rule.rule_type.value}) has no value or values")

        return warnings

    @staticmethod
    def validate_built_in_overlaps() -> List[str]:
        """V3: Check for overlapping activation hints across built-in templates."""
        from .built_in import BUILT_IN_WORKFLOWS
        warnings = []
        hint_map: Dict[str, List[str]] = {}
        for name, data in BUILT_IN_WORKFLOWS.items():
            for hint in data.get("activation_hints", []):
                key = hint.lower()
                if key not in hint_map:
                    hint_map[key] = []
                hint_map[key].append(name)
        for hint, templates in hint_map.items():
            if len(templates) > 1:
                warnings.append(f"Activation hint '{hint}' shared by: {templates}")
        return warnings
