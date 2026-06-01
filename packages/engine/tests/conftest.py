"""
Shared test fixtures and configuration.
"""

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: marks integration tests (may need mock bot)")
    config.addinivalue_line("markers", "slow: marks slow tests")
    config.addinivalue_line("markers", "api: marks API endpoint tests")


@pytest.fixture(autouse=True)
def reset_factory():
    """Reset LLM client factory cache between tests."""
    from src.core.llm_client import LLMClientFactory
    LLMClientFactory.clear_cache()
    yield
    LLMClientFactory.clear_cache()