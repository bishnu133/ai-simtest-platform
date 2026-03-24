"""
P3 #19 — Behavioral Signature Analysis

Analyzes bot communication style: tone, verbosity, patterns, and consistency.
Produces a behavioral fingerprint that can be compared across runs.

Usage:
    from src.signature import SignatureEngine
    engine = SignatureEngine()
    signature = engine.analyze(judged_conversations=report.judged_conversations)
"""

from .engine import SignatureEngine
from .models import (
    BotSignature,
    SignatureComparison,
    SignatureDrift,
    AnomalyFlag,
    AnomalyType,
    TurnSignals,
    ToneProfile,
    VerbosityProfile,
    PatternProfile,
    ConsistencyProfile,
    ReliabilityInfo,
    Confidence,
)
from .compare import compare_signatures
from .signature_html import inject_signature_into_report
from .thresholds import SignatureThresholds, DEFAULT_THRESHOLDS

__all__ = [
    "SignatureEngine",
    "BotSignature",
    "SignatureComparison",
    "SignatureDrift",
    "AnomalyFlag",
    "AnomalyType",
    "TurnSignals",
    "ToneProfile",
    "VerbosityProfile",
    "PatternProfile",
    "ConsistencyProfile",
    "ReliabilityInfo",
    "Confidence",
    "compare_signatures",
    "inject_signature_into_report",
    "SignatureThresholds",
    "DEFAULT_THRESHOLDS",
]
