"""Run domain ↔ ORM mapper with 5→11 RunStatus forward mapping (plan §2.7).

Plan §2.7: "Persisted `runs.status` uses v2 §6.1 11-state enum; in-code
Pydantic `RunStatus` stays at 5 states; mapper translates. Forward
direction is identity, reverse direction raises `RunStatusNotYetSupported`
for the 6 future states."

  In-code (5):   queued, running, completed, failed, cancelled
  Persisted (11): queued, provisioning, running, finalizing,
                  completed, failed, cancelled,
                  waiting_input, paused, expired, unknown

Forward (domain → persisted): identity for the 5 in-code values.

Reverse (persisted → domain): 5 values are identity; the 6 future values
(provisioning, finalizing, waiting_input, paused, expired, unknown) raise
`RunStatusNotYetSupported` with a clear message naming the unsupported
state. This is intentional: if a future writer persists one of those
states, the reader discovers it immediately instead of silently
downgrading to a wrong terminal state.
"""
from __future__ import annotations

from src.db.models import Run
from src.runs.models import RunRecord, RunStatus


class RunStatusNotYetSupported(Exception):
    """Raised when a persisted run status is a future 11-state enum value
    that the 5-state in-code RunStatus does not yet recognize.

    Callers should treat this as a 5xx class error — it means the
    database holds data that this application version cannot read.
    The fix is either to upgrade the reader to the expanded enum or
    to migrate the row to a supported state.
    """

    def __init__(self, persisted_status: str) -> None:
        super().__init__(
            f"Persisted run status '{persisted_status}' is part of the v2 §6.1 "
            f"11-state enum but not yet supported by the in-code 5-state "
            f"RunStatus. Upgrade the reader or migrate the row."
        )
        self.persisted_status = persisted_status


# Explicit map: the 5 in-code states that are also persisted values.
_IN_CODE_STATES: frozenset[str] = frozenset(
    {"queued", "running", "completed", "failed", "cancelled"}
)

# The full 11-state set, so we can distinguish "future state" from
# "garbage string" — the former raises RunStatusNotYetSupported, the
# latter raises ValueError.
_ALL_PERSISTED_STATES: frozenset[str] = frozenset(
    {
        "queued",
        "provisioning",
        "running",
        "finalizing",
        "completed",
        "failed",
        "cancelled",
        "waiting_input",
        "paused",
        "expired",
        "unknown",
    }
)


def run_status_to_persisted(status: RunStatus) -> str:
    """Forward map: in-code RunStatus → persisted status string.

    Identity for all 5 values. This function exists as a named
    primitive so the reverse mapper's counterpart is obvious.
    """
    return status.value


def run_status_from_persisted(persisted: str) -> RunStatus:
    """Reverse map: persisted status string → in-code RunStatus.

    Raises:
        RunStatusNotYetSupported: if `persisted` is one of the 6 future
            11-state enum values.
        ValueError: if `persisted` is not any recognized state.
    """
    if persisted in _IN_CODE_STATES:
        return RunStatus(persisted)
    if persisted in _ALL_PERSISTED_STATES:
        raise RunStatusNotYetSupported(persisted)
    raise ValueError(
        f"Unknown persisted run status '{persisted}' — not in the v2 §6.1 "
        f"11-state enum. Row is corrupt or from a schema newer than the code."
    )


# ---------------------------------------------------------------------------
# Record mappers
# ---------------------------------------------------------------------------


def run_to_domain(row: Run) -> RunRecord:
    """ORM → domain. Note: ORM uses `id`, domain uses `run_id`."""
    return RunRecord(
        run_id=str(row.id),
        tenant_id=str(row.tenant_id),
        workspace_id=str(row.workspace_id),
        status=run_status_from_persisted(row.status),
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        engine_version=row.engine_version,
        metadata=dict(row.run_metadata or {}),
    )


def run_to_orm(record: RunRecord) -> Run:
    """Domain → ORM. Note: domain uses `run_id`, ORM uses `id`.

    `initiated_by_actor_id`, `asset_versions`, and `judge_scores` are
    ORM-only columns (not in the Week 6a `RunRecord` shape). We set
    defaults here so the ORM row is valid; Turn 2 repository writes
    will populate them from context carried separately.
    """
    return Run(
        id=record.run_id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        status=run_status_to_persisted(record.status),
        # Defaults for columns not in the Week 6a RunRecord domain model.
        # Turn 2 write paths will override these with real values sourced
        # from TenantContext + asset resolution.
        initiated_by_actor_id=record.metadata.get(
            "initiated_by_actor_id", "system"
        ),
        engine_version=record.engine_version,
        asset_versions=record.metadata.get("asset_versions", {}),
        judge_scores=record.metadata.get("judge_scores", {}),
        run_metadata=dict(record.metadata or {}),
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )
