"""ComparisonService ID-format tests (Turn 2.7 Drift 2, plan v0.2.1 §4.2).

Three service-level tests that verify the T2.6-D2 fix in
``src/comparisons/service.py``:

  1. The minted id parses as a UUID version-4 string.
  2. 100 service calls yield 100 distinct ids (collision-free).
  3. The minted id does NOT carry the legacy ``cmp_`` prefix.

These are pure InMemory tests — no DB needed — because the bug is in
the service-layer ID-mint, not in persistence. The Postgres round-trip
is tested separately in
``tests/db/test_postgres_comparison_repository.py``.

Why three tests instead of one:
  Test 1 catches "the ID isn't a UUID" (the original T2.6-D2 surface).
  Test 2 catches "the ID is a UUID but it's the same UUID every time"
  (a regression mode that test 1 alone would miss).
  Test 3 catches "the ID is `cmp_<uuid4>`" — the most likely shape a
  half-revert would take, where someone restored the prefix while
  keeping uuid4 inside. This test fails fast on that shape.

Plan reference: turn_2_7_plan_v0_2_1.md §4.2
"""
from __future__ import annotations

import uuid

from src.common.models import ActorRef, TenantContext, utcnow
from src.comparisons import (
    ComparisonRecord,
    ComparisonService,
    InMemoryComparisonRepository,
    InMemoryIdempotencyStore,
)
from src.runs import InMemoryRunRepository, RunRecord, RunService, RunStatus


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor=ActorRef(actor_id="test-user", actor_type="human"),
    )


class _NullProvider:
    """Smoke provider that returns no signals and no error.

    Tests use this because the ID-mint happens before the provider is
    invoked, so the provider's behaviour does not matter — the ID we're
    asserting on is set in the PENDING-write path, which runs first.
    """

    async def compute(
        self, record: ComparisonRecord
    ):  # pragma: no cover - trivial
        return ([], None)


async def _seed_completed_run(run_repo: InMemoryRunRepository, run_id: str) -> None:
    """Seed a COMPLETED run that the service eligibility check will accept."""
    await run_repo.create(
        RunRecord(
            run_id=run_id,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            status=RunStatus.COMPLETED,
            engine_version="engine_v1",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
    )


async def _make_service() -> tuple[ComparisonService, str, str]:
    """Build a ComparisonService wired to InMemory deps + seed two
    eligible (COMPLETED) runs. Returns (service, left_run_id, right_run_id).
    """
    cmp_repo = InMemoryComparisonRepository()
    run_repo = InMemoryRunRepository()
    left_id = str(uuid.uuid4())
    right_id = str(uuid.uuid4())
    await _seed_completed_run(run_repo, left_id)
    await _seed_completed_run(run_repo, right_id)
    service = ComparisonService(
        repo=cmp_repo,
        run_service=RunService(run_repo),
        provider=_NullProvider(),
        idempotency=InMemoryIdempotencyStore(),
    )
    return service, left_id, right_id


# ---------------------------------------------------------------------------
# Test 1 — minted id parses as a UUID4 string
# ---------------------------------------------------------------------------


async def test_comparison_service_create_comparison_returns_uuid_id() -> None:
    """The id minted by ``ComparisonService.create_comparison`` must
    parse as a UUID4. This is the direct verification of the T2.6-D2
    source fix in ``service.py``.

    Failure modes this catches:
      * Someone reverts the fix to ``f"cmp_{uuid.uuid4().hex[:12]}"``
        — UUID parse fails immediately on the ``cmp_`` prefix.
      * Someone switches to ``uuid.uuid1()`` (which is still a UUID, but
        carries MAC-address provenance — not appropriate for public
        identifiers and would fail the version-check below).
      * Someone hard-codes a fixed string for testing and forgets to
        revert it — the parse succeeds only for actual UUIDs.
    """
    service, left, right = await _make_service()
    record = await service.create_comparison(_ctx(), left, right)

    # Step 1 — parses as UUID at all
    parsed = uuid.UUID(record.id)

    # Step 2 — is a version-4 (random) UUID specifically. uuid.uuid4()
    # always produces version=4; this asserts we did not regress to
    # uuid1/uuid3/uuid5.
    assert parsed.version == 4, (
        f"ComparisonService minted a UUID of version {parsed.version}, "
        f"expected 4 (uuid4). id={record.id!r}"
    )

    # Step 3 — round-trip-equal — the str(uuid4()) form preserves bytes.
    assert str(parsed) == record.id, (
        f"UUID round-trip mismatch: parsed={parsed!s} record.id={record.id!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — uniqueness across many calls (collision-free)
# ---------------------------------------------------------------------------


async def test_comparison_service_create_comparison_ids_are_unique() -> None:
    """100 service calls in sequence must yield 100 distinct ids.

    This is a smoke test for collision-free generation. With UUID4, the
    probability of a collision in 100 draws is ~10^-35 — so any
    duplicate within the test run signals a *deterministic* bug
    (e.g., someone hard-coded a constant "00000000-0000-0000-0000-000000000000",
    or a caching layer is reusing a record), not bad luck.
    """
    service, left, right = await _make_service()

    minted_ids: set[str] = set()
    N = 100
    for _ in range(N):
        record = await service.create_comparison(_ctx(), left, right)
        minted_ids.add(record.id)

    assert len(minted_ids) == N, (
        f"Expected {N} distinct ids across {N} create_comparison calls, "
        f"got {len(minted_ids)} unique values. Duplicates indicate "
        f"deterministic id generation (not a UUID4 birthday collision)."
    )


# ---------------------------------------------------------------------------
# Test 3 — explicit `cmp_` prefix regression guard
# ---------------------------------------------------------------------------


async def test_comparison_service_create_comparison_id_does_not_use_cmp_prefix() -> None:
    """Explicit guard: minted id must NOT start with ``cmp_``.

    Test 1 (UUID parse) would catch ``cmp_<plain hex>`` because it isn't
    a valid UUID. But it would NOT catch a "compromise" form like
    ``cmp_<uuid4>`` if someone tried to "preserve readability" by
    re-adding the prefix while keeping a real UUID. That shape would
    pass UUID parse on the suffix only, not the full string — and our
    test 1 calls ``uuid.UUID(record.id)`` on the full string, which
    would also fail. So this test is technically redundant with test 1.

    It exists anyway because a precise failure message is more useful
    than ``ValueError: badly formed hexadecimal UUID string``. When this
    test fails, the operator instantly knows what was reverted; with
    test 1 alone, they'd see a UUID parse error and have to inspect the
    string.

    Plan v0.2.1 §4.2 also requires this explicit guard.
    """
    service, left, right = await _make_service()
    record = await service.create_comparison(_ctx(), left, right)

    assert not record.id.startswith("cmp_"), (
        f"ComparisonService minted an id with the legacy `cmp_` prefix: "
        f"{record.id!r}. This is the T2.6-D2 regression: a previous "
        f"version of `service.py` minted `f\"cmp_{{uuid.uuid4().hex[:12]}}\"`, "
        f"which asyncpg rejected because the ORM column is UUID. Plan v0.2.1 "
        f"§4.2 requires the static `str(uuid.uuid4())` form."
    )
