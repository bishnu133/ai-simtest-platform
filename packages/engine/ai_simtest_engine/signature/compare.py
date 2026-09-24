"""
P3 #19 — Signature Comparison

Compare two BotSignatures to detect behavioral drift across runs.
Isolated from engine.py per review comment.
"""

from __future__ import annotations

from .models import (
    BotSignature,
    SignatureComparison,
    SignatureDrift,
)


# Default thresholds for "significant" drift
# Bounded metrics (0-1 range): use absolute delta thresholds
# Unbounded metrics (word counts etc): use relative percentage thresholds
_BOUNDED_METRICS = {
    "formality", "empathy_frequency", "hedge_frequency", "apology_frequency",
    "refusal_rate", "repetition_score", "consistency_score",
    "question_asking_rate", "list_usage_rate",
}

DEFAULT_DRIFT_THRESHOLDS = {
    # Bounded [0-1]: absolute delta thresholds
    "formality": 0.10,
    "empathy_frequency": 0.15,
    "hedge_frequency": 0.15,
    "apology_frequency": 0.15,
    "refusal_rate": 0.08,
    "repetition_score": 0.15,
    "consistency_score": 0.10,
    "question_asking_rate": 0.10,
    "list_usage_rate": 0.10,
    # Unbounded: relative percentage thresholds
    "verbosity_mean_words": 0.25,
    "verbosity_median_words": 0.25,
}


def compare_signatures(
    baseline: BotSignature,
    current: BotSignature,
    thresholds: dict[str, float] | None = None,
) -> SignatureComparison:
    """
    Compare two BotSignatures and identify behavioral drifts.

    Returns a SignatureComparison with all drifts and a summary.
    """
    thresholds = thresholds or DEFAULT_DRIFT_THRESHOLDS
    drifts: list[SignatureDrift] = []

    # Define comparison pairs: (dimension, metric, baseline_val, current_val)
    pairs = [
        ("tone", "formality",
         baseline.tone.raw.avg_formality_score,
         current.tone.raw.avg_formality_score),
        ("tone", "empathy_frequency",
         baseline.tone.derived.empathy_frequency,
         current.tone.derived.empathy_frequency),
        ("tone", "hedge_frequency",
         baseline.tone.derived.hedge_frequency,
         current.tone.derived.hedge_frequency),
        ("tone", "apology_frequency",
         baseline.tone.derived.apology_frequency,
         current.tone.derived.apology_frequency),
        ("tone", "refusal_rate",
         baseline.tone.derived.refusal_rate,
         current.tone.derived.refusal_rate),
        ("verbosity", "verbosity_mean_words",
         baseline.verbosity.raw.mean_words,
         current.verbosity.raw.mean_words),
        ("verbosity", "verbosity_median_words",
         baseline.verbosity.raw.median_words,
         current.verbosity.raw.median_words),
        ("patterns", "repetition_score",
         baseline.patterns.derived.repetition_score,
         current.patterns.derived.repetition_score),
        ("patterns", "question_asking_rate",
         baseline.patterns.derived.question_asking_rate,
         current.patterns.derived.question_asking_rate),
        ("patterns", "list_usage_rate",
         baseline.patterns.derived.list_usage_rate,
         current.patterns.derived.list_usage_rate),
        ("consistency", "consistency_score",
         baseline.consistency.derived.overall_consistency_score,
         current.consistency.derived.overall_consistency_score),
    ]

    for dimension, metric, b_val, c_val in pairs:
        if b_val == 0.0 and c_val == 0.0:
            continue

        threshold = thresholds.get(metric, 0.15)

        if metric in _BOUNDED_METRICS:
            # Bounded [0-1]: use absolute delta — avoids exaggerating small baseline changes
            abs_delta = abs(c_val - b_val)
            change_pct = ((c_val - b_val) / abs(b_val) * 100) if b_val != 0 else (100.0 if c_val != 0 else 0.0)
            is_significant = abs_delta > threshold
        else:
            # Unbounded (word counts): use relative percentage change
            if b_val != 0:
                change_pct = (c_val - b_val) / abs(b_val) * 100
            elif c_val != 0:
                change_pct = 100.0
            else:
                change_pct = 0.0
            is_significant = abs(change_pct / 100.0) > threshold

        drifts.append(SignatureDrift(
            dimension=dimension,
            metric=metric,
            baseline_value=b_val,
            current_value=c_val,
            change_percent=change_pct,
            is_significant=is_significant,
        ))

    significant_count = sum(1 for d in drifts if d.is_significant)

    # Generate summary
    summary = _generate_comparison_summary(drifts, significant_count)

    return SignatureComparison(
        baseline_run_id=baseline.run_id,
        current_run_id=current.run_id,
        drifts=drifts,
        significant_drift_count=significant_count,
        summary_text=summary,
    )


def _generate_comparison_summary(
    drifts: list[SignatureDrift],
    significant_count: int,
) -> str:
    """Generate a short comparison summary."""
    if significant_count == 0:
        return "No significant behavioral drift detected between runs."

    sig_drifts = [d for d in drifts if d.is_significant]
    parts = [f"{significant_count} significant behavioral drift(s) detected:"]

    for d in sig_drifts[:5]:
        direction = "increased" if d.change_percent > 0 else "decreased"
        parts.append(
            f"  {d.dimension}/{d.metric} {direction} by {abs(d.change_percent):.0f}%"
        )

    return " ".join(parts)
