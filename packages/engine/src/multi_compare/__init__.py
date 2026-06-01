"""
AI SimTest — Multi-Model Comparison (P4 #20).

Enterprise-grade N-way model comparison framework.
Compare 2–10 bot endpoints side-by-side under identical test conditions.

Usage:
    simtest multi-compare --config models.yaml
"""

from src.multi_compare.models import (
    # Enums
    AdapterType,
    ComparisonMode,
    Dimension,
    INVERTED_DIMENSIONS,
    StatisticalVerdictLabel,
    EvaluatorType,
    ModelRunStatus,
    ErrorCategory,
    BudgetRisk,
    RootCauseBucket,
    CheckpointStatus,
    GateVerdict,
    # Identity
    ConversationKey,
    TurnKey,
    TrialKey,
    # Config
    ModelSpec,
    ComparisonSettings,
    DecisionProfile,
    BUILT_IN_PROFILES,
    MultiCompareConfig,
    # Adapter responses
    NormalizedError,
    CanonicalBotResponse,
    # Results
    ModelRunResult,
    # Statistics
    StatisticalVerdict,
    DimensionScore,
    ModelRanking,
    # Coverage
    CoverageParityReport,
    # Forensics
    RootCause,
    FailureForensic,
    # Cost
    CostEfficiency,
    CostGovernance,
    # Diff
    DiffExplanation,
    # Analysis
    SliceResult,
    ComparisonMatrix,
    # Manifest
    EvaluatorVersions,
    RunManifest,
    # Checkpoint
    CheckpointState,
    # Gate
    GateCheck,
    GateResult,
    # Report
    MultiCompareReport,
)

__all__ = [
    "AdapterType", "ComparisonMode", "Dimension", "INVERTED_DIMENSIONS",
    "StatisticalVerdictLabel", "EvaluatorType", "ModelRunStatus",
    "ErrorCategory", "BudgetRisk", "RootCauseBucket", "CheckpointStatus",
    "GateVerdict",
    "ConversationKey", "TurnKey", "TrialKey",
    "ModelSpec", "ComparisonSettings", "DecisionProfile",
    "BUILT_IN_PROFILES", "MultiCompareConfig",
    "NormalizedError", "CanonicalBotResponse",
    "ModelRunResult",
    "StatisticalVerdict", "DimensionScore", "ModelRanking",
    "CoverageParityReport",
    "RootCause", "FailureForensic",
    "CostEfficiency", "CostGovernance",
    "DiffExplanation",
    "SliceResult", "ComparisonMatrix",
    "EvaluatorVersions", "RunManifest",
    "CheckpointState",
    "GateCheck", "GateResult",
    "MultiCompareReport",
]
