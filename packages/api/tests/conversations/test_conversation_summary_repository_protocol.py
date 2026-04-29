"""Conversation summary repository Protocol-conformance tests (Turn 2.6 Step 4).

Purpose
-------
v0.2.1 §4 Step 4: a single test proving ``InMemoryConversationSummaryRepository``
structurally satisfies the ``ConversationSummaryRepository`` Protocol via
``runtime_checkable`` ``isinstance``. Step 5 will add the parallel test for
``PostgresConversationSummaryRepository``.

This test is non-DB. It exercises typing surface only.
"""
from __future__ import annotations

from src.conversations.repository import (
    ConversationSummaryRepository,
    InMemoryConversationSummaryRepository,
)


def test_in_memory_conversation_summary_repository_satisfies_protocol() -> None:
    """``InMemoryConversationSummaryRepository`` must structurally satisfy
    the ``ConversationSummaryRepository`` Protocol (runtime_checkable).

    If this fails, the Protocol surface and the InMemory impl have
    diverged — typically because a Protocol method was added without
    a corresponding impl, or a kwarg shape changed.
    """
    repo = InMemoryConversationSummaryRepository()
    assert isinstance(repo, ConversationSummaryRepository), (
        "InMemoryConversationSummaryRepository must structurally satisfy "
        "ConversationSummaryRepository Protocol (runtime_checkable). The "
        "Protocol intentionally does NOT include _reset() - that helper "
        "is InMemory-only and accessed via getattr by ConversationService "
        "(Turn 2.6 plan v0.2.1 §6.3 / R-7)."
    )

    # The test-only _reset helper exists on InMemory but is NOT part of
    # the Protocol surface (R-7 design).
    assert hasattr(repo, "_reset"), (
        "InMemory impl must expose _reset() as a test-only helper."
    )
