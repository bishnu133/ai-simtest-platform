"""Domain ↔ ORM mapper layer (plan §2.6 identity contract, §2.7 RunStatus alignment).

Each submodule exposes two pure functions:

    to_domain(orm_obj) -> DomainObject
    to_orm(domain_obj) -> OrmObject

Mappers are deliberately small and pure: no DB access, no repository
dependencies, no logging side effects. They exist for two reasons:

  1. **Type discipline** — the ORM layer uses `uuid.UUID`, `datetime`,
     and string-literal columns; the domain layer uses `str` (UUID-as-
     string per plan §2.6), timezone-aware `datetime`, and typed enums.
     Mappers are the one place that translation happens.

  2. **Forward-compatible enum mapping** — plan §2.7 persists `runs.status`
     with the 11-state enum from v2 §6.1, but the in-code `RunStatus`
     enum only has 5 states. The run mapper encodes this one-way
     compatibility: 5 in-code states map to 5 of the 11 persisted
     states on the way to the DB (identity for those 5), and when
     reading from the DB, encountering any of the 6 future states
     raises `RunStatusNotYetSupported` with a clear message rather
     than a `ValueError` from the enum constructor.

Turn 1b delivers all 10 mappers. Repository implementations in Turn 2
consume them via the existing domain Protocol interfaces.
"""
from __future__ import annotations

from src.db.mappers.asset import asset_to_domain, asset_to_orm
from src.db.mappers.audit_event import audit_event_to_domain, audit_event_to_orm
from src.db.mappers.comparison import comparison_to_domain, comparison_to_orm
from src.db.mappers.conversation_summary import (
    conversation_summary_to_domain,
    conversation_summary_to_orm,
)
from src.db.mappers.dashboard_artifact import (
    dashboard_artifact_to_domain,
    dashboard_artifact_to_orm,
)
from src.db.mappers.idempotency_key import (
    idempotency_key_to_domain,
    idempotency_key_to_orm,
)
from src.db.mappers.membership import membership_to_domain, membership_to_orm
from src.db.mappers.run import (
    RunStatusNotYetSupported,
    run_status_from_persisted,
    run_status_to_persisted,
    run_to_domain,
    run_to_orm,
)
from src.db.mappers.tenant import tenant_to_domain, tenant_to_orm
from src.db.mappers.workspace import workspace_to_domain, workspace_to_orm

__all__ = [
    "RunStatusNotYetSupported",
    "asset_to_domain",
    "asset_to_orm",
    "audit_event_to_domain",
    "audit_event_to_orm",
    "comparison_to_domain",
    "comparison_to_orm",
    "conversation_summary_to_domain",
    "conversation_summary_to_orm",
    "dashboard_artifact_to_domain",
    "dashboard_artifact_to_orm",
    "idempotency_key_to_domain",
    "idempotency_key_to_orm",
    "membership_to_domain",
    "membership_to_orm",
    "run_status_from_persisted",
    "run_status_to_persisted",
    "run_to_domain",
    "run_to_orm",
    "tenant_to_domain",
    "tenant_to_orm",
    "workspace_to_domain",
    "workspace_to_orm",
]
