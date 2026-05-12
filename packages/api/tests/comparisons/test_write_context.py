"""WriteContext factory unit tests (Turn 2.7 Drift 4, plan v0.2.1 §2.5).

ONE small test that verifies ``WriteContext.system()`` produces a
context whose actor IS the canonical system actor.

Why this test exists separately from the mapper tests:

The mapper's None-fallback path uses ``WriteContext.system()`` to fill
in the actor when callers don't pass a write_ctx. If
``WriteContext.system()`` ever returns a non-system actor (e.g. someone
"fixes" it to ``ActorRef(actor_id="anonymous", actor_type="system")``),
every persisted row from a non-threaded caller would silently carry
the wrong actor_id — and the mapper tests (which test the *threaded*
case) would not catch it.

This test pins the factory's contract so the mapper's fallback can
trust it.

Plan reference: turn_2_7_plan_v0_2_1.md §2.5
"""
from __future__ import annotations

from src.common.models import ActorRef
from src.common.write_context import WriteContext


def test_write_context_system_uses_system_actor() -> None:
    """``WriteContext.system()`` must return a context whose actor is
    the canonical system actor.

    The 'canonical system actor' is what ``ActorRef.system()`` returns
    today: ``actor_id="system"``, ``actor_type="system"``,
    ``display_name="AI SimTest System"``. If those values change in
    ActorRef, both this test and ``WriteContext.system()`` should be
    reviewed together.
    """
    ctx = WriteContext.system()

    # Three independent assertions on the resulting actor — together
    # they pin the canonical system identity. If any fails, either
    # ActorRef.system() drifted or WriteContext.system() stopped
    # delegating to it.
    assert ctx.actor.actor_id == "system", (
        f"WriteContext.system().actor.actor_id should be 'system', "
        f"got {ctx.actor.actor_id!r}"
    )
    assert ctx.actor.actor_type == "system", (
        f"WriteContext.system().actor.actor_type should be 'system', "
        f"got {ctx.actor.actor_type!r}"
    )
    assert ctx.actor.display_name == "AI SimTest System", (
        f"WriteContext.system().actor.display_name should match the "
        f"canonical system actor display_name, got "
        f"{ctx.actor.display_name!r}"
    )

    # Cross-check: WriteContext.system() and ActorRef.system() agree.
    # If they ever diverge, the mapper's None-fallback (which calls
    # WriteContext.system()) would persist a different actor than a
    # caller that explicitly built ActorRef.system().
    assert ctx.actor == ActorRef.system(), (
        "WriteContext.system().actor must equal ActorRef.system() — "
        "the system identity is canonical and these two factories must "
        "agree."
    )
