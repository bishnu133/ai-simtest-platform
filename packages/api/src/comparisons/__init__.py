"""Comparisons package — first-class comparison resources."""
from src.comparisons.idempotency import InMemoryIdempotencyStore, canonical_hash
from src.comparisons.models import (
    ComparisonListResponse,
    ComparisonRecord,
    ComparisonStatus,
    CreateComparisonRequest,
    MetricDelta,
    RegressionSignal,
    RunProvenance,
)
from src.comparisons.provider import (
    ComparisonProvider,
    FailClosedComparisonProvider,
    ProviderUnavailable,
    get_comparison_provider,
)
from src.comparisons.repository import (
    ComparisonNotFound,
    ComparisonRepository,
    InMemoryComparisonRepository,
)
from src.comparisons.service import (
    ComparisonIneligible,
    ComparisonService,
    IdempotencyConflict,
    IdempotentReplay,
)

__all__ = [
    "ComparisonRecord",
    "ComparisonStatus",
    "CreateComparisonRequest",
    "ComparisonListResponse",
    "RegressionSignal",
    "MetricDelta",
    "RunProvenance",
    "ComparisonRepository",
    "InMemoryComparisonRepository",
    "ComparisonNotFound",
    "ComparisonProvider",
    "FailClosedComparisonProvider",
    "ProviderUnavailable",
    "get_comparison_provider",
    "InMemoryIdempotencyStore",
    "canonical_hash",
    "ComparisonService",
    "ComparisonIneligible",
    "IdempotencyConflict",
    "IdempotentReplay",
]
