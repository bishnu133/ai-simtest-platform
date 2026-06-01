"""
Multi-Model Comparison — Run Manifest & Reproducibility.

Generates, saves, and validates RunManifest objects for
auditability and exact replay of comparison runs.

Also handles evaluator version pinning and QualityJudge
rubric hash capture.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.multi_compare.models import (
    EvaluatorVersions,
    MultiCompareConfig,
    RunManifest,
)
from src.multi_compare.config_loader import redact_model_spec


def build_manifest(
    config: MultiCompareConfig,
    config_path: str | Path,
    personas_json: str | None = None,
    scenario_assignment_json: str | None = None,
    documentation_text: str | None = None,
    evaluator_versions: EvaluatorVersions | None = None,
    quality_prompt_template: str | None = None,
    random_seed: int | None = None,
) -> RunManifest:
    """
    Build a RunManifest capturing all details needed for reproducibility.

    Called at the start of a comparison run, before model execution begins.

    Args:
        config: The validated MultiCompareConfig.
        config_path: Path to the original YAML config file.
        personas_json: Serialized persona list (for hashing).
        scenario_assignment_json: Serialized scenario assignments (for hashing).
        documentation_text: Documentation text used for grounding.
        evaluator_versions: Pinned evaluator versions. Auto-detected if None.
        quality_prompt_template: QualityJudge system prompt for hash capture.
        random_seed: Optional seed for persona generation reproducibility.

    Returns:
        RunManifest with all hashes and version pins.
    """
    # Compute config hash from raw file
    config_hash = ""
    config_file = Path(config_path)
    if config_file.exists():
        config_hash = RunManifest.compute_hash(
            config_file.read_text(encoding="utf-8")
        )

    # Compute persona set hash
    persona_set_id = ""
    if personas_json:
        persona_set_id = RunManifest.compute_hash(personas_json)

    # Compute scenario assignment hash
    scenario_assignment_id = ""
    if scenario_assignment_json:
        scenario_assignment_id = RunManifest.compute_hash(scenario_assignment_json)

    # Compute documentation hash
    documentation_hash = ""
    if documentation_text:
        documentation_hash = RunManifest.compute_hash(documentation_text)

    # Detect evaluator versions if not provided
    if evaluator_versions is None:
        evaluator_versions = detect_evaluator_versions()

    # Wire QualityJudge prompt hash into evaluator versions
    if quality_prompt_template:
        prompt_hash = capture_quality_judge_prompt_hash(quality_prompt_template)
        evaluator_versions.quality["prompt_hash"] = prompt_hash

    # Build redacted model specs
    model_specs_redacted = [
        redact_model_spec(spec) for spec in config.models
    ]

    return RunManifest(
        config_hash=config_hash,
        persona_set_id=persona_set_id,
        scenario_assignment_id=scenario_assignment_id,
        documentation_hash=documentation_hash,
        policy_version=config.settings.policy,
        workflow_version=config.settings.workflow,
        evaluator_versions=evaluator_versions,
        model_specs_redacted=model_specs_redacted,
        simtest_version="1.3.0",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        random_seed=random_seed,
    )


def detect_evaluator_versions() -> EvaluatorVersions:
    """
    Auto-detect installed evaluator versions.

    Captures: Sentence-BERT model, Presidio version,
    Detoxify version, QualityJudge LLM model.
    """
    grounding = {"model": "all-MiniLM-L6-v2", "threshold": "0.35"}
    safety = {"presidio": "unknown", "detoxify": "unknown"}
    quality = {"llm_model": "unknown", "prompt_hash": "unknown"}
    relevance = {"mode": "keyword"}

    # Try to detect Presidio version
    try:
        import presidio_analyzer
        safety["presidio"] = getattr(presidio_analyzer, "__version__", "installed")
    except ImportError:
        safety["presidio"] = "not_installed"

    # Try to detect Detoxify version
    try:
        import detoxify
        safety["detoxify"] = getattr(detoxify, "__version__", "installed")
    except ImportError:
        safety["detoxify"] = "not_installed"

    return EvaluatorVersions(
        grounding=grounding,
        safety=safety,
        quality=quality,
        relevance=relevance,
    )


def capture_quality_judge_prompt_hash(prompt_template: str) -> str:
    """
    Compute SHA-256 hash of the QualityJudge system prompt.

    This is stored in the manifest so that if the prompt changes
    between runs, the manifest hashes will differ — providing
    an audit flag.

    Args:
        prompt_template: Full QualityJudge system prompt text.

    Returns:
        SHA-256 hash string.
    """
    return RunManifest.compute_hash(prompt_template)


def save_manifest(manifest: RunManifest, output_dir: str | Path) -> Path:
    """
    Save the manifest to output_dir/run_manifest.json.

    Uses SafeSerializer to ensure no secrets in output.
    """
    from src.multi_compare.security import SafeSerializer

    output_path = Path(output_dir) / "run_manifest.json"
    data = manifest.model_dump(mode="json")
    SafeSerializer.write_json(data, output_path)
    return output_path


def load_manifest(output_dir: str | Path) -> RunManifest | None:
    """
    Load a previously saved manifest from output_dir/run_manifest.json.

    Returns None if file doesn't exist.
    """
    manifest_path = Path(output_dir) / "run_manifest.json"
    if not manifest_path.exists():
        return None

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return RunManifest(**data)


def validate_manifest_match(
    current: RunManifest,
    checkpoint: RunManifest,
) -> tuple[bool, list[str], list[str]]:
    """
    Validate that a checkpoint's manifest matches the current config.

    Used during resume to ensure the user hasn't changed the config
    since the checkpoint was created.

    Checks (blocking): config_hash, persona_set_id, scenario_assignment_id,
    documentation_hash, policy_version, workflow_version, major version.

    Checks (warning-only): minor/patch version, evaluator versions.

    Returns:
        (is_valid, list_of_blocking_mismatches, list_of_warnings)
    """
    mismatches: list[str] = []
    warnings: list[str] = []

    # ── Blocking checks ────────────────────────────────
    if current.config_hash and checkpoint.config_hash:
        if current.config_hash != checkpoint.config_hash:
            mismatches.append(
                f"Config file changed (hash: {current.config_hash[:12]}... "
                f"vs checkpoint: {checkpoint.config_hash[:12]}...)"
            )

    if current.persona_set_id and checkpoint.persona_set_id:
        if current.persona_set_id != checkpoint.persona_set_id:
            mismatches.append(
                f"Persona set changed (hash: {current.persona_set_id[:12]}... "
                f"vs checkpoint: {checkpoint.persona_set_id[:12]}...)"
            )

    if current.scenario_assignment_id and checkpoint.scenario_assignment_id:
        if current.scenario_assignment_id != checkpoint.scenario_assignment_id:
            mismatches.append(
                f"Scenario assignments changed (hash: "
                f"{current.scenario_assignment_id[:12]}... vs checkpoint: "
                f"{checkpoint.scenario_assignment_id[:12]}...)"
            )

    if current.documentation_hash and checkpoint.documentation_hash:
        if current.documentation_hash != checkpoint.documentation_hash:
            mismatches.append(
                f"Documentation changed (hash: {current.documentation_hash[:12]}... "
                f"vs checkpoint: {checkpoint.documentation_hash[:12]}...)"
            )

    if current.policy_version != checkpoint.policy_version:
        if current.policy_version or checkpoint.policy_version:
            mismatches.append(
                f"Policy version changed: '{checkpoint.policy_version}' → "
                f"'{current.policy_version}'"
            )

    if current.workflow_version != checkpoint.workflow_version:
        if current.workflow_version or checkpoint.workflow_version:
            mismatches.append(
                f"Workflow version changed: '{checkpoint.workflow_version}' → "
                f"'{current.workflow_version}'"
            )

    # Major version mismatch is blocking
    current_major = current.simtest_version.split(".")[0]
    checkpoint_major = checkpoint.simtest_version.split(".")[0]
    if current_major != checkpoint_major:
        mismatches.append(
            f"Major version mismatch: {current.simtest_version} "
            f"vs checkpoint: {checkpoint.simtest_version}"
        )

    # ── Warning-only checks ─────────────────────────────
    if current.simtest_version != checkpoint.simtest_version:
        if current_major == checkpoint_major:
            warnings.append(
                f"Minor/patch version changed: {checkpoint.simtest_version} → "
                f"{current.simtest_version} (compatible, proceeding)"
            )

    # Evaluator version drift warning
    if (current.evaluator_versions.quality.get("prompt_hash", "unknown") != "unknown"
            and checkpoint.evaluator_versions.quality.get("prompt_hash", "unknown") != "unknown"):
        if current.evaluator_versions.quality.get("prompt_hash") != checkpoint.evaluator_versions.quality.get("prompt_hash"):
            warnings.append(
                "QualityJudge prompt hash changed since checkpoint. "
                "Evaluation results may not be directly comparable."
            )

    is_valid = len(mismatches) == 0
    return is_valid, mismatches, warnings


def finalize_manifest(manifest: RunManifest) -> RunManifest:
    """
    Finalize the manifest at the end of a comparison run.

    Sets the end timestamp.
    """
    manifest.timestamp_end = datetime.now(timezone.utc).isoformat()
    return manifest
