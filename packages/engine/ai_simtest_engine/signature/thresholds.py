"""
P3 #19 — Behavioral Signature Analysis: Thresholds & Confidence

Configurable minimum sample sizes and confidence computation.
Below threshold → partial signature with unavailable metrics and explanation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import Confidence, ReliabilityInfo


class SignatureThresholds(BaseModel):
    """Minimum sample sizes for each analyzer to produce reliable results."""

    # Tone analyzer
    min_responses_for_tone: int = Field(default=5, description="Min bot responses for tone analysis")

    # Verbosity analyzer
    min_responses_for_verbosity: int = Field(default=5, description="Min bot responses for verbosity stats")
    min_responses_for_drift: int = Field(default=10, description="Min responses for early/late drift analysis")

    # Pattern analyzer
    min_responses_for_patterns: int = Field(default=8, description="Min responses for pattern extraction")
    min_responses_for_ngrams: int = Field(default=10, description="Min responses for n-gram significance")

    # Consistency (meta-analyzer)
    min_conversations_for_consistency: int = Field(default=3, description="Min conversations for cross-conversation consistency")
    min_persona_groups_for_comparison: int = Field(default=2, description="Min persona type groups for persona-type comparison")

    # Anomaly detection
    min_responses_for_anomaly: int = Field(default=10, description="Min responses before flagging anomalies")


DEFAULT_THRESHOLDS = SignatureThresholds()


def compute_reliability(
    sample_count: int,
    threshold: int,
    extra_notes: list[str] | None = None,
) -> ReliabilityInfo:
    """
    Compute reliability info based on sample size vs threshold.

    Returns HIGH if sample >= 2× threshold, MEDIUM if >= threshold, LOW if below.
    """
    notes = list(extra_notes or [])

    if sample_count < threshold:
        confidence = Confidence.LOW
        notes.append(f"Only {sample_count} samples (minimum {threshold} recommended)")
    elif sample_count < threshold * 2:
        confidence = Confidence.MEDIUM
        notes.append(f"{sample_count} samples — moderate confidence")
    else:
        confidence = Confidence.HIGH

    return ReliabilityInfo(
        confidence=confidence,
        sample_count=sample_count,
        notes=notes,
    )
