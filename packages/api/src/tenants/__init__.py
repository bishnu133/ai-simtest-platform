"""Tenant lookup and creation — Turn 2 Step 2 (Category B, tenant-scoped).

Consumers import the Protocol and the record type from this module:

    from src.tenants import TenantRepository, TenantRecord
"""
from __future__ import annotations

from src.db.domain import TenantRecord
from src.tenants.repository import (
    InMemoryTenantRepository,
    PostgresTenantRepository,
    TenantRepository,
)

__all__ = [
    "TenantRecord",
    "TenantRepository",
    "InMemoryTenantRepository",
    "PostgresTenantRepository",
]
