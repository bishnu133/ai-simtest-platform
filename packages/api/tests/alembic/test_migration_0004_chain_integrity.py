"""Migration 0004 chain integrity tests (Turn 2.7 Drift 1, plan v0.2.1 §4.1).

Three pure-metadata tests that do NOT require a live Postgres:
  1. Migration 0004 is the chain head (alembic heads → 0004 only).
  2. Migration 0004's down_revision is 0003.
  3. Migration 0004's upgrade body contains the literal
     `GRANT app_user TO CURRENT_USER` statement (regression guard
     against a future "fix" that switches to a dynamic form without
     thinking — see plan v0.2 §2.2 for the documented decision).

These are subprocess + AST tests, not DB tests. They run without the
Postgres fixture and catch the most common breakage modes:

  * Someone adds a 0005 migration but forgets the chain link
    (test 1 catches: heads != exactly {0004}).
  * Someone re-parents 0004 to a different down_revision
    (test 2 catches the parent change).
  * Someone "improves" the GRANT form to dynamic SQL or removes the
    GRANT entirely (test 3 catches the literal-string drift).

The DO-block runtime semantics — that CURRENT_USER inside the DO block
resolves to the migration connection role — is tested separately in
test_migration_0004_grant_succeeds.py with a real Postgres apply.

Plan reference: turn_2_7_plan_v0_2_1.md §2.1 + §4.1
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


# Anchors to the migration file. If the file is renamed, both must update.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "alembic" / "versions"
MIGRATION_0004_FILE = MIGRATIONS_DIR / "0004_grant_app_user_to_current_user.py"


# Frozen revision identifiers (must match the migration source). The
# revision id is shorter than the file name to honor the alembic_version
# varchar(32) limit — see migration 0004 docstring "Naming note".
EXPECTED_REVISION_ID = "0004_grant_app_user_membership"
EXPECTED_DOWN_REVISION = "0003_idem_keys_workspace_id"


# ---------------------------------------------------------------------------
# Test 1 — chain head
# ---------------------------------------------------------------------------


def test_migration_0004_is_chain_head() -> None:
    """`alembic heads` must return exactly the 0004 revision id and
    nothing else.

    Failure modes this catches:
      * A 0005 migration is added but its down_revision points to 0003
        instead of 0004 → multiple heads → ambiguous upgrade target.
      * A typo in 0004's revision id leaves the chain broken at 0003.
      * A merge that introduces a branch creates two heads.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"`alembic heads` exited {result.returncode}:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # `alembic heads` outputs lines like:
    #     0004_grant_app_user_membership (head)
    # Possibly with deprecation warnings on stderr; parse stdout only.
    heads = [
        line.split()[0]
        for line in result.stdout.strip().splitlines()
        if line.strip() and not line.startswith("INFO")
    ]
    assert heads == [EXPECTED_REVISION_ID], (
        f"Expected exactly one head ({EXPECTED_REVISION_ID!r}), got {heads!r}.\n"
        f"Full stdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2 — down_revision points to 0003
# ---------------------------------------------------------------------------


def test_migration_0004_down_revision_is_0003() -> None:
    """The migration's `down_revision` module-level assignment must be
    the 0003 revision id, parsed via AST so we don't accidentally match
    docstring text.

    Failure mode this catches: someone re-parents 0004 to 0002 or
    leaves it at None (which would make 0004 a second base, breaking
    `alembic upgrade head`).
    """
    assert MIGRATION_0004_FILE.exists(), (
        f"Migration file not found: {MIGRATION_0004_FILE}"
    )
    source = MIGRATION_0004_FILE.read_text()
    tree = ast.parse(source)

    down_revision_value = None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "down_revision"
            and isinstance(node.value, ast.Constant)
        ):
            down_revision_value = node.value.value
            break

    assert down_revision_value == EXPECTED_DOWN_REVISION, (
        f"Migration 0004's down_revision must be {EXPECTED_DOWN_REVISION!r}, "
        f"got {down_revision_value!r}. The chain must be "
        f"<base> -> 0001 -> 0002 -> 0003 -> 0004 (Stage B sign-off precondition)."
    )


# ---------------------------------------------------------------------------
# Test 3 — upgrade body contains the literal GRANT statement
# ---------------------------------------------------------------------------


def test_migration_0004_upgrade_body_grants_app_user_to_current_user() -> None:
    """The upgrade body must contain the literal SQL string
    `GRANT app_user TO CURRENT_USER`.

    Plan v0.2 §2.2 ratified the static keyword form over the dynamic
    `EXECUTE format('GRANT app_user TO %I', current_user)` form because
    CURRENT_USER is an explicit `role_specification` keyword in the
    Postgres GRANT grammar. Switching back to dynamic SQL without a
    documented reason would be a regression — this test is the guard.

    Failure modes this catches:
      * Someone "improves" the form to `EXECUTE format(...)`.
      * Someone removes the GRANT entirely (nullifying the migration).
      * Someone capitalizes differently (`Grant app_user...`) — Postgres
        is case-insensitive but our literal-string assertion is not, so
        a stylistic change here is also caught and can be reviewed.
    """
    assert MIGRATION_0004_FILE.exists(), (
        f"Migration file not found: {MIGRATION_0004_FILE}"
    )
    source = MIGRATION_0004_FILE.read_text()
    tree = ast.parse(source)

    upgrade_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            upgrade_fn = node
            break
    assert upgrade_fn is not None, "upgrade() function missing from migration 0004"

    upgrade_source = ast.get_source_segment(source, upgrade_fn)
    assert upgrade_source is not None, "could not extract upgrade() source"

    assert "GRANT app_user TO CURRENT_USER" in upgrade_source, (
        "Migration 0004's upgrade() body must contain the literal SQL\n"
        "    GRANT app_user TO CURRENT_USER\n"
        "Plan v0.2 §2.2 ratified this static keyword form. If you need to\n"
        "switch to dynamic SQL, update plan + this test together."
    )

    # And confirm the discipline rule: no string-interpolation form
    # snuck in alongside.
    assert "format(" not in upgrade_source or "GRANT" not in upgrade_source.split("format(")[1].split(")")[0], (
        "Migration 0004 upgrade() appears to use `format(...)` for the "
        "GRANT — plan v0.2 §2.2 ratified the static keyword form. If you "
        "need dynamic SQL, update the plan first."
    )
