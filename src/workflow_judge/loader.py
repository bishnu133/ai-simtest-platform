"""
Workflow Loader v2 — with match_type, order_mode, activation_hints, weight normalization.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml
from .models import HardRule, HardRuleType, MatchType, SuccessCondition, WorkflowDefinition, WorkflowStep


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
                hard_rules.append(HardRule(id=r.get("id", f"rule_{i}"), name=r.get("name", f"Rule {i}"),
                    rule_type=rt, value=str(r.get("value", "")), values=r.get("values", []),
                    severity=r.get("severity", "high"), case_sensitive=r.get("case_sensitive", False),
                    match_type=mt_str, description=r.get("description", "")))

            conditions = []
            for i, c in enumerate(data.get("success_conditions", [])):
                if not isinstance(c, dict): raise WorkflowLoadError(f"Condition {i} must be a mapping")
                conditions.append(SuccessCondition(id=c.get("id", f"cond_{i}"),
                    description=c.get("description", f"Condition {i}"), required=c.get("required", True)))

            return WorkflowDefinition(
                id=data.get("id", ""), name=data["name"], domain=data.get("domain", "general"),
                description=data.get("description", ""), version=str(data.get("version", "1.0")),
                steps=steps, hard_rules=hard_rules, success_conditions=conditions,
                step_weight=float(data.get("step_weight", 0.5)), rule_weight=float(data.get("rule_weight", 0.3)),
                condition_weight=float(data.get("condition_weight", 0.2)),
                pass_threshold=float(data.get("pass_threshold", 0.7)),
                order_mode=data.get("order_mode", "none"),
                activation_hints=data.get("activation_hints", []),
                tags=data.get("tags", []), metadata=data.get("metadata", {}))
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
        warnings = []
        if not workflow.steps: warnings.append("Workflow has no steps defined")
        if not workflow.hard_rules and not workflow.success_conditions:
            warnings.append("Workflow has neither hard rules nor success conditions")
        step_ids = [s.id for s in workflow.steps]
        dupes = [sid for sid in step_ids if step_ids.count(sid) > 1]
        if dupes: warnings.append(f"Duplicate step IDs: {set(dupes)}")
        weights = workflow.step_weight + workflow.rule_weight + workflow.condition_weight
        if abs(weights - 1.0) > 0.01: warnings.append(f"Weights sum to {weights:.2f}, expected ~1.0 (will be auto-normalized)")
        return warnings
