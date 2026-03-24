"""
P3 #19 — Deterministic Summary Generator

Template-based summary text from analyzer outputs.
No LLM calls — reproducible, auditable, cheap.
"""

from __future__ import annotations

from .models import (
    ToneProfile,
    VerbosityProfile,
    PatternProfile,
    ConsistencyProfile,
    AnomalyFlag,
    FormalityLevel,
    VerbosityLevel,
)


def generate_summary(
    tone: ToneProfile,
    verbosity: VerbosityProfile,
    patterns: PatternProfile,
    consistency: ConsistencyProfile,
    anomalies: list[AnomalyFlag],
) -> str:
    """
    Generate a 2-3 sentence summary of the bot's behavioral signature.

    Deterministic — same inputs always produce same output.
    """
    parts = []

    # Formality + tone
    formality_desc = {
        FormalityLevel.FORMAL: "formal",
        FormalityLevel.NEUTRAL: "moderately formal",
        FormalityLevel.INFORMAL: "informal",
    }.get(tone.derived.formality_level, "moderately formal")

    parts.append(f"The bot is {formality_desc}")

    # Empathy/apology characterization
    if tone.derived.empathy_frequency > 0.5:
        parts[-1] += " with frequent empathy expressions"
    elif tone.derived.apology_frequency > 0.3:
        parts[-1] += " with frequent apologies"

    # Verbosity
    verb_desc = {
        VerbosityLevel.LOW: "concise",
        VerbosityLevel.MEDIUM: "medium-length",
        VerbosityLevel.HIGH: "verbose",
    }.get(verbosity.derived.verbosity_level, "medium-length")

    parts[-1] += f", consistently {verb_desc} in responses"

    # Drift
    if verbosity.derived.verbosity_drift_magnitude > 0.15:
        direction = verbosity.derived.verbosity_drift_direction.value
        parts[-1] += f" (trending {direction} over conversation turns)"

    parts[-1] += "."

    # Consistency
    score = consistency.derived.overall_consistency_score
    if score > 0.7:
        parts.append("Behavioral consistency is high across conversations.")
    elif score > 0.4:
        parts.append("Behavioral consistency is moderate — some variation across conversations.")
    else:
        parts.append("Behavioral consistency is low — significant variation in style across conversations.")

    # Patterns
    pattern_notes = []
    if patterns.derived.repetition_score > 0.6:
        pattern_notes.append("highly repetitive phrasing")
    elif patterns.derived.repetition_score > 0.3:
        pattern_notes.append("moderately repetitive phrasing")

    if patterns.derived.question_asking_rate > 0.4:
        pattern_notes.append("frequent clarifying questions")

    if patterns.derived.list_usage_rate > 0.4:
        pattern_notes.append("heavy list usage")

    if pattern_notes:
        parts.append(f"Notable patterns include {', '.join(pattern_notes)}.")

    # Anomalies
    if anomalies:
        parts.append(f"{len(anomalies)} anomalous response(s) detected.")

    # Refusals
    if tone.derived.refusal_rate > 0.1:
        redirect_note = ""
        if tone.derived.refusal_redirect_rate > 0.5:
            redirect_note = " (most include safe redirects)"
        parts.append(
            f"Refusal rate is {tone.derived.refusal_rate:.0%} of responses{redirect_note}."
        )

    return " ".join(parts)
