"""Comparison service Protocol-typing parity tests (Turn 2.6 Step 1 / Step 3).

Purpose
-------
v0.2.1 §4 Step 1 (R-3 corrected): two tests that prove ``ComparisonService``
is typed against the ``ComparisonRepository`` Protocol, not the concrete
``InMemoryComparisonRepository``. Step 3 appends the parallel idempotency
Protocol-typing test once ``IdempotencyStore`` Protocol exists.

These tests are non-DB. They exercise typing surface only.
"""
from __future__ import annotations

import typing

import pytest

from src.comparisons.repository import (
    ComparisonRepository,
    InMemoryComparisonRepository,
)
from src.comparisons.service import ComparisonService


# ---------------------------------------------------------------------------
# Step 1 — repo typed against ComparisonRepository Protocol
# ---------------------------------------------------------------------------


def test_comparison_service_typed_against_comparison_repository_protocol() -> None:
    """``ComparisonService.__init__``'s ``repo`` annotation must be the
    ``ComparisonRepository`` Protocol, not the concrete
    ``InMemoryComparisonRepository`` class.

    Turn 2.6 plan v0.2.1 MF-1: services must depend on Protocols, mirroring
    the Turn 2.5 fix for ``RunService``. Note ``service.py`` uses
    ``from __future__ import annotations`` (PEP 563), so annotations are
    stored as strings; resolve via ``typing.get_type_hints``.
    """
    hints = typing.get_type_hints(ComparisonService.__init__)
    repo_annotation = hints["repo"]

    assert repo_annotation is ComparisonRepository, (
        f"ComparisonService.__init__'s `repo` parameter must be annotated as "
        f"ComparisonRepository (Protocol), got {repo_annotation!r}. "
        f"This is the same MF-1 discipline Turn 2.5 fixed for runs."
    )

    # Negative: must NOT be the concrete class
    assert repo_annotation is not InMemoryComparisonRepository, (
        "ComparisonService.__init__'s `repo` parameter is annotated against "
        "the concrete InMemoryComparisonRepository - Turn 2.6 v0.2.1 MF-1 "
        "requires Protocol typing instead."
    )


def test_comparison_service_constructor_accepts_inmemory_repo_via_protocol() -> None:
    """Constructing ``ComparisonService`` with an ``InMemoryComparisonRepository``
    instance must work and the stored repo must structurally satisfy the
    ``ComparisonRepository`` Protocol.

    Proves the Protocol typing is not just decorative - it's the contract
    every concrete impl must continue to satisfy.
    """
    repo = InMemoryComparisonRepository()

    # runtime_checkable isinstance check - proves InMemory still satisfies
    # the Protocol after Turn 2.6 typing changes.
    assert isinstance(repo, ComparisonRepository), (
        "InMemoryComparisonRepository must structurally satisfy "
        "ComparisonRepository Protocol (runtime_checkable). If this fails, "
        "the Protocol surface and the InMemory impl have diverged."
    )

    # Also accessible via ComparisonService - supply minimum-viable run_service
    # and provider stubs to construct.
    from src.runs.repository import InMemoryRunRepository
    from src.runs.service import RunService

    class _StubProvider:
        async def compute(self, record):
            return ([], None)

    svc = ComparisonService(
        repo=repo,
        run_service=RunService(InMemoryRunRepository()),
        provider=_StubProvider(),
    )
    # Internal state - verify the repo we passed is what the service holds
    assert svc._repo is repo


# ---------------------------------------------------------------------------
# Step 3 carry — idempotency typed against IdempotencyStore Protocol
# ---------------------------------------------------------------------------


def test_comparison_service_typed_against_idempotency_store_protocol() -> None:
    """``ComparisonService.__init__``'s ``idempotency`` annotation must be
    the ``IdempotencyStore`` Protocol (resolved as ``IdempotencyStore | None``),
    not the concrete ``InMemoryIdempotencyStore`` class.

    Turn 2.6 plan v0.2.1 §4 Step 3: deferred from Step 1 because the
    Protocol did not exist until Step 3. Same MF-1 discipline as the
    repo annotation test above.
    """
    from src.comparisons.idempotency import (
        IdempotencyStore,
        InMemoryIdempotencyStore as _InMemImpl,
    )

    hints = typing.get_type_hints(ComparisonService.__init__)
    idem_annotation = hints["idempotency"]
    # The annotation is `IdempotencyStore | None`, which is `Optional[...]`
    # under typing.get_type_hints. Both forms are acceptable; check the
    # underlying union members.
    args = typing.get_args(idem_annotation)
    assert IdempotencyStore in args, (
        f"ComparisonService.__init__'s `idempotency` parameter must be "
        f"annotated as IdempotencyStore | None, got annotation={idem_annotation!r}, "
        f"union args={args!r}. Turn 2.6 v0.2.1 MF-1 requires Protocol typing."
    )
    assert _InMemImpl not in args, (
        "ComparisonService.__init__'s `idempotency` parameter is annotated "
        "against the concrete InMemoryIdempotencyStore - Turn 2.6 v0.2.1 "
        "MF-1 requires Protocol typing instead."
    )

    # Negative: also confirm InMemoryIdempotencyStore satisfies the
    # Protocol (runtime_checkable) so existing call sites stay valid.
    assert isinstance(_InMemImpl(), IdempotencyStore)
