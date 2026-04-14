"""Comparison compute provider — fail-closed default (v1.2.2 §M3, §S2).

The default provider raises ProviderUnavailable on every call, which the
router maps to HTTP 503 `comparison_not_supported`. Test fakes are
activated ONLY via explicit FastAPI dependency override. There is NO
env-based silent fallback, NO import fallback, NO auto-degradation.
"""
from __future__ import annotations

from typing import Protocol

from src.api.errors import APIError
from src.comparisons.models import ComparisonRecord, RegressionSignal


class ProviderUnavailable(APIError):
    code = "comparison_not_supported"
    http_status = 503


class ComparisonProvider(Protocol):
    async def compute(
        self, record: ComparisonRecord
    ) -> tuple[list[RegressionSignal], str | None]:
        """Return (regression_signals, error). error is None on success."""
        ...


class FailClosedComparisonProvider:
    """Default provider. Always raises 503. Fail-closed by design."""

    async def compute(self, record: ComparisonRecord):
        raise ProviderUnavailable(
            "No comparison provider is configured for this deployment"
        )


def get_comparison_provider() -> ComparisonProvider:
    """FastAPI dependency. Routers depend on this. Tests override via app.dependency_overrides."""
    return FailClosedComparisonProvider()
