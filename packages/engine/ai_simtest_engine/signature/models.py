"""
P3 #19 — Behavioral Signature Analysis: Data Models

All Pydantic models for the signature module.
Every analyzer profile has two layers:
  - raw_metrics: stable, auditable numbers
  - derived_scores: interpreted labels/scores (may evolve)

Analysis hierarchy:
  turn-level signals → conversation aggregates → persona aggregates → run signature
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerbosityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FormalityLevel(str, Enum):
    INFORMAL = "informal"
    NEUTRAL = "neutral"
    FORMAL = "formal"


class DriftDirection(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"


class AnomalyType(str, Enum):
    LENGTH_OUTLIER = "length_outlier"
    TONE_SHIFT = "tone_shift"
    FORMALITY_SHIFT = "formality_shift"
    HIGH_REPETITION = "high_repetition"
    EXCESSIVE_HEDGING = "excessive_hedging"
    EXCESSIVE_APOLOGY = "excessive_apology"
    UNUSUAL_QUESTION_RATIO = "unusual_question_ratio"
    REFUSAL_SPIKE = "refusal_spike"
    CUSTOM = "custom"


# ── Reliability ──────────────────────────────────────────────


class ReliabilityInfo(BaseModel):
    """Confidence and caveats for any metric or profile."""
    confidence: Confidence = Confidence.HIGH
    sample_count: int = 0
    notes: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence.value,
            "sample_count": self.sample_count,
            "notes": self.notes,
        }


# ── Turn-Level Signals ───────────────────────────────────────


class TurnSignals(BaseModel):
    """Raw signals extracted from a single bot response."""
    turn_index: int
    conversation_id: str
    word_count: int = 0
    sentence_count: int = 0
    avg_words_per_sentence: float = 0.0
    unique_word_ratio: float = 0.0  # unique words / total words

    # Tone markers
    positive_markers: int = 0
    negative_markers: int = 0
    neutral_indicators: int = 0
    empathy_phrases: int = 0
    hedge_phrases: int = 0
    directive_phrases: int = 0
    apology_phrases: int = 0

    # Formality
    formality_score: float = 0.5  # 0=very informal, 1=very formal

    # Refusal
    is_refusal: bool = False
    refusal_has_redirect: bool = False

    # Structure
    has_list: bool = False
    has_numbered_list: bool = False
    question_count: int = 0
    section_count: int = 0  # headers / multi-section responses

    # Raw text reference (for evidence)
    text_snippet: str = ""  # first 200 chars for evidence display

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ── Tone Profile ─────────────────────────────────────────────


class ToneRawMetrics(BaseModel):
    """Auditable raw tone numbers."""
    total_responses: int = 0
    positive_marker_count: int = 0
    negative_marker_count: int = 0
    neutral_indicator_count: int = 0
    empathy_phrase_count: int = 0
    hedge_phrase_count: int = 0
    directive_phrase_count: int = 0
    apology_phrase_count: int = 0
    avg_formality_score: float = 0.5
    formality_std_dev: float = 0.0
    refusal_count: int = 0
    refusal_with_redirect_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ToneDerivedScores(BaseModel):
    """Interpreted tone labels."""
    positive_ratio: float = 0.0  # positive_markers / total markers
    negative_ratio: float = 0.0
    neutral_ratio: float = 0.0
    empathy_frequency: float = 0.0  # empathy phrases per response
    hedge_frequency: float = 0.0
    directive_frequency: float = 0.0
    apology_frequency: float = 0.0
    formality_level: FormalityLevel = FormalityLevel.NEUTRAL
    refusal_rate: float = 0.0  # refusals / total responses
    refusal_redirect_rate: float = 0.0  # redirected refusals / total refusals

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["formality_level"] = self.formality_level.value
        return d


class ToneProfile(BaseModel):
    """Complete tone analysis for a run."""
    raw: ToneRawMetrics = Field(default_factory=ToneRawMetrics)
    derived: ToneDerivedScores = Field(default_factory=ToneDerivedScores)
    reliability: ReliabilityInfo = Field(default_factory=ReliabilityInfo)
    evidence: list[dict[str, str]] = Field(default_factory=list)  # [{metric, value, example}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw.to_dict(),
            "derived": self.derived.to_dict(),
            "reliability": self.reliability.to_dict(),
            "evidence": self.evidence,
        }


# ── Verbosity Profile ────────────────────────────────────────


class VerbosityRawMetrics(BaseModel):
    """Auditable raw verbosity numbers."""
    total_responses: int = 0
    total_words: int = 0
    mean_words: float = 0.0
    median_words: float = 0.0
    p95_words: float = 0.0
    min_words: int = 0
    max_words: int = 0
    mean_sentences: float = 0.0
    mean_words_per_sentence: float = 0.0
    mean_unique_word_ratio: float = 0.0
    # Trend: early vs late turns
    early_turn_mean_words: float = 0.0  # first third of turns
    late_turn_mean_words: float = 0.0  # last third of turns

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class VerbosityDerivedScores(BaseModel):
    """Interpreted verbosity labels."""
    verbosity_level: VerbosityLevel = VerbosityLevel.MEDIUM
    verbosity_drift_direction: DriftDirection = DriftDirection.STABLE
    verbosity_drift_magnitude: float = 0.0  # % change early→late
    information_density: float = 0.0  # unique_word_ratio as density proxy

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["verbosity_level"] = self.verbosity_level.value
        d["verbosity_drift_direction"] = self.verbosity_drift_direction.value
        return d


class VerbosityProfile(BaseModel):
    """Complete verbosity analysis for a run."""
    raw: VerbosityRawMetrics = Field(default_factory=VerbosityRawMetrics)
    derived: VerbosityDerivedScores = Field(default_factory=VerbosityDerivedScores)
    reliability: ReliabilityInfo = Field(default_factory=ReliabilityInfo)
    evidence: list[dict[str, str]] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw.to_dict(),
            "derived": self.derived.to_dict(),
            "reliability": self.reliability.to_dict(),
            "evidence": self.evidence,
        }


# ── Pattern Profile ──────────────────────────────────────────


class RepeatedPhrase(BaseModel):
    """A phrase that appears across multiple responses."""
    phrase: str
    count: int
    percentage: float = 0.0  # % of responses containing this phrase
    category: str = "general"  # opening, closing, apology, disclaimer, hedge, general

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class PatternRawMetrics(BaseModel):
    """Auditable raw pattern numbers."""
    total_responses: int = 0
    unique_opening_phrases: int = 0
    unique_closing_phrases: int = 0
    total_unique_ngrams: int = 0
    question_asking_responses: int = 0
    list_usage_responses: int = 0
    multi_section_responses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class PatternDerivedScores(BaseModel):
    """Interpreted pattern labels."""
    # (A) Repetition
    repetition_score: float = 0.0  # 0=unique every time, 1=extremely repetitive
    top_repeated_phrases: list[RepeatedPhrase] = Field(default_factory=list)
    top_openings: list[RepeatedPhrase] = Field(default_factory=list)
    top_closings: list[RepeatedPhrase] = Field(default_factory=list)
    # (B) Interaction style
    question_asking_rate: float = 0.0  # % responses that ask a question
    list_usage_rate: float = 0.0  # % responses with lists
    multi_section_rate: float = 0.0  # % responses with headers/sections
    # Signature phrases (distinctive n-grams)
    signature_phrases: list[RepeatedPhrase] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["top_repeated_phrases"] = [p.to_dict() for p in self.top_repeated_phrases]
        d["top_openings"] = [p.to_dict() for p in self.top_openings]
        d["top_closings"] = [p.to_dict() for p in self.top_closings]
        d["signature_phrases"] = [p.to_dict() for p in self.signature_phrases]
        return d


class PatternProfile(BaseModel):
    """Complete pattern analysis for a run."""
    raw: PatternRawMetrics = Field(default_factory=PatternRawMetrics)
    derived: PatternDerivedScores = Field(default_factory=PatternDerivedScores)
    reliability: ReliabilityInfo = Field(default_factory=ReliabilityInfo)
    evidence: list[dict[str, str]] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw.to_dict(),
            "derived": self.derived.to_dict(),
            "reliability": self.reliability.to_dict(),
            "evidence": self.evidence,
        }


# ── Consistency Profile (Meta-Analyzer) ──────────────────────


class ConsistencyRawMetrics(BaseModel):
    """Auditable consistency numbers — computed from other analyzer outputs."""
    persona_group_count: int = 0
    conversation_count: int = 0
    tone_formality_std_across_conversations: float = 0.0
    verbosity_std_across_conversations: float = 0.0
    tone_drift_early_vs_late: float = 0.0  # avg tone change within conversations
    verbosity_drift_early_vs_late: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ConsistencyDerivedScores(BaseModel):
    """Interpreted consistency labels."""
    overall_consistency_score: float = 0.0  # 0=wildly inconsistent, 1=perfectly consistent
    tone_consistency: float = 0.0
    verbosity_consistency: float = 0.0
    pattern_consistency: float = 0.0
    # Per-persona-type behavior differences
    persona_type_avg_verbosity: dict[str, float] = Field(default_factory=dict)  # {persona_type: avg_word_count}
    # Drift
    within_conversation_drift: DriftDirection = DriftDirection.STABLE

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["within_conversation_drift"] = self.within_conversation_drift.value
        return d


class ConsistencyProfile(BaseModel):
    """Complete consistency analysis — meta-analyzer output."""
    raw: ConsistencyRawMetrics = Field(default_factory=ConsistencyRawMetrics)
    derived: ConsistencyDerivedScores = Field(default_factory=ConsistencyDerivedScores)
    reliability: ReliabilityInfo = Field(default_factory=ReliabilityInfo)
    evidence: list[dict[str, str]] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw.to_dict(),
            "derived": self.derived.to_dict(),
            "reliability": self.reliability.to_dict(),
            "evidence": self.evidence,
        }


# ── Anomaly ──────────────────────────────────────────────────


class AnomalyFlag(BaseModel):
    """A single anomalous response flagged by the engine."""
    anomaly_type: AnomalyType
    conversation_id: str
    turn_index: int
    description: str
    metric_value: float = 0.0
    baseline_value: float = 0.0  # what the metric normally is
    text_snippet: str = ""  # evidence
    detection_method: str = "z_score"  # z_score | percentile | rule_based

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["anomaly_type"] = self.anomaly_type.value
        return d


# ── Bot Signature (Run-Level) ────────────────────────────────


class BotSignature(BaseModel):
    """
    The complete behavioral fingerprint for one simulation run.

    This is the top-level output — everything rolls up here.
    """
    # Metadata
    run_id: str = ""
    timestamp: str = ""
    conversation_count: int = 0
    turn_count: int = 0

    # Profiles from each analyzer
    tone: ToneProfile = Field(default_factory=ToneProfile)
    verbosity: VerbosityProfile = Field(default_factory=VerbosityProfile)
    patterns: PatternProfile = Field(default_factory=PatternProfile)
    consistency: ConsistencyProfile = Field(default_factory=ConsistencyProfile)

    # Anomalies
    anomalies: list[AnomalyFlag] = Field(default_factory=list)
    anomaly_count: int = 0

    # Summary (template-generated, deterministic)
    summary_text: str = ""

    # Overall reliability
    reliability: ReliabilityInfo = Field(default_factory=ReliabilityInfo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "conversation_count": self.conversation_count,
            "turn_count": self.turn_count,
            "tone": self.tone.to_dict(),
            "verbosity": self.verbosity.to_dict(),
            "patterns": self.patterns.to_dict(),
            "consistency": self.consistency.to_dict(),
            "anomalies": [a.to_dict() for a in self.anomalies],
            "anomaly_count": self.anomaly_count,
            "summary_text": self.summary_text,
            "reliability": self.reliability.to_dict(),
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """Compact summary for appending to summary.json."""
        return {
            "conversation_count": self.conversation_count,
            "turn_count": self.turn_count,
            "anomaly_count": self.anomaly_count,
            "summary": self.summary_text,
            "tone_formality": self.tone.derived.formality_level.value,
            "verbosity_level": self.verbosity.derived.verbosity_level.value,
            "consistency_score": self.consistency.derived.overall_consistency_score,
            "repetition_score": self.patterns.derived.repetition_score,
            "reliability": self.reliability.confidence.value,
        }


# ── Signature Comparison ─────────────────────────────────────


class SignatureDrift(BaseModel):
    """A single behavioral drift between two runs."""
    dimension: str  # e.g. "verbosity", "formality", "repetition"
    metric: str
    baseline_value: float
    current_value: float
    change_percent: float
    is_significant: bool = False  # exceeds threshold

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class SignatureComparison(BaseModel):
    """Result of comparing two BotSignatures."""
    baseline_run_id: str = ""
    current_run_id: str = ""
    drifts: list[SignatureDrift] = Field(default_factory=list)
    significant_drift_count: int = 0
    summary_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_run_id": self.baseline_run_id,
            "current_run_id": self.current_run_id,
            "drifts": [d.to_dict() for d in self.drifts],
            "significant_drift_count": self.significant_drift_count,
            "summary_text": self.summary_text,
        }
