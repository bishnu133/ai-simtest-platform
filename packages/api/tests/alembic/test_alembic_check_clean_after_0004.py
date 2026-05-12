"""Migration 0004 — `alembic check` cleanliness test (Drift 1).

ONE test that runs `alembic check` against the migrated DB and asserts
the autogenerate diff is empty. After 0004 (which only touches role
membership, not schema), the live DB schema must continue to match
SQLAlchemy's ``Base.metadata`` exactly.

Why this test matters:
    Migration 0004 grants role membership and does not change any
    table/column/index. So `alembic check` MUST report no schema
    differences. If it ever reports a difference, that means either:

      * Someone snuck a schema change into 0004 (e.g., added a column
        ALTER) without updating the SQLAlchemy models.
      * 0001/0002/0003 drifted from the models in a way that 0004's
        application revealed (e.g., a server_default got out of sync).
      * A future migration was added but `compare_server_default` /
        `compare_type` flagged a model-vs-DB drift.

    All three are bugs that need attention. This test is the canary.

`alembic check` exits 0 if no diff, non-zero if a diff exists.

Plan reference:
    Bishnu's v0.2 review strongly-recommended #9 — `alembic check` test
    should run after a role-only migration to prove autogenerate is
    still clean from a model/schema point of view.
    turn_2_7_plan_v0_2_1.md §2.5 (the +1 that lifted target to 391/1
    in v0.2.1 baseline; v0.2.1 final target 392/1 includes this test).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


async def test_alembic_check_is_clean_after_0004(clean_db: str) -> None:
    """`alembic check` must exit 0 against the fully-migrated DB.

    Uses the existing `clean_db` fixture which:
      1. Runs `alembic upgrade head` in a subprocess (session-scoped).
      2. TRUNCATEs all data tables before yielding (per-test).

    By the time we run `alembic check`, the DB has the full chain
    applied through 0004 — so the check is against the latest head.

    Failure modes this catches:
      * 0004 accidentally adds a schema change → models don't match.
      * 0001/0002/0003 reference a server_default the model doesn't
        declare, and a recent SQLAlchemy version started flagging it.
      * Someone bumps 0004's revision id or branch_labels in a way
        that confuses alembic's autogen.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(PROJECT_ROOT),
        env={
            **os.environ,
            "DATABASE_URL": clean_db,
            "AI_SIMTEST_DATABASE_URL": clean_db,
            "PYTHONPATH": str(PROJECT_ROOT),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"`alembic check` reported a model/schema drift after migration\n"
        f"chain through 0004. exit={result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n\n"
        f"Investigate: either a migration drifted from the SQLAlchemy\n"
        f"models, or a model declaration drifted from the migrations.\n"
        f"Both are bugs."
    )
