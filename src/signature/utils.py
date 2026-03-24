"""
P3 #19 — Behavioral Signature Analysis: Shared Utilities

Small helpers used across extractors and analyzers.
"""

from __future__ import annotations

import statistics


def safe_mean(values: list[float | int]) -> float:
    """Mean of a list, returns 0.0 for empty."""
    return statistics.mean(values) if values else 0.0


def safe_median(values: list[float | int]) -> float:
    """Median of a list, returns 0.0 for empty."""
    return statistics.median(values) if values else 0.0


def safe_stdev(values: list[float | int]) -> float:
    """Standard deviation, returns 0.0 for <2 elements."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def safe_percentile(values: list[float | int], p: float) -> float:
    """Percentile (0-100), returns 0.0 for empty."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    floor_k = int(k)
    ceil_k = min(floor_k + 1, len(sorted_vals) - 1)
    frac = k - floor_k
    return sorted_vals[floor_k] + frac * (sorted_vals[ceil_k] - sorted_vals[floor_k])


def truncate_text(text: str, max_len: int = 200) -> str:
    """Truncate text to max_len chars, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
